"""Run an agent from its resolved spec, record it, pause for approval, resume. WF1.

Entry points (sync wrappers in runtime/engine/__init__.py):
  arun_agent(name, input, ...)            -> result dict
  adecide_approval(id, decision, approver) -> result dict
  aexpire_approval(id)                     -> result dict

Limits (effective.limits is already the minimum across layers; not re-derived):
  steps    ModelCallLimitMiddleware(thread_limit=..., exit_behavior="end"). The
           thread counter is checkpointed, so it survives an approval resume.
  budget   BudgetMiddleware, per completed model call (turn boundary).
  timeout  RunControl + watchdog thread -> request_drain("timeout") ->
           GraphDrained. A step-boundary check alone would let one slow MCP
           call blow straight past the deadline.
Aggregates (usd_per_month, max_concurrent_runs) are checked BEFORE the run by
summing the runs table per layer (FROZEN D7).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import threading
import time
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (HumanInTheLoopMiddleware, ModelCallLimitMiddleware,
                                         ModelFallbackMiddleware)
from langchain_core.messages import AIMessage
from langgraph.errors import GraphDrained
from langgraph.runtime import RunControl
from langgraph.types import Command

from contracts.errors import Code, ReAgentError, Rejection
from runtime.engine.binding import build_model, load_tools
from runtime.engine.recorder import Recorder, database_url
from runtime.guardrails.middleware import (BudgetMiddleware, ModelGuardMiddleware,
                                           PermissionMiddleware, ToolExecutionMiddleware)
from runtime.guardrails.permissions import GrantSet
from runtime.guardrails.redaction import Redactor, install_log_redaction
from runtime.subagents import ParentCtx, RunCtx, make_agent_tools

log = logging.getLogger("runtime.engine")
install_log_redaction()

STEP_LIMIT_MARKER = "Model call limits exceeded"


# --------------------------------------------------------------------------
# Resolved spec
# --------------------------------------------------------------------------

def load_resolved(name: str, *, root_team: str | None = None,
                  trigger: str | None = None) -> dict[str, Any]:
    """The real resolver (H1), else the golden fixtures.

    Resolves as the system principal (caller=None): the runtime needs the
    UNREDACTED effective spec, since it must send the enterprise prompt to the
    model, and a member's view nulls enterprise values (FROZEN D3). The end
    user is never the resolver's caller here. `root_team` carries the root
    run's team down a sub-agent chain (D1); `trigger` lets the resolver refuse
    on_behalf_of tools on user-less triggers (D2).
    Set REAGENT_RESOLVER=fake to force fixtures."""
    if os.environ.get("REAGENT_RESOLVER", "auto") != "fake":
        try:
            from runtime.resolver import resolve_agent  # type: ignore
        except ImportError:
            resolve_agent = None
        if resolve_agent is not None:
            try:
                doc = resolve_agent(name, caller=None, root_team=root_team, trigger=trigger)
            except LookupError as e:
                raise ReAgentError(Rejection(Code.SUBAGENT_NOT_CALLABLE, field="agent",
                                             blocked_by="registry", attempted=name)) from e
            if doc.get("rejections"):
                raise ReAgentError(*[_rejection(r) for r in doc["rejections"]])
            return doc
    from runtime.engine.fakes import resolved_spec
    doc = resolved_spec(name)
    doc["_from_fixture"] = True
    return doc


def _rejection(r: dict[str, Any]) -> Rejection:
    try:
        code = Code(r["code"])
    except ValueError:
        code = Code.SCHEMA_INVALID
    return Rejection(code, field=r.get("field", "?"), blocked_by=r.get("blocked_by", "?"),
                     attempted=r.get("attempted"), allowed=r.get("allowed"))


# --------------------------------------------------------------------------
# Checkpointer: Postgres when there is a database, else one in-process saver
# --------------------------------------------------------------------------

_MEMORY_SAVER = None


class _Checkpointer:
    """async context manager yielding a checkpointer. AsyncPostgresSaver with
    setup() run once per process; InMemorySaver only without a database, in
    which case resume works within this process and nowhere else.

    `durable=False` is for runs that can never pause: no approval-gated tool
    means no interrupt, so nothing ever resumes from the checkpoint. Those get
    a throwaway InMemorySaver, which saves several Tiger round trips per graph
    step (~100ms each from the venue). Anything that CAN pause stays on
    Postgres with durability="sync"."""

    _setup_done = False

    def __init__(self, durable: bool = True) -> None:
        self.durable = durable

    async def __aenter__(self):
        url = database_url()
        if url and not self.durable:
            from langgraph.checkpoint.memory import InMemorySaver
            self._cm = None
            return InMemorySaver()
        if url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            self._cm = AsyncPostgresSaver.from_conn_string(url)
            saver = await self._cm.__aenter__()
            if not _Checkpointer._setup_done:
                await saver.setup()
                _Checkpointer._setup_done = True
            return saver
        global _MEMORY_SAVER
        if _MEMORY_SAVER is None:
            from langgraph.checkpoint.memory import InMemorySaver
            _MEMORY_SAVER = InMemorySaver()
        self._cm = None
        return _MEMORY_SAVER

    async def __aexit__(self, *exc):
        if self._cm is not None:
            await self._cm.__aexit__(*exc)


# --------------------------------------------------------------------------
# Building one run's graph
# --------------------------------------------------------------------------

def _min(*vals: Any) -> Any:
    xs = [v for v in vals if v is not None]
    return min(xs) if xs else None


class _Built:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


async def _build(spec: dict[str, Any], recorder: Recorder, ctx: RunCtx, checkpointer: Any,
                 grants: GrantSet) -> _Built:
    eff = spec["effective"]
    model_cfg = eff.get("model") or {}
    prompt = eff.get("prompt")
    if prompt is None:
        raise ReAgentError(Rejection(Code.SCHEMA_INVALID, field="effective.prompt",
                                     blocked_by="resolver", attempted="null (redacted view)"))
    tools = await load_tools(grants)
    tools += make_agent_tools(eff.get("sub_agents") or [], ctx, recorder)

    has_tools = bool(tools)
    prompt = prompt + tool_map_note(grants, eff.get("sub_agents") or [])
    model = build_model(model_cfg, has_tools=has_tools)
    budget = BudgetMiddleware(ctx.limit_usd, recorder, model_cfg.get("id", "?"),
                              model_cfg.get("openrouter_model", "?"), ctx=ctx)
    middleware: list[Any] = [ModelGuardMiddleware(recorder, recorder.redactor)]
    fb = build_model(model_cfg, fallback=True, has_tools=has_tools)
    if fb is not None and model_cfg.get("fallback_openrouter_model") != model_cfg.get("openrouter_model"):
        middleware.append(ModelFallbackMiddleware(fb))
    middleware.append(budget)
    if ctx.limit_steps:
        middleware.append(ModelCallLimitMiddleware(thread_limit=ctx.limit_steps, exit_behavior="end"))
    middleware.append(PermissionMiddleware(grants, recorder))
    gated = {g.tool: {"allowed_decisions": ["approve", "reject"]}
             for g in grants.grants.values() if g.requires_approval}
    if gated:
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=gated, interrupt_mode="per_call",
                                                   description_prefix="Approval required"))
    middleware.append(ToolExecutionMiddleware(grants, recorder, recorder.redactor, ctx.deadline))

    agent = create_agent(model, tools=tools, system_prompt=prompt, middleware=middleware,
                         checkpointer=checkpointer, name=spec.get("agent"))
    return _Built(agent=agent, grants=grants, model_cfg=model_cfg)


def tool_map_note(grants: GrantSet, sub_agents: list[dict[str, Any]]) -> str:
    """Prompts name permissions (`github:pulls.comment`) but tool names cannot
    contain dots or colons, so the model sees `github__pulls_comment`. Without
    this map a live model wrote "[Comment on ...]" as text instead of calling
    the tool. Appended to the SAME single system message (FROZEN D21 keeps one
    system message; this is a platform-written tail after the three layers)."""
    from runtime.guardrails.permissions import agent_tool_name
    pairs = [(g.permission, g.tool) for g in grants.grants.values()]
    pairs += [(f"agent:{sa['name']}", agent_tool_name(sa["name"])) for sa in sub_agents]
    if not pairs:
        return ""
    return ("\n\nTool names: when these instructions name a permission, call the matching "
            "tool. " + "; ".join(f"{a} -> {b}" for a, b in pairs) + ".")


def _grants_for(spec: dict[str, Any], parent: ParentCtx | None) -> GrantSet:
    grants = GrantSet.from_effective(spec["effective"])
    # FROZEN D1: an enterprise agent inside a team's run is cut down to the
    # root run's grants, computed once at the root and carried down.
    # The real resolver already applies this via root_team; the fixture path
    # approximates the team's grants with the root agent's own permissions.
    if parent is not None and spec.get("level") == "enterprise" and spec.get("_from_fixture"):
        grants = grants.intersect_connectors(parent.root_permissions)
    return grants


# --------------------------------------------------------------------------
# Pre-run refusals
# --------------------------------------------------------------------------

def _prerun_checks(spec: dict[str, Any], recorder: Recorder, grants: GrantSet,
                   parent: ParentCtx | None, user: str | None, trigger: str) -> None:
    eff = spec["effective"]
    lim = eff.get("limits") or {}
    if parent is not None:
        max_depth = _min(lim.get("max_call_depth"), 3)
        if parent.depth + 1 > max_depth:
            raise ReAgentError(Rejection(Code.CALL_DEPTH_EXCEEDED, field="limits.max_call_depth",
                                         blocked_by="effective.limits", attempted=parent.depth + 1,
                                         allowed=max_depth))
        if spec.get("agent") in parent.chain:
            raise ReAgentError(Rejection(Code.SUBAGENT_CYCLE, field="sub_agents",
                                         blocked_by="runtime", attempted=" -> ".join(
                                             parent.chain + (spec["agent"],))))
    # FROZEN D2: on_behalf_of needs a principal from the session.
    if not user:
        for g in grants.grants.values():
            if g.auth == "on_behalf_of":
                raise ReAgentError(Rejection(Code.ON_BEHALF_OF_NO_USER, field=g.permission,
                                             blocked_by="runtime", attempted=trigger))
    # FROZEN D7: aggregates, per layer, summed from runs. Root runs only: a
    # child's spend is part of its root run's.
    if parent is None:
        usage = recorder.aggregate_usage(spec["agent"], spec.get("team"))
        for layer, agg in (eff.get("aggregate_limits") or {}).items():
            agg = agg or {}
            used = usage.get(layer) or {"usd_month": 0.0, "running": 0}
            label = f"team:{spec.get('team')}" if layer == "team" else layer
            cap = agg.get("usd_per_month")
            if cap is not None and used["usd_month"] >= float(cap):
                raise ReAgentError(Rejection(Code.AGGREGATE_BUDGET_EXCEEDED, field="limits.usd_per_month",
                                             blocked_by=label, attempted=round(used["usd_month"], 4),
                                             allowed=cap))
            conc = agg.get("max_concurrent_runs")
            if conc is not None and used["running"] >= int(conc):
                raise ReAgentError(Rejection(Code.CONCURRENCY_EXCEEDED, field="limits.max_concurrent_runs",
                                             blocked_by=label, attempted=used["running"], allowed=conc))


# --------------------------------------------------------------------------
# Execute and finalise
# --------------------------------------------------------------------------

async def _invoke(agent: Any, payload: Any, config: dict[str, Any], ctx: RunCtx) -> tuple[Any, str | None]:
    """Run the graph under a wall-clock watchdog. Returns (output, drain_reason)."""
    control = RunControl()  # fresh per invocation; a drained control stays drained
    timer = None
    if ctx.deadline is not None:
        remaining = max(0.0, ctx.deadline - time.monotonic())
        timer = threading.Timer(remaining, control.request_drain, args=("timeout",))
        timer.daemon = True
        timer.start()
    try:
        out = await agent.ainvoke(payload, config, durability="sync", control=control, version="v2")
        return out, None
    except GraphDrained:
        return None, control.drain_reason or "timeout"
    finally:
        if timer is not None:
            timer.cancel()


def _final_text(messages: list[Any]) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls and m.content:
            return m.content if isinstance(m.content, str) else json.dumps(m.content)
    return ""


async def _finalise(built: _Built, spec: dict[str, Any], recorder: Recorder, ctx: RunCtx,
                    config: dict[str, Any], out: Any, drained: str | None) -> dict[str, Any]:
    state = (await built.agent.aget_state(config)).values
    own_cost = float(state.get("cost_usd") or 0.0)
    cost = own_cost + ctx.child_cost_usd
    steps = int(state.get("model_calls") or 0)
    tokens_in, tokens_out = int(state.get("input_tokens") or 0), int(state.get("output_tokens") or 0)
    lim = spec["effective"].get("limits") or {}
    result: dict[str, Any] = {"run_id": ctx.run_id, "agent": spec["agent"],
                              "version": spec.get("version"), "cost_usd": round(cost, 6),
                              "steps": steps, "error": None, "output": None,
                              "children": list(ctx.children), "input_tokens": tokens_in,
                              "output_tokens": tokens_out,
                              "model": built.model_cfg.get("openrouter_model")}
    interrupts = list(out.interrupts) if out is not None else []
    if drained:
        rej = Rejection(Code.TIMEOUT, field="limits.timeout_seconds", blocked_by="effective.limits",
                        attempted=lim.get("timeout_seconds"), allowed=lim.get("timeout_seconds"))
        recorder.event("policy_violation", payload={"code": rej.code.value, "field": rej.field,
                                                    "blocked_by": rej.blocked_by, "message": rej.message})
        result.update(status="failed", error=f"{rej.code.value}: {rej.message}")
    elif interrupts:
        approvals = []
        timeout_min = (spec["effective"].get("guardrails") or {}).get("approval_timeout_minutes")
        for intr in interrupts:
            v = intr.value if isinstance(intr.value, dict) else {}
            perm = built.grants.permission_for(v.get("name", ""))
            g = built.grants.grants.get(v.get("name", ""))
            existing = recorder.pending_approval(ctx.run_id, perm)
            if existing is not None:
                approvals.append(existing)
                continue
            expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=timeout_min)
                       if timeout_min else None)
            aid = recorder.create_approval(perm, v.get("args") or {}, expires)
            recorder.event("approval_requested", action=perm, payload={
                "approval_id": aid, "action": perm, "risk": g.risk if g else "write",
                "expires_at": expires.isoformat() if expires else None})
            approvals.append(aid)
        result.update(status="awaiting_approval", approvals=approvals)
    elif state.get("halted"):
        result.update(status="failed", error=_final_text(state["messages"]))
    else:
        text = _final_text(state["messages"])
        if text.startswith(STEP_LIMIT_MARKER):
            rej = Rejection(Code.STEP_LIMIT_EXCEEDED, field="limits.max_steps",
                            blocked_by="effective.limits", attempted=steps, allowed=lim.get("max_steps"))
            recorder.event("policy_violation", payload={"code": rej.code.value, "field": rej.field,
                                                        "blocked_by": rej.blocked_by,
                                                        "message": f"{rej.code.value}: max_steps reached"})
            result.update(status="failed", error=f"{rej.code.value}: {text}")
        else:
            result.update(status="succeeded")
        result["output"] = recorder.redactor.redact_text(text)[0]  # belt and braces
    recorder.finish_run(status=result["status"], cost_usd=cost, input_tokens=tokens_in,
                        output_tokens=tokens_out, steps=steps, error=result["error"],
                        output=result["output"])
    if result["status"] != "awaiting_approval":
        recorder.span_close(ctx.span_id, spec["agent"], parent=ctx.parent_span_id,
                            status=result["status"], cost_usd=round(cost, 6),
                            output=(result["output"] or "")[:8000], error=result["error"])
    return result


def _refused(recorder: Recorder, spec: dict[str, Any] | None, err: ReAgentError,
             status: str = "denied") -> dict[str, Any]:
    for r in err.rejections:
        if recorder.run_id is not None:
            recorder.event("policy_violation", payload={"code": r.code.value, "field": r.field,
                                                        "blocked_by": r.blocked_by, "message": r.message})
    msg = "; ".join(f"{r.code.value}: {r.message}" for r in err.rejections)
    if recorder.run_id is not None:
        recorder.finish_run(status=status, cost_usd=0.0, input_tokens=0, output_tokens=0,
                            steps=0, error=msg)
    return {"run_id": recorder.run_id, "agent": (spec or {}).get("agent"), "status": status,
            "error": msg, "output": None, "cost_usd": 0.0, "steps": 0,
            "rejections": [r.to_dict() for r in err.rejections]}


def _input_message(input: Any) -> dict[str, Any]:
    # FROZEN D21: run input is always a separate user message.
    content = input if isinstance(input, str) else json.dumps(input, default=str)
    return {"messages": [{"role": "user", "content": content}]}


# --------------------------------------------------------------------------
# Public async API
# --------------------------------------------------------------------------

async def arun_agent(name: str, input: Any, *, user: str | None = None, trigger: str = "http",
                     budget_override: dict[str, Any] | None = None,
                     _parent: ParentCtx | None = None,
                     on_start: Any = None) -> dict[str, Any]:
    """`on_start(run_id)` fires once the runs row exists, so the API can answer
    202 with a run id while the run carries on in the background."""
    try:
        spec = load_resolved(name, root_team=_parent.root_team if _parent else None,
                             trigger=trigger)
    except ReAgentError as e:
        return _refused(Recorder(Redactor()), {"agent": name}, e)
    eff = spec["effective"]
    recorder = Recorder(Redactor.from_effective(eff))
    lim = dict(eff.get("limits") or {})
    for k, v in (budget_override or {}).items():
        lim[k] = _min(lim.get(k), v)  # an override may only tighten
    user = user or (_parent.user if _parent else None)
    p = _parent
    limit_usd = _min(lim.get("usd_per_run"), p.remaining_usd if p else None)
    limit_steps = _min(lim.get("max_steps"), p.remaining_steps if p else None)
    deadline = time.monotonic() + float(lim["timeout_seconds"]) if lim.get("timeout_seconds") else None
    deadline = _min(deadline, p.deadline if p else None)

    model_cfg = eff.get("model") or {}
    grants = _grants_for(spec, p)
    try:
        recorder.start_run(agent=spec["agent"], version=spec.get("version") or "0.0.0",
                           level=spec.get("level") or "team", team=spec.get("team"),
                           access=spec.get("access"), trigger=trigger,
                           model=model_cfg.get("openrouter_model"),
                           parent_run_id=p.run_id if p else None, user=user)
        if on_start is not None:
            on_start(recorder.run_id)
        root_span = recorder.span_open(spec["agent"], parent=p.span_id if p else None,
                                       agent=spec["agent"], version=spec.get("version"),
                                       trigger=trigger)
        ctx = RunCtx(run_id=recorder.run_id, span_id=root_span, depth=(p.depth + 1) if p else 0,
                     limit_usd=limit_usd, limit_steps=limit_steps, deadline=deadline,
                     root_permissions=p.root_permissions if p else grants.permissions(),
                     root_team=p.root_team if p else spec.get("team"), user=user,
                     chain=(p.chain if p else ()) + (spec["agent"],),
                     parent_span_id=p.span_id if p else None)
        _prerun_checks(spec, recorder, grants, p, user, trigger)
        can_pause = any(g.requires_approval for g in grants.grants.values())
        async with _Checkpointer(durable=can_pause) as saver:
            built = await _build(spec, recorder, ctx, saver, grants)
            config = {"configurable": {"thread_id": f"run-{ctx.run_id}"},
                      "recursion_limit": max(25, (limit_steps or 25) * 4 + 10)}
            out, drained = await _invoke(built.agent, _input_message(input), config, ctx)
            return await _finalise(built, spec, recorder, ctx, config, out, drained)
    except ReAgentError as e:
        return _refused(recorder, spec, e)
    except Exception as e:  # noqa: BLE001 — a run must always end recorded
        log.exception("run %s failed", recorder.run_id)
        msg = recorder.redactor.redact_text(f"{type(e).__name__}: {e}")[0][:500]
        if recorder.run_id is not None:
            recorder.event("error", payload={"code": Code.MODEL_ERROR.value, "message": msg,
                                             "fatal": True})
            recorder.finish_run(status="failed", cost_usd=0.0, input_tokens=0, output_tokens=0,
                                steps=0, error=msg)
        return {"run_id": recorder.run_id, "agent": name, "status": "failed", "error": msg,
                "output": None, "cost_usd": 0.0, "steps": 0}
    finally:
        recorder.close()


async def _resume(approval: dict[str, Any], decision: str, recorder: Recorder) -> dict[str, Any]:
    run_id = approval["run_id"]
    name, _version = recorder.agent_for_run(run_id)
    spec = load_resolved(name)
    recorder.redactor = Redactor.from_effective(spec["effective"])
    recorder.attach(run_id)
    lim = spec["effective"].get("limits") or {}
    prior = recorder.run_status(run_id)
    grants = _grants_for(spec, None)
    ctx = RunCtx(run_id=run_id, span_id=f"resume-{run_id}", depth=0,
                 limit_usd=lim.get("usd_per_run"), limit_steps=lim.get("max_steps"),
                 deadline=time.monotonic() + float(lim.get("timeout_seconds") or 240),
                 root_permissions=grants.permissions(), root_team=spec.get("team"),
                 user=None, chain=(name,))
    async with _Checkpointer() as saver:
        built = await _build(spec, recorder, ctx, saver, grants)
        config = {"configurable": {"thread_id": f"run-{run_id}"},
                  "recursion_limit": max(25, (lim.get("max_steps") or 25) * 4 + 10)}
        snap = await built.agent.aget_state(config)
        # The child cost already rolled into runs.cost_usd; own cost lives in
        # the checkpointed state, so only carry the difference.
        ctx.child_cost_usd = max(0.0, float(prior.get("cost_usd") or 0.0)
                                 - float(snap.values.get("cost_usd") or 0.0))
        answers = {}
        for intr in snap.interrupts:
            v = intr.value if isinstance(intr.value, dict) else {}
            if built.grants.permission_for(v.get("name", "")) == approval["action"]:
                answers[intr.id] = {"type": "approve"} if decision == "approved" else \
                    {"type": "reject", "message": "A human reviewer rejected this action."}
        if not answers:
            raise RuntimeError(f"run {run_id} has no pending interrupt for {approval['action']}")
        recorder.event("span_open", payload={"span_id": ctx.span_id, "parent_span_id": None,
                                             "name": f"{name} (resumed)"})
        out, drained = await _invoke(built.agent, Command(resume=answers), config, ctx)
        return await _finalise(built, spec, recorder, ctx, config, out, drained)


async def adecide_approval(approval_id: int, decision: str = "approved",
                           approver: str | None = None) -> dict[str, Any]:
    """Approve resumes the paused run from its checkpoint, in any process.
    Reject and expire DENY: the action never runs and the run fails (D16)."""
    if decision not in ("approved", "rejected", "expired"):
        raise ValueError(f"decision must be approved|rejected|expired, got {decision!r}")
    recorder = Recorder(Redactor())
    try:
        appr = recorder.get_approval(approval_id)
        if appr["decision"] is not None:
            raise ValueError(f"approval {approval_id} was already decided: {appr['decision']}")
        recorder.attach(appr["run_id"])
        recorder.decide(approval_id, decision, approver if decision != "expired" else None)
        recorder.event("approval_decided", action=appr["action"], payload={
            "approval_id": approval_id, "decision": decision,
            "approver": approver if decision != "expired" else None})
        if decision == "approved":
            return await _resume(appr, decision, recorder)
        code = Code.APPROVAL_EXPIRED if decision == "expired" else Code.APPROVAL_REJECTED
        rej = Rejection(code, field=appr["action"], blocked_by="approver",
                        attempted=appr["action"], allowed="?")
        msg = rej.message if code == Code.APPROVAL_EXPIRED else \
            f"{appr['action']} was rejected by {approver or 'a reviewer'}; the action was not performed."
        recorder.event("policy_violation", action=appr["action"], payload={
            "code": code.value, "field": appr["action"], "blocked_by": "approver",
            "message": msg})
        prior = recorder.run_status(appr["run_id"])
        status = "failed" if code == Code.APPROVAL_EXPIRED else "denied"
        recorder.finish_run(status=status, cost_usd=float(prior.get("cost_usd") or 0.0),
                            input_tokens=0, output_tokens=0, steps=int(prior.get("steps") or 0),
                            error=f"{code.value}: {msg}")
        return {"run_id": appr["run_id"], "status": status, "error": f"{code.value}: {msg}",
                "approval_id": approval_id}
    finally:
        recorder.close()


async def aexpire_approval(approval_id: int) -> dict[str, Any]:
    return await adecide_approval(approval_id, decision="expired", approver=None)
