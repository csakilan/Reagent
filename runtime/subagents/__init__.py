"""Agents calling agents (SPEC §8, FROZEN D1). WF1.

Each `sub_agents` entry becomes a tool. The permission string is
`agent:<name>`; the tool name the model sees is `agent__<name>` because colons
are illegal in tool names.

  - the callee runs with its own resolved permissions, INTERSECTED with the
    root run's grants (D1: this is where SPEC §8.1 is wrong). The root set is
    computed once and carried down the whole chain.
  - limits flow down as min(own, caller's remaining) for budget, steps, time.
  - depth is capped at max_call_depth. Cycles are rejected at resolve time; a
    cycle that slips through still dies on the depth cap.
  - the child is its own `runs` row with parent_run_id, and its cost rolls up.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import StructuredTool

from runtime.guardrails.permissions import agent_tool_name


@dataclass
class ParentCtx:
    run_id: int
    span_id: str | None
    depth: int
    remaining_usd: float | None
    remaining_steps: int | None
    deadline: float | None            # time.monotonic() deadline
    root_permissions: set[str] | None  # D1: carried from the root run
    root_team: str | None
    user: str | None
    chain: tuple[str, ...] = ()
    connector_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class RunCtx:
    """Mutable per-run state the sub-agent tool needs from its caller."""

    run_id: int
    span_id: str
    depth: int
    limit_usd: float | None
    limit_steps: int | None
    deadline: float | None
    root_permissions: set[str] | None
    root_team: str | None
    user: str | None
    chain: tuple[str, ...]
    parent_span_id: str | None = None
    connector_headers: dict[str, str] = field(default_factory=dict)  # root run's, never per child
    spent_usd: float = 0.0            # own model cost so far, updated by the budget hook
    child_cost_usd: float = 0.0
    steps: int = 0
    children: list[int] = field(default_factory=list)

    def remaining_usd(self) -> float | None:
        if self.limit_usd is None:
            return None
        return max(0.0, self.limit_usd - self.spent_usd - self.child_cost_usd)

    def as_parent(self) -> ParentCtx:
        return ParentCtx(
            run_id=self.run_id, span_id=self.span_id, depth=self.depth,
            remaining_usd=self.remaining_usd(),
            remaining_steps=None if self.limit_steps is None else max(1, self.limit_steps - self.steps),
            deadline=self.deadline, root_permissions=self.root_permissions,
            root_team=self.root_team, user=self.user, chain=self.chain,
            connector_headers=dict(self.connector_headers))


def make_agent_tools(sub_agents: list[dict[str, Any]], ctx: RunCtx, recorder: Any) -> list[StructuredTool]:
    from runtime.engine.runner import arun_agent  # late: runner imports this module

    tools = []
    for sa in sub_agents:
        name = sa["name"]

        async def call(_name: str = name, **kwargs: Any) -> str:
            payload = kwargs.get("input") if set(kwargs) == {"input"} else kwargs
            action = f"agent:{_name}"
            sid = recorder.span_open(action, parent=ctx.span_id, action=action)
            t0 = time.monotonic()
            res = await arun_agent(_name, payload, trigger="agent", _parent=ctx.as_parent())
            cost = float(res.get("cost_usd") or 0.0)
            ctx.child_cost_usd += cost
            if res.get("run_id"):
                ctx.children.append(res["run_id"])
            ok = res.get("status") == "succeeded"
            recorder.event("tool_call", action=action, allowed=True, payload={
                "mcp_tool": agent_tool_name(_name), "args": payload,
                "result_summary": str(res.get("output") or res.get("error") or "")[:300],
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "error": None if ok else res.get("error")})
            recorder.span_close(sid, action, parent=ctx.span_id, action=action,
                                child_run_id=res.get("run_id"), status=res.get("status"),
                                cost_usd=cost)
            out = res.get("output")
            if not ok:
                return json.dumps({"error": res.get("error"), "status": res.get("status")})
            return out if isinstance(out, str) else json.dumps(out)

        tools.append(StructuredTool.from_function(
            coroutine=call, name=agent_tool_name(name),
            description=(f"[agent:{name}] Call the {name} agent. Pass its input fields as "
                         f"arguments, e.g. text=<the content to examine>."),
            args_schema={"type": "object", "properties": {
                "text": {"type": "string", "description": "Content for the agent to work on"}},
                "required": ["text"]}))
    return tools
