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
import copy
import os
from typing import Any

import yaml
from fastapi import APIRouter, Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from contracts.errors import Code, ReAgentError
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


# -- publish pipeline (SPEC.md §11) -------------------------------------------
publish_router = APIRouter(prefix="/v0", tags=["publish"])
DASHBOARD_URL = os.environ.get("REAGENT_DASHBOARD_URL", "http://localhost:3000")


def _refusal(err: ReAgentError, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content=err.to_dict())


def _run_for(run_id: int, cat: Catalog, user: str | None):
    from runtime.publish import STORE
    _, is_admin, team = _principal(cat, user)
    try:
        run = STORE.get(run_id)
    except KeyError:
        raise HTTPException(404, "not found")
    doc = run["draft"]
    if not _visible(doc, is_admin, team):
        raise HTTPException(404, "not found")
    return run


@router.post("/{name}/publish", status_code=202, tags=["publish"])
def post_publish(name: str, body: dict[str, Any] | None = Body(default=None),
                 x_reagent_user: str | None = Header(default=None)):
    """Starts a PIPELINE, not a publish. Stages 1-3 run now; stage 4 is a human."""
    from runtime.publish import start, view

    cat = load_catalog()
    user, is_admin, team = _principal(cat, x_reagent_user)
    draft = None
    if body and isinstance(body.get("spec"), str):
        draft = yaml.safe_load(body["spec"])
    doc = draft or cat.latest(name)
    if doc is None or not _visible(doc, is_admin, team):
        raise HTTPException(404, "not found")
    try:
        run = start(name, user, draft, catalog=cat)
    except ReAgentError as e:
        code = e.rejections[0].code
        return _refusal(e, 409 if code is Code.PIPELINE_ALREADY_RUNNING else 422)
    return {"publish_run_id": run["id"], "review_url": f"{DASHBOARD_URL}/publish?run={run['id']}",
            "run": view(run)}


@publish_router.get("/publish-runs/{run_id}")
def get_publish_run(run_id: int, x_reagent_user: str | None = Header(default=None)):
    from runtime.publish import view
    return view(_run_for(run_id, load_catalog(), x_reagent_user))


@publish_router.post("/publish-runs/{run_id}/cases/{case_id:path}/review")
def post_review(run_id: int, case_id: str, body: dict[str, Any] = Body(...),
                x_reagent_user: str | None = Header(default=None)):
    from runtime.publish import review, view
    run = _run_for(run_id, load_catalog(), x_reagent_user)
    decision = {"approve": "approve", "edit": "edit", "reject": "reject"}.get(body.get("decision"))
    if decision is None:
        raise HTTPException(422, "decision must be approve, edit or reject")
    try:
        run = review(run["id"], case_id, decision, x_reagent_user,
                     edited_case=body.get("edited_case"), reason=body.get("reason"))
    except KeyError:
        raise HTTPException(404, f"no case {case_id} in this pipeline")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except ReAgentError as e:
        return _refusal(e, 409)
    return view(run)


@publish_router.post("/publish-runs/{run_id}/cases/{case_id:path}/dry-run")
def post_dry_run(run_id: int, case_id: str, x_reagent_user: str | None = Header(default=None)):
    """Runs one case once so the reviewer sees real (redacted) agent output."""
    from runtime.evals import run_evals
    cat = load_catalog()
    run = _run_for(run_id, cat, x_reagent_user)
    c = next((x for x in run["cases"] if x["case_id"] == case_id), None)
    if c is None or c["origin"] == "inherited":
        raise HTTPException(404, f"no reviewable case {case_id} in this pipeline")
    draft = copy.deepcopy(run["draft"])
    draft["spec"].setdefault("evaluation", {})["cases"] = [c["_case"]]
    rep = run_evals(run["agent"], 1, draft=draft, catalog=cat, include_inherited=False)
    one = rep["cases"][0] if rep["cases"] else {}
    return {"output": {"checks": one.get("checks"), "error": one.get("error"),
                       "blocked_calls": one.get("blocked_calls", [])},
            "tool_calls": one.get("tool_calls", []), "passed": bool(one.get("passes")),
            "cost_usd": rep["total_cost"]}


@publish_router.post("/publish-runs/{run_id}/continue", status_code=202)
def post_continue(run_id: int, x_reagent_user: str | None = Header(default=None)):
    """Continues to evals once every case is reviewed. Continuing is the author's
    acknowledgement of stage 2's advisory findings."""
    from runtime.publish import acknowledge, proceed, view
    run = _run_for(run_id, load_catalog(), x_reagent_user)
    if x_reagent_user == run["started_by"]:
        acknowledge(run["id"], x_reagent_user)
    try:
        return view(proceed(run["id"]))
    except ReAgentError as e:
        return _refusal(e, 409)


def create_app() -> FastAPI:
    app = FastAPI(title="ReAgent agents (wf/resolver)")
    app.include_router(router)
    app.include_router(publish_router)
    return app


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    uvicorn.run(create_app(), host=a.host, port=a.port)
