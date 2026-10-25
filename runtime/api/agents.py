"""Agent routes owned by wf/resolver. Contract: contracts/openapi.yaml.

    GET  /v0/agents/{name}/resolved   the resolved view (H1)
    POST /v0/agents/{name}/validate   validate a draft without saving it

`router` is mounted by the runtime's app. Until it is, serve these routes alone:

    python -m runtime.api.agents --port 8000

The acting principal comes ONLY from the X-ReAgent-User header (FROZEN D2).
Visibility is filtered here, server-side; the dashboard renders what it gets.
"""
from __future__ import annotations

import argparse
from typing import Any

import yaml
from fastapi import APIRouter, Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from runtime.resolver import load_catalog, resolve_agent
from runtime.resolver.source import Catalog
from runtime.resolver.validate import validate_draft_text

router = APIRouter(prefix="/v0/agents", tags=["agents"])


def _principal(cat: Catalog, user: str | None) -> tuple[str, bool, str | None]:
    if not user:
        raise HTTPException(403, "X-ReAgent-User header required")
    is_admin = user in cat.admins()
    team = cat.team_of(user)
    if not is_admin and team is None:
        raise HTTPException(403, f"{user} is not an enterprise admin or a member of any team")
    return user, is_admin, team


def _visible(doc: dict[str, Any], is_admin: bool, team: str | None) -> bool:
    """SPEC.md §2: members see their own team's agents and no enterprise configs."""
    if is_admin:
        return True
    md = doc["metadata"]
    return md.get("level") == "team" and md.get("team") == team


@router.get("/{name}/resolved")
def get_resolved(name: str, x_reagent_user: str | None = Header(default=None)):
    cat = load_catalog()                       # SPEC.md §1: re-read every layer, every time
    user, is_admin, team = _principal(cat, x_reagent_user)
    doc = cat.latest(name)
    if doc is None or not _visible(doc, is_admin, team):
        raise HTTPException(404, "not found")  # deliberately indistinguishable
    out = resolve_agent(name, caller=user, catalog=cat)
    if out["rejections"]:
        return JSONResponse(status_code=422, content={"rejections": out["rejections"]})
    return out


@router.post("/{name}/validate")
def post_validate(name: str, body: dict[str, Any] = Body(...),
                  x_reagent_user: str | None = Header(default=None)):
    cat = load_catalog()
    user, is_admin, team = _principal(cat, x_reagent_user)
    text = body.get("spec")
    if not isinstance(text, str):
        raise HTTPException(422, "body.spec must be the raw YAML of the .agent file")
    try:
        draft = yaml.safe_load(text)
    except yaml.YAMLError:
        draft = None
    if isinstance(draft, dict) and not is_admin:
        md = draft.get("metadata") or {}
        if md.get("level") == "enterprise" or (md.get("team") not in (None, team)):
            raise HTTPException(403, "members validate drafts in their own team only")
    return validate_draft_text(text, cat)


@router.post("/{name}/evals")
def post_evals(name: str, body: dict[str, Any] | None = Body(default=None),
               x_reagent_user: str | None = Header(default=None)):
    """Runs the suite in the mocked sandbox. The model follows REAGENT_MODEL
    (fake by default), so a dashboard click never spends credits by accident."""
    from runtime.evals import run_evals

    cat = load_catalog()
    user, is_admin, team = _principal(cat, x_reagent_user)
    doc = cat.latest(name)
    if doc is None or not _visible(doc, is_admin, team):
        raise HTTPException(404, "not found")
    body = body or {}
    runs = body.get("runs_per_case", 5)
    if not isinstance(runs, int) or runs < 1:
        raise HTTPException(422, "runs_per_case must be an integer >= 1")
    report = run_evals(name, runs_per_case=runs, caller=user, catalog=cat,
                       mutants=bool(body.get("mutants")))
    if not is_admin:
        for c in report["cases"]:      # SPEC.md §11: members see inherited cases' names and results only
            if c["origin"] == "inherited":
                c["checks"] = {}
    return report


def create_app() -> FastAPI:
    app = FastAPI(title="ReAgent agents (wf/resolver)")
    app.include_router(router)
    return app


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    uvicorn.run(create_app(), host=a.host, port=a.port)
