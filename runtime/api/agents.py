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
import json
import copy
import os
import re
import sys
from typing import Any

import yaml
from fastapi import APIRouter, BackgroundTasks, Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from contracts.errors import Code, ReAgentError, Rejection
from runtime.resolver import load_catalog, resolve_agent, resolve_document
from runtime.resolver.source import Catalog, semver_key
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


def _status(doc: dict[str, Any]) -> str:
    """Lifecycle lives in the database (SPEC.md §12). From files: platform agents are
    seeded published (FROZEN D8), every team agent is a draft until it publishes."""
    return doc.get("_status") or ("published" if doc["metadata"].get("level") == "enterprise" else "draft")


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    md, spec = doc["metadata"], doc.get("spec", {})
    uc = spec.get("use_case") or {}
    return {"name": md["name"], "version": str(md.get("version", "0.0.0")), "status": _status(doc),
            "team": md.get("team"), "description": uc.get("description", ""),
            "desired_outcome": uc.get("desired_outcome"), "tags": md.get("tags", []),
            "forked_from": md.get("forked_from"), "update_available": False,
            "pass_rate": None, "production_success_rate": None, "avg_cost_usd": None}


@router.get("/agents")
def get_agents(q: str | None = None, tag: str | None = None, rewrite: bool = False,
               limit: int | None = None, x_reagent_user: str | None = Header(default=None)):
    """The caller's collection, filtered SERVER-side (SPEC.md §2): a member sees only
    their own team's agents, never enterprise-level ones. Admins see everything.

    With `q`, ranked by OUTCOME (runtime/api/agent_search.py): the query is matched
    against what each agent achieves, measured results break ties, and each hit
    carries `match: {score, reason, matched}`. Visibility is applied BEFORE ranking,
    so another team's agents never influence the scores. `rewrite=true` lets a
    cheap model restate a request-shaped query as a desired outcome (live only)."""
    from runtime.api import agent_search
    cat = load_catalog()
    _, is_admin, team = _principal(cat, x_reagent_user)
    measured = agent_search.measured_results()
    docs, items = [], {}
    for name in sorted(cat.agents):
        doc = cat.latest(name)
        if doc is None or not _visible(doc, is_admin, team):
            continue
        item = _summary(doc)
        for k, v in (measured.get(name) or {}).items():
            item[k] = v
        if tag and tag not in item["tags"]:
            continue
        docs.append(doc)
        items[name] = item
    if not q or not q.strip():
        return {"agents": list(items.values())}
    query = agent_search.expand_query(q) if rewrite else q
    out = []
    for hit in agent_search.rank(query, docs, items)[: limit or None]:
        item = items[hit["name"]]
        item["match"] = {"score": hit["score"], "reason": hit["reason"], "matched": hit["matched"]}
        out.append(item)
    return {"agents": out}


@router.get("/agents/{name}")
def get_agent(name: str, x_reagent_user: str | None = Header(default=None)):
    cat = load_catalog()
    _, is_admin, team = _principal(cat, x_reagent_user)
    doc = cat.latest(name)
    if doc is None or not _visible(doc, is_admin, team):
        raise HTTPException(404, "not found")       # deliberately indistinguishable
    versions = [{"version": v, "status": _status(d), "published_at": None, "published_by": None}
                for v, d in sorted(cat.agent_versions(name).items(), key=lambda kv: semver_key(kv[0]))]
    clean = {k: v for k, v in doc.items() if not k.startswith("_")}
    return {**_summary(doc), "level": doc["metadata"].get("level", "team"),
            "spec_yaml": yaml.safe_dump(clean, sort_keys=False), "versions": versions,
            # The run form renders real fields from this, not a raw JSON body.
            "input_schema": doc.get("spec", {}).get("input")}


def _grant_line(name: str, g: dict[str, Any]) -> str:
    parts = [f"{name}: {', '.join(g.get('allow', ['*']))}"]
    if g.get("deny"):
        parts.append("deny " + ", ".join(g["deny"]))
    for k, v in (g.get("resources") or {}).items():
        parts.append(f"{k} {', '.join(v)}")
    return "; ".join(parts)


def _guardrail_lines(g: dict[str, Any]) -> list[str]:
    out = []
    red = g.get("redaction") or {}
    if red.get("secrets"):
        out.append("secret redaction required")
    if red.get("pii"):
        out.append("PII redaction required")
    appr = g.get("approval") or {}
    if appr.get("require_for_risk"):
        out.append("approval required for " + " and ".join(appr["require_for_risk"]) + " risk"
                   + (f" ({appr['timeout_minutes']} minute timeout)" if appr.get("timeout_minutes") else ""))
    if appr.get("actions"):
        out.append("approval required for " + ", ".join(appr["actions"]))
    if g.get("audit_log"):
        out.append("audit log enabled")
    if g.get("deny"):
        out.append("never: " + ", ".join(g["deny"]))
    return out


def _bucket_line(b: dict[str, Any]) -> str:
    return (f"{b['name']}: {b['type']} · read {', '.join(b.get('read', []))} · "
            f"write {', '.join(b.get('write', []) or ['runtime only'])}")


def _agent_node(doc: dict[str, Any]) -> dict[str, Any]:
    spec, md = doc.get("spec", {}), doc["metadata"]
    model = spec.get("model") or {}
    b = spec.get("budget") or {}
    tools = [f"{t['connector']}:{a}" for t in spec.get("tools") or [] for a in t["actions"]]
    tools += [f"agent:{s['ref'].partition('@')[0]}" for s in spec.get("sub_agents") or []]
    ev = spec.get("evaluation") or {}
    cases = ev.get("cases") or []
    budget = [f"${b['usd_per_run']} / run" if "usd_per_run" in b else "",
              f"{b['max_steps']} steps" if "max_steps" in b else "",
              f"{b['timeout_seconds']}s" if "timeout_seconds" in b else ""]
    thresholds = ev.get("thresholds") or {}
    evaluation = [f"{ev['runs_per_case']} runs per case" if "runs_per_case" in ev else "",
                  f"pass rate ≥ {thresholds['pass_rate']:.2f}" if "pass_rate" in thresholds else "",
                  (f"{len(cases)} cases: " + ", ".join(sorted({c.get('kind', 'positive') for c in cases})))
                  if cases else "no cases yet"]
    metrics = (spec.get("observability") or {}).get("metrics") or []
    return {
        "name": md["name"], "version": str(md.get("version", "")), "model": model.get("id"),
        "fallback": model.get("fallback"), "tools": tools,
        "resources": sorted({f"{k}: {', '.join(v)}" for t in spec.get("tools") or []
                             for k, v in (t.get("resources") or {}).items()}),
        "budget": " · ".join(x for x in budget if x) or "inherits the team's limits",
        "purpose": (spec.get("use_case") or {}).get("description", ""),
        "prompt": ((spec.get("prompt") or {}).get("task") or "").strip(),
        "memory": [f"{m['bucket']}: {m['mode']}" for m in spec.get("memory") or []],
        "evaluation": [x for x in evaluation if x],
        "triggers": [t["type"] + (f" ({', '.join(t.get('events', []))})" if t.get("events") else "")
                     for t in spec.get("triggers") or []],
        "guardrails": _guardrail_lines(spec.get("guardrails") or {}),
        "observability": [f"metrics: {', '.join(metrics)}"] if metrics else [],
        "source": f"{md.get('team') or 'enterprise'}/{md['name']}@{md.get('version', '')}",
    }


@router.get("/hierarchy")
def get_hierarchy(x_reagent_user: str | None = Header(default=None)):
    """Enterprise → teams → agents, built per caller on the server. SPEC.md §2: a
    member never receives the enterprise layer, another team, or enterprise-level
    agents; they are absent from the response, not hidden by the client.
    Not in contracts/openapi.yaml yet (reported)."""
    cat = load_catalog()
    user, is_admin, team = _principal(cat, x_reagent_user)
    ent = cat.ent
    if is_admin:
        evl = ent.get("evaluation") or {}
        judge = evl.get("judge") or {}
        req = evl.get("requirements") or {}
        enterprise = {
            "name": cat.enterprise_name, "locked": False, "admins": ent.get("admins", []),
            "description": cat.enterprise["metadata"].get("description", ""),
            "prompt": (ent.get("prompt") or "").strip(), "models": ent.get("models") or {},
            "connectors": [_grant_line(n, g) for n, g in (ent.get("connectors") or {}).items()],
            "triggers": (ent.get("triggers") or {}).get("allow", []),
            "guardrails": _guardrail_lines(ent.get("guardrails") or {}), "limits": ent.get("limits") or {},
            "evaluation": {"judge_model": judge.get("model"), "votes": judge.get("votes"),
                           "min_agreement": judge.get("min_agreement"),
                           "min_cases": ", ".join(f"{v} {k}" for k, v in (req.get("min_cases") or {}).items()),
                           "tool_coverage": bool(req.get("tool_coverage"))},
            "memory": [_bucket_line(b) for b in (ent.get("memory") or {}).get("buckets") or []],
            "trace_retention_days": (ent.get("observability") or {}).get("log_retention_days"),
            "agents": [_agent_node(cat.latest(n)) for n in sorted(cat.agents)
                       if cat.latest(n)["metadata"].get("level") == "enterprise"],
        }
    else:
        enterprise = {"name": cat.enterprise_name, "locked": True,
                      "description": "Set by enterprise admins. Members never see the enterprise layer; "
                                     "its rules still apply to every agent below it."}
    teams = []
    for tname in sorted(cat.teams):
        if not (is_admin or tname == team):
            continue
        t = cat.team(tname) or {}
        teams.append({
            "name": tname, "description": cat.teams[tname]["metadata"].get("description", ""),
            "source": f"team:{tname}", "members": t.get("members", []), "approvers": t.get("approvers", []),
            "prompt": (t.get("prompt") or "").strip(),
            "models": t.get("models") or {"allow": ["inherits the enterprise's"]},
            "connectors": [_grant_line(n, g) for n, g in (t.get("connectors") or {}).items()],
            "guardrails": _guardrail_lines(t.get("guardrails") or {}), "limits": t.get("limits") or {},
            "memory": [_bucket_line(b) for b in (t.get("memory") or {}).get("buckets") or []],
            "agents": [_agent_node(cat.latest(n)) for n in sorted(cat.agents)
                       if cat.latest(n)["metadata"].get("team") == tname],
        })
    return {"label": f"{cat.enterprise_name} · as seen by {user}",
            "viewer": {"user": user, "team": team, "is_enterprise_admin": is_admin},
            "enterprise": enterprise, "teams": teams}


@router.get("/agents/{name}/resolved")
def get_resolved(name: str, x_reagent_user: str | None = Header(default=None)):
    cat = load_catalog()                       # SPEC.md §1: re-read every layer, every time
    user, is_admin, team = _principal(cat, x_reagent_user)
    if name.startswith("team:"):               # the team's base config, previewed unsaved
        base = name[len("team:"):]
        if cat.team(base) is None or not (is_admin or base == team):
            raise HTTPException(404, "not found")
        draft = team_base(cat, base, user, "new-agent")
        out = resolve_agent("new-agent", caller=user, catalog=cat, draft=draft)
    else:
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
    # REAGENT_PIPELINE_RUNS_PER_CASE (the demo's one-run setting) also sets the
    # default here, so the dashboard's Run evals button matches the pipeline.
    runs = body.get("runs_per_case", int(os.environ.get("REAGENT_PIPELINE_RUNS_PER_CASE") or 5))
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
    if run["status"] == "running" and run["stage"] == "review":
        from runtime.publish.live import prerun  # inherited cases need no review: run them now
        prerun(run)
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
    from runtime.publish.live import prerun      # start this case's eval while review goes on
    prerun(run, [case_id])
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
    from runtime.publish import acknowledge, view
    run = _run_for(run_id, load_catalog(), x_reagent_user)
    if x_reagent_user == run["started_by"]:
        acknowledge(run["id"], x_reagent_user)
    # Stages 5-7 run in the background (runtime/publish/live.py); the dashboard polls
    # this run and shows each case as it finishes. Refusals still come back here.
    from runtime.publish import STORE
    from runtime.publish.live import continue_in_background
    from runtime.publish.pipeline import check_ready
    run = STORE.get(run["id"])
    if run["status"] != "running":
        return view(run)
    try:
        check_ready(run)
    except ReAgentError as e:
        return _refusal(e, 409)
    continue_in_background(run["id"])
    return view(run)


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


@router.get("/teams/{team_name}/grants", tags=["agents"])
def get_team_grants(team_name: str, x_reagent_user: str | None = Header(default=None)):
    """What a team's agents may pick from: granted connector actions with their
    resource scope, and allowed models. SPEC.md §2 lets members see exactly this
    for their own team. Not in contracts/openapi.yaml yet (the New agent preview)."""
    from contracts.errors import Code as _Code
    from runtime.resolver.merge import Resolution, Scope

    cat = load_catalog()
    _, is_admin, team = _principal(cat, x_reagent_user)
    if cat.team(team_name) is None or not (is_admin or team_name == team):
        raise HTTPException(404, "not found")
    probe = {"metadata": {"name": "grants-probe", "version": "0.0.0", "level": "team",
                          "team": team_name}, "spec": {}}
    res = Resolution(cat, probe, caller=None, root_team=None, overrides=None, trigger=None)
    res.collect_deny()
    scope = res.team_scope(res.enterprise_scope(), team_name, f"team:{team_name}", silent=True)
    connectors = []
    for name, g in sorted(scope.grants.items()):
        actions = sorted(a for a in g["actions"] if not res.denied(name, a))
        risks = {a: cat.actions(name)[a]["risk"] for a in actions}
        connectors.append({"connector": name, "actions": actions, "risk": risks,
                           "resources": g["resources"]})
    models = sorted(res._allow_chain("models", sorted(cat.models), _Code.UNKNOWN_MODEL)[-1][0])
    return {"team": team_name, "connectors": connectors, "models": models,
            "denylist": sorted({p for p, _ in res.deny})}


@router.post("/publish-runs/{run_id}/abandon")
def post_abandon(run_id: int, x_reagent_user: str | None = Header(default=None)):
    """Abandon a running pipeline, freeing the agent for a new one (FROZEN D17).
    Not in contracts/openapi.yaml yet: the 409 for an already-running pipeline names
    the run, and this is how the dashboard clears it. Reported for a contract update."""
    from runtime.publish import abandon, view
    run = _run_for(run_id, load_catalog(), x_reagent_user)
    return view(abandon(run["id"]))


def team_base(cat: Catalog, team: str, owner: str, name: str) -> dict[str, Any]:
    """The team's base config as a blank agent: it inherits the enterprise and team
    layers and adds nothing. No tools (least privilege, SPEC.md §1), the team's
    first allowed model, an http trigger, and placeholders the author replaces."""
    spec = cat.team(team) or {}
    allowed = (spec.get("models") or {}).get("allow") or (cat.ent.get("models") or {}).get("allow") or ["*"]
    model = next((p for p in allowed if p in cat.models), next(iter(sorted(cat.models)), ""))
    return {"apiVersion": "reagent/v0", "kind": "Agent",
            "metadata": {"name": name, "version": "0.1.0", "level": "team", "team": team},
            "spec": {"use_case": {"description": "What this agent does.",
                                  "desired_outcome": "What success looks like."},
                     "model": {"id": model},
                     "prompt": {"task": "Describe the task step by step."},
                     "triggers": [{"type": "http"}],
                     "ownership": {"owners": [owner]}}}


@router.post("/agents/{name}/fork", status_code=201)
def post_fork(name: str, body: dict[str, Any] = Body(...),
              x_reagent_user: str | None = Header(default=None)):
    """Fork into the caller's team as a new draft. SPEC.md §12, FROZEN D24.

    `name` is a team agent, and `forked_from` records its EXACT version, never a
    range. Or `name` is `team:<team>`: the team's base config, which is how a new
    agent starts from scratch. Agent names cannot contain ':', so it never collides."""
    from runtime.resolver import resolve_document
    from runtime.resolver.source import register_draft

    cat = load_catalog()
    user, is_admin, team = _principal(cat, x_reagent_user)
    new_name = (body or {}).get("new_name", "")
    base_team = name[len("team:"):] if name.startswith("team:") else None
    src = None
    if base_team is not None:
        if cat.team(base_team) is None or not (is_admin or base_team == team):
            raise HTTPException(404, "not found")
        target = base_team
    else:
        src = cat.latest(name)
        if src is None or not _visible(src, is_admin, team):
            raise HTTPException(404, "not found")
        if src["metadata"].get("level") != "team":
            raise HTTPException(422, "only team agents can be forked")
        target = team or src["metadata"].get("team")
        if not is_admin and src["metadata"].get("team") != team:
            return JSONResponse(status_code=403, content={"rejections": [Rejection(
                Code.FORK_OUTSIDE_TEAM, "metadata.forked_from", f"team:{team}", attempted=name).to_dict()]})
    if not NAME.match(new_name or ""):
        raise HTTPException(422, "new_name must match ^[a-z0-9-]+$")
    if cat.agent_versions(new_name):
        raise HTTPException(409, f"an agent called {new_name} already exists")

    if src is None:
        doc = team_base(cat, target, user, new_name)
    else:
        doc = copy.deepcopy(src)
        doc["metadata"].update(name=new_name, version="0.1.0", team=target,
                               forked_from=f"{name}@{src['metadata']['version']}")
        doc["spec"].setdefault("ownership", {})["owners"] = [user]
    md = doc["metadata"]
    resolved = resolve_document(doc, catalog=cat, caller=None)

    if os.environ.get("REAGENT_LAYERS", "files").lower() == "db":
        from runtime.publish.pgstore import PgStore
        PgStore().create_draft(doc, forked_from=(name, str(src["metadata"]["version"])) if src else None)
    else:
        register_draft(doc)
    spec = doc["spec"]
    return {"name": new_name, "version": md["version"], "status": "draft", "team": target,
            "level": "team", "description": spec.get("use_case", {}).get("description", ""),
            "desired_outcome": spec.get("use_case", {}).get("desired_outcome"),
            "tags": md.get("tags", []), "forked_from": md.get("forked_from"),
            "update_available": False,
            "spec_yaml": yaml.safe_dump(doc, sort_keys=False),
            "versions": [{"version": md["version"], "status": "draft", "published_at": None,
                          "published_by": None}],
            "rejections": resolved["rejections"]}


# -- connector requests and admin layer edits (SPEC.md §2, MVP rows 8 and 9) ----
#: Connector requests without a database. In database mode they are rows in
#: connector_requests (contracts/db/001_init.sql).
_REQUESTS: list[dict[str, Any]] = []


def _layers_db() -> bool:
    return os.environ.get("REAGENT_LAYERS", "files").lower() == "db"


def _admin_only(cat: Catalog, user: str | None) -> str:
    who, is_admin, _ = _principal(cat, user)
    if not is_admin:
        raise HTTPException(403, "enterprise admins only")
    return who


def _yaml_doc(body: dict[str, Any] | None) -> Any:
    text = (body or {}).get("spec")
    if not isinstance(text, str):
        raise HTTPException(422, "body.spec must be the raw YAML document")
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HTTPException(422, f"the YAML does not parse: {e.__class__.__name__}")


def _layer_response(report: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=200 if not report["rejections"] else 422, content=report)


@router.post("/connector-requests", status_code=201, tags=["admin"])
def post_connector_request(body: dict[str, Any] = Body(...), x_reagent_user: str | None = Header(default=None)):
    """A team member asks enterprise admins for a connector or action their team lacks."""
    cat = load_catalog()
    user, is_admin, team = _principal(cat, x_reagent_user)
    text = str((body or {}).get("description") or "").strip()
    if not text:
        raise HTTPException(422, "description is required")
    target = (body or {}).get("team") if is_admin else team
    if not target or cat.team(target) is None:
        raise HTTPException(422, "requests are made for a team")
    req = {"description": text[:2000], "team": target, "requested_by": user, "status": "open",
           "connector": (body or {}).get("connector")}
    if _layers_db():
        import psycopg
        with psycopg.connect(os.environ.get("DATABASE_URL") or "", connect_timeout=15) as c:
            tid = c.execute("SELECT id FROM teams WHERE name = %s", (target,)).fetchone()[0]
            uid = c.execute("SELECT id FROM users WHERE name = %s", (user,)).fetchone()[0]
            row = c.execute("INSERT INTO connector_requests (team_id, requested_by, description) "
                            "VALUES (%s, %s, %s) RETURNING id, created_at", (tid, uid, req["description"])).fetchone()
        req.update(id=row[0], created_at=row[1].isoformat())
    else:
        from runtime.publish.store import now
        req.update(id=len(_REQUESTS) + 1, created_at=now())
        _REQUESTS.append(req)
    return req


def _all_requests() -> list[dict[str, Any]]:
    if not _layers_db():
        return list(_REQUESTS)
    import psycopg
    with psycopg.connect(os.environ.get("DATABASE_URL") or "", connect_timeout=15) as c:
        rows = c.execute("SELECT r.id, t.name, u.name, r.description, r.status, r.created_at FROM connector_requests r "
                         "JOIN teams t ON t.id = r.team_id JOIN users u ON u.id = r.requested_by ORDER BY r.id").fetchall()
    return [{"id": i, "team": t, "requested_by": u, "description": d, "status": s, "created_at": c_.isoformat()}
            for i, t, u, d, s, c_ in rows]


@router.get("/connector-requests", tags=["admin"])
def get_connector_requests(x_reagent_user: str | None = Header(default=None)):
    """Admins see every request; members see their own team's."""
    cat = load_catalog()
    _, is_admin, team = _principal(cat, x_reagent_user)
    return {"requests": [r for r in _all_requests() if is_admin or r["team"] == team]}


@router.post("/connector-requests/{request_id}/decide", tags=["admin"])
def post_decide_request(request_id: int, body: dict[str, Any] = Body(...),
                        x_reagent_user: str | None = Header(default=None)):
    """An admin closes a request: fulfilled (after granting it in the team layer) or rejected.
    Not in contracts/openapi.yaml yet (reported)."""
    cat = load_catalog()
    _admin_only(cat, x_reagent_user)
    status = (body or {}).get("status")
    if status not in ("fulfilled", "rejected"):
        raise HTTPException(422, "status must be fulfilled or rejected")
    if _layers_db():
        import psycopg
        with psycopg.connect(os.environ.get("DATABASE_URL") or "", connect_timeout=15) as c:
            if c.execute("UPDATE connector_requests SET status = %s WHERE id = %s RETURNING id",
                         (status, request_id)).fetchone() is None:
                raise HTTPException(404, "not found")
    else:
        req = next((r for r in _REQUESTS if r["id"] == request_id), None)
        if req is None:
            raise HTTPException(404, "not found")
        req["status"] = status
    return {"id": request_id, "status": status}


@router.post("/admin/teams/{team_id}", tags=["admin"])
def post_admin_team(team_id: str, body: dict[str, Any] = Body(...), x_reagent_user: str | None = Header(default=None)):
    """Edit a team layer: grants, models, limits, guardrails, members. Reports which of
    the team's agents start or stop resolving. `team_id` is the team's name."""
    from runtime.resolver.layers import apply
    cat = load_catalog()
    _admin_only(cat, x_reagent_user)
    doc = _yaml_doc(body)
    if not isinstance(doc, dict) or (doc.get("metadata") or {}).get("name") != team_id:
        raise HTTPException(422, f"the document must be the Team layer named {team_id}")
    return _layer_response(apply(doc, dry_run=bool((body or {}).get("dry_run"))))


@router.post("/admin/enterprise", tags=["admin"])
def post_admin_enterprise(body: dict[str, Any] = Body(...), x_reagent_user: str | None = Header(default=None)):
    from runtime.resolver.layers import apply
    cat = load_catalog()
    _admin_only(cat, x_reagent_user)
    return _layer_response(apply(_yaml_doc(body), dry_run=bool((body or {}).get("dry_run"))))


@router.post("/admin/registry", status_code=201, tags=["admin"])
def post_admin_registry(body: dict[str, Any] = Body(...), x_reagent_user: str | None = Header(default=None)):
    """Register a connector or model. A new connector's actions also need wire names in
    contracts/connector_actions.json (generated) before agents can call them."""
    from runtime.resolver.layers import apply
    cat = load_catalog()
    _admin_only(cat, x_reagent_user)
    kind = (body or {}).get("kind")
    if kind not in ("connector", "model"):
        raise HTTPException(422, "kind must be connector or model")
    doc = _yaml_doc(body)
    if not isinstance(doc, dict) or doc.get("kind") != kind.capitalize():
        raise HTTPException(422, f"the document must be kind: {kind.capitalize()}")
    report = apply(doc)
    return JSONResponse(status_code=201 if report["saved"] else 422, content=report)


@router.get("/admin/layers", tags=["admin"])
def get_admin_layers(x_reagent_user: str | None = Header(default=None)):
    """The editable layer documents as YAML, for the admin screens. Admins only."""
    cat = load_catalog()
    _admin_only(cat, x_reagent_user)
    dump = lambda d: yaml.safe_dump(d, sort_keys=False)
    return {"enterprise": dump(cat.enterprise), "teams": {n: dump(d) for n, d in sorted(cat.teams.items())},
            "connectors": sorted(cat.connectors), "models": sorted(cat.models)}


@router.get("/meta-agents", tags=["meta-agents"])
def get_meta_agents(x_reagent_user: str | None = Header(default=None)):
    """Runnable meta-agents (SPEC.md §10): names and descriptions only. Every user may
    run them; their configs are enterprise-level and never returned to a member."""
    cat = load_catalog()
    _principal(cat, x_reagent_user)
    out = []
    for name in sorted(cat.agents):
        doc = cat.latest(name)
        md = doc["metadata"]
        if md.get("level") == "enterprise" and md.get("access") == "run_only":
            spec = doc.get("spec", {})
            out.append({"name": name, "description": (spec.get("use_case") or {}).get("description", ""),
                        "input": (spec.get("input") or {}).get("properties", {})})
    return {"meta_agents": out}


@router.post("/meta-agents/{name}/runs", status_code=202, tags=["meta-agents"])
def post_meta_agent_run(name: str, body: dict[str, Any] = Body(...),
                        x_reagent_user: str | None = Header(default=None)):
    """Run a meta-agent for the CALLER: it acts on the caller's team only (FROZEN D1),
    sees the enterprise prompt but never the caller's team prompt (D21), and reaches
    the platform through the platform connector, filtered by the caller."""
    from runtime.engine import run_agent
    cat = load_catalog()
    user, is_admin, _ = _principal(cat, x_reagent_user)
    # Team decision: meta-agents run when a user action needs them (describing a
    # new agent, publishing, a failed run), not on demand. Admins keep this route.
    if not is_admin:
        raise HTTPException(403, "meta-agents run as part of the user flow, not on demand")
    doc = cat.latest(name)
    if doc is None or doc["metadata"].get("access") != "run_only":
        raise HTTPException(404, "not found")
    payload = (body or {}).get("input")
    if not isinstance(payload, dict):
        raise HTTPException(422, "body.input must be an object")
    r = run_agent(name, payload, user=user, trigger="http")
    keep = ("run_id", "status", "output", "error", "cost_usd", "steps", "model", "rejections")
    return {k: r.get(k) for k in keep if k in r}


def _slack_signature_ok(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Slack's v0 signing: HMAC-SHA256 of 'v0:<timestamp>:<body>' with the signing secret."""
    import hashlib
    import hmac
    import time
    try:
        if abs(time.time() - int(timestamp)) > 300:          # replay window: 5 minutes
            return False
    except ValueError:
        return False
    mine = "v0=" + hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mine, signature or "")


def _channel_name(channel: str) -> str:
    """Slack events carry a channel ID; triggers name channels. Resolve through
    REAGENT_SLACK_CHANNELS ('C0123=#ask-people-ops,...') or, live, conversations.info."""
    if channel.startswith("#"):
        return channel
    for pair in os.environ.get("REAGENT_SLACK_CHANNELS", "").split(","):
        cid, _, name = pair.partition("=")
        if cid.strip() == channel and name.strip():
            return name.strip()
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token:
        import httpx
        try:
            r = httpx.get("https://slack.com/api/conversations.info", params={"channel": channel},
                          headers={"Authorization": f"Bearer {token}"}, timeout=3)
            name = (r.json().get("channel") or {}).get("name")
            if name:
                return f"#{name}"
        except httpx.HTTPError:
            pass
    return channel


def slack_targets(cat: Catalog, event: str, channel: str) -> list[str]:
    """Agents whose slack trigger listens for this event in this channel (globs allowed)."""
    from runtime.resolver.globs import match_any
    out = []
    for name in sorted(cat.agents):
        doc = cat.latest(name)
        for t in doc.get("spec", {}).get("triggers") or []:
            if (t.get("type") == "slack" and event in (t.get("events") or [])
                    and match_any(channel, t.get("channels") or ["*"])):
                out.append(name)
                break
    return out


def _run_from_slack(name: str, payload: dict[str, Any]) -> None:
    from runtime.engine import run_agent
    # FROZEN D2: no session, so no acting user. on_behalf_of tools are refused
    # with E_ON_BEHALF_OF_NO_USER; the run and its refusal are recorded as usual.
    run_agent(name, payload, user=None, trigger="slack")


@router.post("/events/slack", tags=["triggers"])
async def post_slack_events(request: Request, background: BackgroundTasks):
    """MVP row 3: the Slack Events API endpoint. Verifies Slack's signature, answers the
    URL verification challenge, and runs every agent whose `slack` trigger matches an
    app_mention. Runs happen in the background: Slack wants a reply within 3 seconds.
    Not in contracts/openapi.yaml yet (reported)."""
    body = await request.body()
    secret = os.environ.get("SLACK_SIGNING_SECRET")
    if not secret:
        raise HTTPException(503, "SLACK_SIGNING_SECRET is not configured")
    if not _slack_signature_ok(secret, request.headers.get("X-Slack-Request-Timestamp", ""), body,
                               request.headers.get("X-Slack-Signature", "")):
        raise HTTPException(401, "bad Slack signature")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(400, "not JSON")
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}
    if request.headers.get("X-Slack-Retry-Num"):
        return {"ok": True, "ignored": "retry"}               # the first delivery already ran it
    event = data.get("event") or {}
    if data.get("type") != "event_callback" or event.get("type") != "app_mention":
        return {"ok": True, "ignored": event.get("type") or data.get("type")}
    channel = _channel_name(str(event.get("channel", "")))
    targets = slack_targets(load_catalog(), "app_mention", channel)
    payload = {"text": event.get("text", ""), "channel": channel, "user": event.get("user"),
               "thread_ts": event.get("thread_ts") or event.get("ts")}
    for name in targets:
        background.add_task(_run_from_slack, name, payload)
    return {"ok": True, "channel": channel, "agents": targets}


#: Outcome-search score above which forking the closest agent beats a blank base config.
FORK_SCORE = 2.5


def _slug(text: str, taken: set[str]) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {"a", "an", "the", "and", "or", "that", "to", "for", "of", "in", "on", "our", "my", "with", "agent", "which", "it", "them"}
    base = "-".join([w for w in words if w not in stop][:3]) or "new-agent"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


@router.post("/agents/build", status_code=201, tags=["agents"])
def post_build_agent(body: dict[str, Any] = Body(...), x_reagent_user: str | None = Header(default=None)):
    """agent-builder, triggered by describing an agent in the New agent drawer (SPEC.md §10).

    It drafts a new agent for the caller's team from a plain-English request: it
    forks the closest existing agent by outcome when one fits, otherwise the
    team's base config. With REAGENT_MODEL=live the real agent-builder meta-agent
    runs through the platform connector; without it, or if it proposes nothing,
    the same decision is made directly. The draft is never published.
    Not in contracts/openapi.yaml yet (reported)."""
    cat = load_catalog()
    user, is_admin, team = _principal(cat, x_reagent_user)
    request = str((body or {}).get("request") or "").strip()
    if len(request) < 8:
        raise HTTPException(422, "describe the agent in a sentence or two")
    target = team or (body or {}).get("team")
    if not target or cat.team(target) is None:
        raise HTTPException(422, "agents are built for a team")

    hits = get_agents(q=request, limit=3, x_reagent_user=user)["agents"]
    hits = [h for h in hits if h.get("team") == target]
    candidates = [{"name": h["name"], "score": round(h["match"]["score"], 2), "reason": h["match"]["reason"]}
                  for h in hits if h.get("match")]

    by = "platform"
    if os.environ.get("REAGENT_MODEL", "fake").lower() == "live":
        before = set(cat.agents)
        try:
            from runtime.engine import run_agent
            run_agent("agent-builder", {"request": request}, user=user, trigger="http")
        except Exception:  # noqa: BLE001 - fall back to the direct decision below
            pass
        fresh = sorted(set(load_catalog().agents) - before)
        if fresh:
            made = load_catalog().latest(fresh[0])
            resolved = resolve_document(made, catalog=load_catalog(), caller=None)
            return {"name": fresh[0], "by": "agent-builder", "source": made["metadata"].get("forked_from") or f"team:{target}",
                    "candidates": candidates, "spec_yaml": yaml.safe_dump(made, sort_keys=False),
                    "rejections": resolved["rejections"], "team": target}

    name = (body or {}).get("name") or _slug(request, set(cat.agents))
    if not NAME.match(name) or cat.agent_versions(name):
        raise HTTPException(409, f"an agent called {name} already exists")
    best = candidates[0] if candidates and candidates[0]["score"] >= FORK_SCORE else None
    if best:
        src = cat.latest(best["name"])
        doc = copy.deepcopy(src)
        doc["metadata"].update(name=name, version="0.1.0", team=target,
                               forked_from=f"{best['name']}@{src['metadata']['version']}")
        task = (doc["spec"].get("prompt") or {}).get("task", "").rstrip()
        doc["spec"]["prompt"] = {"task": f"{task}\n\nAdapted for this request: {request}"}
        reason = f"Forked {best['name']}: {best['reason']}"
    else:
        doc = team_base(cat, target, user, name)
        doc["spec"]["prompt"] = {"task": f"{request}\n\nWork step by step and say what you did."}
        reason = ("No existing agent is close enough, so it starts from the team's base config."
                  if candidates else "No similar agent in the team, so it starts from the team's base config.")
    doc["spec"]["use_case"] = {"description": request[:300], "desired_outcome": request[:300]}
    doc["spec"].setdefault("ownership", {})["owners"] = [user]
    resolved = resolve_document(doc, catalog=cat, caller=None)
    if _layers_db():
        from runtime.publish.pgstore import PgStore
        src_ref = doc["metadata"].get("forked_from")
        PgStore().create_draft(doc, forked_from=tuple(src_ref.split("@")) if src_ref else None)
    else:
        from runtime.resolver.source import register_draft
        register_draft(doc)
    return {"name": name, "by": by, "source": doc["metadata"].get("forked_from") or f"team:{target}",
            "reason": reason, "candidates": candidates, "team": target,
            "spec_yaml": yaml.safe_dump(doc, sort_keys=False), "rejections": resolved["rejections"]}


@router.get("/runs/{run_id}/triage", tags=["runs"])
async def get_run_triage(run_id: int, x_reagent_user: str | None = Header(default=None)):
    """failure-triage, triggered by opening a failed or denied run in the trace viewer.
    Not in contracts/openapi.yaml yet (reported)."""
    from runtime.api import runs as _runs
    from runtime.evals.triage import triage
    _principal(load_catalog(), x_reagent_user)
    run = await _runs.get_run(run_id)
    trace = await _runs.get_trace(run_id)
    return triage(run, trace)


@router.post("/publish-runs/{run_id}/cost-check", tags=["publish"])
def post_cost_check(run_id: int, x_reagent_user: str | None = Header(default=None)):
    """cost-optimizer, triggered from a pipeline whose evals have run (SPEC.md §10).

    Re-runs the same approved cases on every other model the team allows and
    recommends the cheapest one that still clears the gate. It proposes; the
    author changes model.id and publishes again. Not in openapi.yaml yet."""
    from runtime.evals import run_evals
    cat = load_catalog()
    run = _run_for(run_id, cat, x_reagent_user)
    if not run.get("report"):
        raise HTTPException(409, "evals have not run in this pipeline yet")
    user, _, _ = _principal(cat, x_reagent_user)
    draft = copy.deepcopy(run["draft"])
    kept = [c["_case"] for c in run["cases"] if c.get("decision") in ("approved", "edited") and c["origin"] != "inherited"]
    draft["spec"].setdefault("evaluation", {})["cases"] = kept
    team = draft["metadata"].get("team")
    allowed = get_team_grants(team, x_reagent_user=user)["models"] if team else sorted(cat.models)
    current = draft["spec"]["model"]["id"]
    runs = int(os.environ.get("REAGENT_PIPELINE_RUNS_PER_CASE") or 1)
    base = run["report"]
    options = [{"model": current, "pass_rate": base["pass_rate"], "avg_cost": base["avg_cost"],
                "gate_blocked": base["gate_blocked"], "current": True}]
    for model in allowed:
        if model == current:
            continue
        rep = run_evals(run["agent"], runs, draft=draft, catalog=cat, caller=user,
                        overrides={"spec": {"model": {"id": model}}})
        options.append({"model": model, "pass_rate": rep["pass_rate"], "avg_cost": rep["avg_cost"],
                        "gate_blocked": rep["gate_blocked"], "current": False})
    passing = [o for o in options if not o["gate_blocked"]]
    best = min(passing, key=lambda o: (o["avg_cost"], -o["pass_rate"])) if passing else None
    if best is None:
        advice = "No allowed model clears the gate on these tests yet."
    elif best["current"] or best["avg_cost"] >= options[0]["avg_cost"]:
        advice = f"Keep {current}: no cheaper allowed model clears the gate."
    else:
        saving = 1 - best["avg_cost"] / options[0]["avg_cost"] if options[0]["avg_cost"] else 0
        advice = (f"Switch model.id to {best['model']}: it clears the gate at "
                  f"${best['avg_cost']:.4f} a run, {saving:.0%} cheaper than {current}.")
    return {"by": "cost-optimizer", "runs_per_case": runs, "options": options,
            "recommendation": best["model"] if best else None, "advice": advice}


@router.get("/runs", tags=["runs"])
def get_runs(agent: str | None = None, limit: int = 25, x_reagent_user: str | None = Header(default=None)):
    """Recent top-level runs the caller may see, newest first, so the trace viewer can
    list them instead of asking for an id. Filtered on the SERVER (SPEC.md §2): a
    member sees runs of their own team's agents only. Not in openapi.yaml yet."""
    from runtime.engine.recorder import MEMORY, database_url
    cat = load_catalog()
    _, is_admin, team = _principal(cat, x_reagent_user)
    limit = max(1, min(int(limit), 100))

    def visible(name: str | None) -> bool:
        doc = cat.latest(name) if name else None
        return doc is not None and _visible(doc, is_admin, team) and (agent is None or name == agent)

    out: list[dict[str, Any]] = []
    if database_url():
        import psycopg
        with psycopg.connect(database_url(), connect_timeout=15) as c:
            rows = c.execute(
                "SELECT r.id, a.name, r.status, r.trigger, u.name, r.started_at, r.cost_usd, r.error "
                "FROM runs r JOIN agent_versions v ON v.id = r.agent_version_id JOIN agents a ON a.id = v.agent_id "
                "LEFT JOIN users u ON u.id = r.triggered_by WHERE r.parent_run_id IS NULL "
                "ORDER BY r.started_at DESC LIMIT %s", (limit * 4,)).fetchall()
        for rid, name, status, trigger, user, started, cost, error in rows:
            if visible(name):
                out.append({"run_id": rid, "agent": name, "status": status, "trigger": trigger, "user": user,
                            "started_at": started.isoformat() if started else None,
                            "cost_usd": float(cost or 0), "error": (error or "")[:160] or None})
    else:
        for rid in sorted(MEMORY, reverse=True):
            r = MEMORY[rid]
            if r.get("parent_run_id") is None and visible(r.get("agent")):
                out.append({"run_id": rid, "agent": r.get("agent"), "status": r.get("status"),
                            "trigger": r.get("trigger"), "user": r.get("user"), "started_at": None,
                            "cost_usd": float(r.get("cost_usd") or 0), "error": (r.get("error") or "")[:160] or None})
    return {"runs": out[:limit]}


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
    # psycopg's async mode rejects Windows' default ProactorEventLoop.
    loop = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"
    uvicorn.run(create_app(), host=a.host, port=a.port, loop=loop)
