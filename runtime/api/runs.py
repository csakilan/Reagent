"""Run and approval routes. WF1. Shapes are contracts/openapi.yaml.

  POST /v0/agents/{name}/runs   {input, wait?}    202 RunAccepted | 200 RunResult | 402 | 422
  GET  /v0/runs/{id}                              RunResult
  GET  /v0/runs/{id}/trace                        {run_id, children, events[]}
  GET  /v0/approvals?status=pending|decided|all   {approvals: [Approval]}
  POST /v0/approvals/{id}       {decision}        RunResult | 410

The acting principal comes ONLY from the X-ReAgent-User header (FROZEN D2).
A `user` field in the body is rejected by the schema, not ignored.

`router` is what a shared app should include. `app` is a standalone app that
also mounts WF2's runtime.api.agents router if it exists, so this file can be
served on its own:  uvicorn runtime.api.runs:app --port 8000
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from runtime.engine import adecide_approval, arun_agent
from runtime.engine.recorder import MEMORY, MEMORY_APPROVALS, database_url

router = APIRouter()
_TASKS: set[asyncio.Task] = set()

#: Codes that mean "a budget is exhausted at some layer" -> HTTP 402.
_BUDGET_CODES = {"E_AGGREGATE_BUDGET_EXCEEDED", "E_CONCURRENCY_EXCEEDED"}
#: Codes that mean the agent does not resolve -> HTTP 422.
_RESOLVE_CODES = {"E_SCHEMA_INVALID", "E_WIDEN_ALLOWLIST", "E_LIMIT_ABOVE_PARENT",
                  "E_SAFETY_FLAG_DISABLED", "E_LOCKED_PATH", "E_RESOURCE_OUT_OF_SCOPE",
                  "E_DENIED_ACTION", "E_CONNECTOR_NOT_GRANTED", "E_ACTION_NOT_GRANTED",
                  "E_UNKNOWN_CONNECTOR", "E_UNKNOWN_ACTION", "E_UNKNOWN_MODEL",
                  "E_MODEL_NOT_ALLOWED", "E_UNKNOWN_TEAM", "E_SUBAGENT_CYCLE"}


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no `user`: D2
    input: dict[str, Any]
    wait: bool = False


class Decision(BaseModel):
    decision: Literal["approved", "rejected"]


def _conn():
    import psycopg
    return psycopg.connect(database_url(), autocommit=True, connect_timeout=10)


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v


def _as_result(r: dict[str, Any]) -> dict[str, Any]:
    out = r.get("output")
    approvals = r.get("approvals") or []
    return {
        "run_id": r.get("run_id"), "parent_run_id": r.get("parent_run_id"),
        "status": r.get("status"),
        "output": {"text": out} if isinstance(out, str) else out,
        "cost_usd": float(r.get("cost_usd") or 0.0),
        "input_tokens": int(r.get("input_tokens") or 0),
        "output_tokens": int(r.get("output_tokens") or 0),
        "steps": int(r.get("steps") or 0), "latency_ms": r.get("latency_ms"),
        "model": r.get("model"), "error": r.get("error"),
        "rejections": r.get("rejections") or [],
        "pending_approval_id": approvals[0] if approvals else r.get("pending_approval_id"),
    }


def _http_for(result: dict[str, Any]) -> int:
    codes = {x.get("code") for x in result.get("rejections") or []}
    if codes & _BUDGET_CODES:
        return 402
    if codes & _RESOLVE_CODES:
        return 422
    return 200


@router.post("/v0/agents/{name}/runs")
async def create_run(name: str, body: RunRequest,
                     x_reagent_user: str | None = Header(default=None)) -> JSONResponse:
    if body.wait:
        res = await arun_agent(name, body.input, user=x_reagent_user, trigger="http")
        code = _http_for(res)
        payload = {"rejections": res["rejections"]} if code in (402, 422) else _as_result(res)
        return JSONResponse(payload, status_code=code)

    started = asyncio.Event()
    holder: dict[str, Any] = {}

    def on_start(run_id: int) -> None:
        holder["run_id"] = run_id
        started.set()

    task = asyncio.create_task(arun_agent(name, body.input, user=x_reagent_user,
                                          trigger="http", on_start=on_start))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    waiter = asyncio.create_task(started.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    waiter.cancel()
    if task.done():  # refused before or right after the runs row was written
        res = task.result()
        code = _http_for(res)
        if code in (402, 422):
            return JSONResponse({"rejections": res["rejections"]}, status_code=code)
        return JSONResponse(_as_result(res), status_code=200)
    return JSONResponse({"run_id": holder["run_id"], "status": "running"}, status_code=202)


@router.get("/v0/runs/{run_id}")
async def get_run(run_id: int) -> dict[str, Any]:
    if not database_url():
        r = MEMORY.get(run_id)
        if not r:
            raise HTTPException(404, f"run {run_id} not found")
        pending = [a["id"] for a in MEMORY_APPROVALS.values()
                   if a["run_id"] == run_id and a["decision"] is None]
        return _as_result({**r, "run_id": run_id, "pending_approval_id": pending[0] if pending else None})
    with _conn() as c:
        row = c.execute(
            "select id, parent_run_id, status, cost_usd, input_tokens, output_tokens, steps,"
            " latency_ms, model, error from runs where id=%s", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"run {run_id} not found")
        pend = c.execute("select id from approvals where run_id=%s and decision is null"
                         " order by id limit 1", (run_id,)).fetchone()
        # The final (redacted) text is stored on the root span_close.
        out = c.execute(
            "select payload_redacted->>'output' from run_events where run_id=%s"
            " and type='span_close' and payload_redacted ? 'output' order by seq desc limit 1",
            (run_id,)).fetchone()
    keys = ["run_id", "parent_run_id", "status", "cost_usd", "input_tokens", "output_tokens",
            "steps", "latency_ms", "model", "error"]
    r = dict(zip(keys, row))
    r["output"] = out[0] if out else None
    r["pending_approval_id"] = pend[0] if pend else None
    return _as_result(r)


def _event_row(run_id: int, seq: int, type_: str, action: Any, allowed: Any,
               payload: Any, ts: Any) -> dict[str, Any]:
    payload = payload or None
    e = {"run_id": run_id, "seq": seq, "type": type_, "action": action, "allowed": allowed,
         "payload_redacted": payload, "ts": _iso(ts)}
    if type_ in ("span_open", "span_close") and isinstance(payload, dict):
        e["span_id"] = payload.get("span_id")
        e["parent_span_id"] = payload.get("parent_span_id")
    if e["ts"] is None:
        del e["ts"]
    return e


@router.get("/v0/runs/{run_id}/trace")
async def get_trace(run_id: int) -> dict[str, Any]:
    if not database_url():
        r = MEMORY.get(run_id)
        if not r:
            raise HTTPException(404, f"run {run_id} not found")
        children = [k for k, v in MEMORY.items() if v.get("parent_run_id") == run_id]
        return {"run_id": run_id, "children": children,
                "events": [_event_row(run_id, e["seq"], e["type"], e["action"], e["allowed"],
                                      e["payload_redacted"], None) for e in r["events"]]}
    with _conn() as c:
        rows = c.execute("select seq, type, action, allowed, payload_redacted, ts from run_events"
                         " where run_id=%s order by seq", (run_id,)).fetchall()
        children = [x[0] for x in c.execute(
            "select id from runs where parent_run_id=%s order by started_at", (run_id,)).fetchall()]
    return {"run_id": run_id, "children": children,
            "events": [_event_row(run_id, *row) for row in rows]}


@router.get("/v0/approvals")
async def list_approvals(status: Literal["pending", "decided", "all"] = "pending",
                         x_reagent_user: str | None = Header(default=None)) -> dict[str, Any]:
    if not database_url():
        items = [a for a in MEMORY_APPROVALS.values()
                 if status == "all" or (a["decision"] is None) == (status == "pending")]
        return {"approvals": [{**{k: _iso(v) for k, v in a.items()},
                               "agent": MEMORY.get(a["run_id"], {}).get("agent"),
                               "team": MEMORY.get(a["run_id"], {}).get("team"),
                               "requested_at": _iso(dt.datetime.now(dt.timezone.utc))}
                              for a in items]}
    where = {"pending": "ap.decision is null", "decided": "ap.decision is not null", "all": "true"}[status]
    sql = ("select ap.id, ap.run_id, a.name, t.name, ap.action, ap.args_redacted, ap.requested_at,"
           " ap.expires_at, ap.decision, u.name, ap.decided_at"
           " from approvals ap"
           " left join lateral (select agent_version_id, team_id from runs where id=ap.run_id limit 1) r on true"
           " left join agent_versions av on av.id=r.agent_version_id"
           " left join agents a on a.id=av.agent_id left join teams t on t.id=r.team_id"
           " left join users u on u.id=ap.approver_id"
           f" where {where}")
    args: list[Any] = []
    with _conn() as c:
        if x_reagent_user:
            # Filtered to teams the caller approves for; enterprise admins see all.
            me = c.execute("select id, is_enterprise_admin from users where name=%s",
                           (x_reagent_user,)).fetchone()
            if me and not me[1]:
                sql += (" and r.team_id in (select team_id from team_members"
                        " where user_id=%s and is_approver)")
                args.append(me[0])
        rows = c.execute(sql + " order by ap.requested_at desc limit 200", args).fetchall()
    from runtime.guardrails.permissions import connector_actions
    risk = {a["permission"]: a["risk"] for cdef in connector_actions()["connectors"].values()
            for a in cdef["actions"]}
    keys = ["id", "run_id", "agent", "team", "action", "args_redacted", "requested_at",
            "expires_at", "decision", "approver", "decided_at"]
    out = []
    for row in rows:
        d = {k: _iso(v) for k, v in zip(keys, row)}
        d["risk"] = risk.get(d["action"], "write")
        out.append(d)
    return {"approvals": out}


@router.post("/v0/approvals/{approval_id}")
async def decide(approval_id: int, body: Decision,
                 x_reagent_user: str | None = Header(default=None)) -> JSONResponse:
    from runtime.engine.recorder import Recorder
    from runtime.guardrails.redaction import Redactor
    rec = Recorder(Redactor())
    try:
        try:
            appr = rec.get_approval(approval_id)
        except KeyError:
            raise HTTPException(404, f"approval {approval_id} not found")
    finally:
        rec.close()
    if appr["decision"] == "expired":
        return JSONResponse({"detail": "approval expired; the action was denied"}, status_code=410)
    if appr["decision"] is not None:
        raise HTTPException(409, f"approval {approval_id} was already {appr['decision']}")
    exp = appr.get("expires_at")
    if exp is not None and exp < dt.datetime.now(dt.timezone.utc):
        from runtime.engine import aexpire_approval
        await aexpire_approval(approval_id)  # D16: expiry denies
        return JSONResponse({"detail": "approval expired; the action was denied"}, status_code=410)
    res = await adecide_approval(approval_id, decision=body.decision, approver=x_reagent_user)
    return JSONResponse(_as_result(res), status_code=200)


app = FastAPI(title="ReAgent runtime", version="0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
try:  # WF2's routes, when they exist
    from runtime.api.agents import router as _agents_router  # type: ignore
    app.include_router(_agents_router)
except Exception:  # noqa: BLE001
    pass
