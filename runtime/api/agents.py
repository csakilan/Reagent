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
import re
from typing import Any

import yaml
from fastapi import APIRouter, Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from contracts.errors import Code, ReAgentError, Rejection
from runtime.resolver import load_catalog, resolve_agent
from runtime.resolver.source import Catalog
from runtime.resolver.validate import validate_draft_text

# ONE router for every wf/resolver route: the runtime app mounts `router` and
# nothing else (runtime/api/runs.py), so a second router would never be served.
router = APIRouter(prefix="/v0", tags=["agents"])


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


@router.get("/agents/{name}/resolved")
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


@router.post("/agents/{name}/validate")
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


@router.post("/agents/{name}/evals")
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


@router.post("/agents/{name}/publish", status_code=202, tags=["publish"])
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


@router.get("/publish-runs/{run_id}")
def get_publish_run(run_id: int, x_reagent_user: str | None = Header(default=None)):
    from runtime.publish import view
    return view(_run_for(run_id, load_catalog(), x_reagent_user))


@router.post("/publish-runs/{run_id}/cases/{case_id:path}/review")
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


@router.post("/publish-runs/{run_id}/cases/{case_id:path}/dry-run")
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


@router.post("/publish-runs/{run_id}/continue", status_code=202)
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


@router.post("/admin/calibration", tags=["admin"])
def post_calibration(body: dict[str, Any] = Body(...),
                     x_reagent_user: str | None = Header(default=None)):
    """Add human labels, then re-grade every label with the current judge (FROZEN D19).
    Enterprise admins only: calibration decides whether the judge may gate a publish."""
    from runtime.evals.judge import recalibrate

    cat = load_catalog()
    user, is_admin, _ = _principal(cat, x_reagent_user)
    if not is_admin:
        raise HTTPException(403, "enterprise admins only")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise HTTPException(503, "calibration labels live in the database, and none is configured")
    labels = (body or {}).get("labels")
    if not isinstance(labels, list) or not all(
            isinstance(l, dict) and {"case_name", "output_redacted", "human_verdict"} <= set(l)
            for l in labels):
        raise HTTPException(422, "labels must be a list of {case_name, output_redacted, human_verdict}")
    import psycopg
    from runtime.guardrails.redaction import Redactor
    redact = Redactor()
    with psycopg.connect(url, connect_timeout=15) as c:
        uid = c.execute("SELECT id FROM users WHERE name = %s", (user,)).fetchone()
        if uid is None:
            raise HTTPException(403, f"{user} is not a known user")
        for l in labels:
            # The column is named _redacted: redact BEFORE the write, never on read.
            text = redact.redact_text(str(l["output_redacted"]))[0]
            c.execute("INSERT INTO calibration_labels (agent_version_id, case_name, output_redacted, "
                      "human_verdict, labeled_by) VALUES (%s, %s, %s, %s, %s)",
                      (l.get("agent_version_id"), l["case_name"], text, bool(l["human_verdict"]), uid[0]))
    out = recalibrate(cat, url)
    return {k: out[k] for k in ("agreement", "kappa", "n_labels", "calibrated", "judge_model")}


NAME = re.compile(r"^[a-z0-9-]+$")


@router.post("/agents/{name}/fork", status_code=201)
def post_fork(name: str, body: dict[str, Any] = Body(...),
              x_reagent_user: str | None = Header(default=None)):
    """Fork a team agent into the caller's team as a new draft. SPEC.md §12, FROZEN D24:
    `forked_from` records the source's EXACT version, never a range."""
    from runtime.resolver import resolve_document
    from runtime.resolver.source import register_draft

    cat = load_catalog()
    user, is_admin, team = _principal(cat, x_reagent_user)
    src = cat.latest(name)
    if src is None or not _visible(src, is_admin, team):
        raise HTTPException(404, "not found")
    if src["metadata"].get("level") != "team":
        raise HTTPException(422, "only team agents can be forked")
    target = team or src["metadata"].get("team")
    if not is_admin and src["metadata"].get("team") != team:
        return JSONResponse(status_code=403, content={"rejections": [Rejection(
            Code.FORK_OUTSIDE_TEAM, "metadata.forked_from", f"team:{team}", attempted=name).to_dict()]})
    new_name = (body or {}).get("new_name", "")
    if not NAME.match(new_name or ""):
        raise HTTPException(422, "new_name must match ^[a-z0-9-]+$")
    if cat.agent_versions(new_name):
        raise HTTPException(409, f"an agent called {new_name} already exists")

    doc = copy.deepcopy(src)
    md = doc["metadata"]
    md.update(name=new_name, version="0.1.0", team=target,
              forked_from=f"{name}@{src['metadata']['version']}")
    doc["spec"].setdefault("ownership", {})["owners"] = [user]
    resolved = resolve_document(doc, catalog=cat, caller=None)

    if os.environ.get("REAGENT_LAYERS", "files").lower() == "db":
        from runtime.publish.pgstore import PgStore
        PgStore().create_draft(doc, forked_from=(name, str(src["metadata"]["version"])))
    else:
        register_draft(doc)
    spec = doc["spec"]
    return {"name": new_name, "version": md["version"], "status": "draft", "team": target,
            "level": "team", "description": spec.get("use_case", {}).get("description", ""),
            "desired_outcome": spec.get("use_case", {}).get("desired_outcome"),
            "tags": md.get("tags", []), "forked_from": md["forked_from"], "update_available": False,
            "spec_yaml": yaml.safe_dump(doc, sort_keys=False),
            "versions": [{"version": md["version"], "status": "draft", "published_at": None,
                          "published_by": None}],
            "rejections": resolved["rejections"]}


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
