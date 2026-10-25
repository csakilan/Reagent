"""LangChain v1 middleware for the runtime guardrails. WF1.

Read docs/research/langchain.md before changing any of this:
  - there is NO before_tool / after_tool hook. The pre-tool gate is after_model
    with can_jump_to=["end"]; per-call enforcement is wrap_tool_call.
  - the tool-list filter is advisory. PermissionMiddleware is the boundary.
  - cost arrives only on a completed call, so BudgetMiddleware halts at a turn
    boundary, never mid-generation.

Order in create_agent(middleware=[...]) matters: wrap_* nest first-listed
outermost. The runner lists Permission, then HITL, then ToolExecution, so a
denied call is refused before an approval is ever queued, and a tool only
counts as executed (allowed: true) once it has actually run.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from contracts.errors import Code, Rejection
from runtime.guardrails.permissions import GrantSet
from runtime.guardrails.redaction import Redactor


class RunState(AgentState):
    """Tracked (checkpointed) so totals survive an approval pause and resume."""

    cost_usd: NotRequired[float]
    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]
    model_calls: NotRequired[int]
    halted: NotRequired[str]          # an E_ code when a limit stopped the run


def model_cost(meta: dict[str, Any]) -> float:
    """Dollar cost of one completed call. Never usage_metadata (tokens only).

    Verified live 2026-10-25 against langchain-openai 1.7.2: a NON-streaming
    call (which is what create_agent makes) puts OpenRouter's cost at
    response_metadata["token_usage"]["cost"], not response_metadata["cost"]
    as docs/research/openrouter.md says. Read both, top level first."""
    for v in (meta.get("cost"), (meta.get("token_usage") or {}).get("cost")):
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _summary(content: Any, limit: int = 300) -> str:
    s = content if isinstance(content, str) else json.dumps(content, default=str)
    return s if len(s) <= limit else s[:limit] + f"... ({len(s)} chars)"


def _resource(args: dict[str, Any]) -> str | None:
    for k in ("repo", "channel", "bucket"):
        if isinstance(args.get(k), str):
            return args[k]
    return None


class PermissionMiddleware(AgentMiddleware):
    """The real boundary. Denies any call outside effective.tool_permissions
    (or inside effective.denylist, or outside a grant's resource scope),
    records a tool_call row with allowed=false, and tells the model why."""

    def __init__(self, grants: GrantSet, recorder: Any) -> None:
        super().__init__()
        self.grants = grants
        self.recorder = recorder

    def _deny(self, request: ToolCallRequest) -> ToolMessage | None:
        call = request.tool_call
        rej = self.grants.check(call["name"], call.get("args") or {})
        if rej is None:
            return None
        perm = self.grants.permission_for(call["name"])
        self.recorder.event("tool_call", action=perm, allowed=False, payload={
            "mcp_tool": call["name"], "args": call.get("args") or {},
            "resource": _resource(call.get("args") or {}),
            "denial_code": rej.code.value, "result_summary": rej.message})
        self.recorder.event("policy_violation", action=perm, payload={
            "code": rej.code.value, "field": rej.field, "blocked_by": rej.blocked_by,
            "message": rej.message})
        return ToolMessage(content=f"{rej.code.value}: {rej.message}", name=call["name"],
                           tool_call_id=call["id"], status="error")

    def wrap_tool_call(self, request: ToolCallRequest,
                       handler: Callable[[ToolCallRequest], Any]) -> Any:
        return self._deny(request) or handler(request)

    async def awrap_tool_call(self, request: ToolCallRequest,
                              handler: Callable[[ToolCallRequest], Awaitable[Any]]) -> Any:
        denied = self._deny(request)
        if denied is not None:
            return denied
        return await handler(request)


class ToolExecutionMiddleware(AgentMiddleware):
    """Innermost tool wrapper. Runs only once a call is permitted and, where
    needed, approved. Redacts the result BEFORE the model sees it (path 2 of
    4) and records the executed call."""

    def __init__(self, grants: GrantSet, recorder: Any, redactor: Redactor,
                 deadline: float | None = None) -> None:
        super().__init__()
        self.grants = grants
        self.recorder = recorder
        self.redactor = redactor
        # time.monotonic() deadline. RunControl drains only at a step boundary,
        # so a hung MCP call would sail past it; this bounds the call itself.
        self.deadline = deadline

    def _finish(self, request: ToolCallRequest, result: Any, started: float,
                error: str | None = None) -> Any:
        call = request.tool_call
        perm = self.grants.permission_for(call["name"])
        if isinstance(result, ToolMessage):
            clean, hits = self.redactor.redact(result.content)
            self.recorder.redaction_hits(hits, "tool_result")
            result.content = clean
            # MCP tool failures come back as content, not status="error".
            if error is None and (result.status == "error"
                                  or "Error executing tool" in _summary(clean, 400)):
                error = _summary(clean, 200)
            summary = _summary(clean)
        else:
            summary = type(result).__name__
        if perm.startswith("agent:"):
            return result  # the sub-agent tool records its own span
        self.recorder.event("tool_call", action=perm, allowed=True, payload={
            "mcp_tool": call["name"], "args": call.get("args") or {},
            "resource": _resource(call.get("args") or {}),
            "result_summary": summary, "duration_ms": int((time.monotonic() - started) * 1000),
            "error": error})
        return result

    def wrap_tool_call(self, request, handler):
        t = time.monotonic()
        return self._finish(request, handler(request), t)

    async def awrap_tool_call(self, request, handler):
        t = time.monotonic()
        if self.deadline is None:
            return self._finish(request, await handler(request), t)
        try:
            res = await asyncio.wait_for(handler(request), timeout=max(0.0, self.deadline - t))
        except (asyncio.TimeoutError, TimeoutError):
            call = request.tool_call
            res = ToolMessage(content=f"{Code.TIMEOUT.value}: the tool call ran past the run's "
                                      "timeout and was abandoned.",
                              name=call["name"], tool_call_id=call["id"], status="error")
            return self._finish(request, res, t, error=Code.TIMEOUT.value)
        return self._finish(request, res, t)


class ModelGuardMiddleware(AgentMiddleware):
    """Path 1 of 4: model output. Redacts the AI message text and the
    arguments of any tool call it requests, so a secret the model repeats can
    neither reach the user nor be posted to GitHub in a comment body."""

    def __init__(self, recorder: Any, redactor: Redactor) -> None:
        super().__init__()
        self.recorder = recorder
        self.redactor = redactor

    def _clean(self, response: Any) -> Any:
        msgs = response.result if isinstance(response, ModelResponse) else [response]
        for m in msgs:
            if not isinstance(m, AIMessage):
                continue
            if m.content:
                m.content, hits = self.redactor.redact(m.content)
                self.recorder.redaction_hits(hits, "model_output")
            for tc in m.tool_calls:
                tc["args"], hits = self.redactor.redact(tc.get("args") or {})
                self.recorder.redaction_hits(hits, "model_output")
        return response

    def wrap_model_call(self, request: ModelRequest, handler):
        return self._clean(handler(request))

    async def awrap_model_call(self, request: ModelRequest, handler):
        return self._clean(await handler(request))


class BudgetMiddleware(AgentMiddleware):
    """USD per-run budget. There is no built-in cost middleware.

    OpenRouter sends cost only in the final chunk of a call, so this sums
    response_metadata["cost"] per COMPLETED call in after_model and halts at
    that turn boundary with jump_to: end. It also records the model_call row.
    """

    state_schema = RunState

    def __init__(self, limit_usd: float | None, recorder: Any, model_id: str,
                 openrouter_model: str, limit_layer: str = "effective.limits",
                 ctx: Any = None) -> None:
        super().__init__()
        self.limit = limit_usd
        self.recorder = recorder
        self.model_id = model_id
        self.openrouter_model = openrouter_model
        self.limit_layer = limit_layer
        self.ctx = ctx  # RunCtx: shares spend with the sub-agent tool
        self._t0: float | None = None

    def before_model(self, state, runtime):
        self._t0 = time.monotonic()
        return None

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: RunState, runtime: Any) -> dict[str, Any] | None:
        last = state["messages"][-1]
        meta = getattr(last, "response_metadata", {}) or {}
        usage = getattr(last, "usage_metadata", None) or {}
        cost = model_cost(meta)
        total = float(state.get("cost_usd") or 0.0) + cost
        upd: dict[str, Any] = {
            "cost_usd": total,
            "input_tokens": int(state.get("input_tokens") or 0) + int(usage.get("input_tokens") or 0),
            "output_tokens": int(state.get("output_tokens") or 0) + int(usage.get("output_tokens") or 0),
            "model_calls": int(state.get("model_calls") or 0) + 1,
        }
        child = float(getattr(self.ctx, "child_cost_usd", 0.0) or 0.0)
        if self.ctx is not None:
            self.ctx.spent_usd = total
            self.ctx.steps = upd["model_calls"]
        served = str(meta.get("model_name") or self.openrouter_model)
        self.recorder.event("model_call", payload={
            "model": self.model_id, "openrouter_model": served,
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cost_usd": cost, "cumulative_cost_usd": round(total, 8),
            "latency_ms": int((time.monotonic() - self._t0) * 1000) if self._t0 else 0,
            "fallback_used": bool(served and self.openrouter_model
                                  and not served.startswith(self.openrouter_model.split(":")[0])
                                  and not served.startswith("fake")),
            "finish_reason": str(meta.get("finish_reason") or ""),
        })
        if self.limit is not None and total + child > self.limit:
            rej = Rejection(Code.BUDGET_EXCEEDED, field="limits.usd_per_run",
                            blocked_by=self.limit_layer, attempted=round(total + child, 6),
                            allowed=self.limit)
            self.recorder.event("policy_violation", payload={
                "code": rej.code.value, "field": rej.field, "blocked_by": rej.blocked_by,
                "message": rej.message})
            upd.update(halted=rej.code.value, jump_to="end",
                       messages=[AIMessage(content=f"{rej.code.value}: {rej.message}")])
        return upd

    @hook_config(can_jump_to=["end"])
    async def aafter_model(self, state: RunState, runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)
