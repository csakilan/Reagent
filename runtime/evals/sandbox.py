"""The mocked sandbox an eval case runs in. FROZEN D4.

`mocks[<qualified action>]` is the verbatim tool result returned to EVERY call
of that action, with no argument matching. Sub-agent calls (`agent:<name>`) are
served from the same mocks and never start a real run. An unmocked call returns
an error to the model and fails the case with E_UNMOCKED_ACTION.

The real runtime drives the run: its permission middleware, budget, step limit
and timeout are all in force. Only three seams are swapped, in-process, for the
duration of one run:

  binding._server_tools     every connector's tools become mock-backed (full
                            action set, so the drift check stays real)
  runner.make_agent_tools   sub-agents become mock-backed tools
  runner.load_resolved      the run uses the spec being evaluated, with
                            approvals auto-granted and recorded, so side
                            effects behind an approval can be graded

These are WF1 internals. A first-class `sandbox=` hook on run_agent would be
better; until then this module is the only place that touches them.
"""
from __future__ import annotations

import contextlib
import copy
import json
import threading
from typing import Any, Iterator

from langchain_core.tools import StructuredTool

from contracts.errors import Code

_LOCK = threading.Lock()                    # the seams are process-global

_ARGS = {"type": "object", "additionalProperties": True, "properties": {
    k: {"type": t} for k, t in {
        "repo": "string", "pr_number": "integer", "body": "string", "bucket": "string",
        "query": "string", "key": "string", "text": "string", "channel": "string",
        "ts": "string", "name": "string", "path": "string", "agent": "string",
        "mode": "string", "emoji": "string"}.items()}}


class Sandbox:
    def __init__(self, mocks: dict[str, Any]) -> None:
        self.mocks = mocks
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.unmocked: list[str] = []
        self.denied: list[str] = []
        self.approvals_granted: list[str] = []

    def answer(self, permission: str, args: dict[str, Any]) -> str:
        self.calls.append((permission, dict(args)))
        if permission not in self.mocks:
            self.unmocked.append(permission)
            return json.dumps({"error": f"{Code.UNMOCKED_ACTION.value}: {permission} has no mock "
                                        f"in this eval case"})
        value = self.mocks[permission]
        return value if isinstance(value, str) else json.dumps(value)


@contextlib.contextmanager
def sandboxed(resolved: dict[str, Any], mocks: dict[str, Any]) -> Iterator[Sandbox]:
    from runtime.engine import binding, runner
    from runtime.guardrails.permissions import agent_tool_name, connector_actions

    box = Sandbox(mocks)
    spec = copy.deepcopy(resolved)
    for t in spec["effective"]["tool_permissions"]:
        if t.get("requires_approval"):
            box.approvals_granted.append(t["permission"])
            t["requires_approval"] = False

    def tool(permission: str, name: str, description: str) -> StructuredTool:
        async def run(**kw: Any) -> str:
            return box.answer(permission, kw)
        return StructuredTool.from_function(coroutine=run, name=name, description=description,
                                            args_schema=_ARGS)

    async def server_tools(connector: str) -> list[StructuredTool]:
        c = connector_actions()["connectors"].get(connector) or {"actions": []}
        return [tool(a["permission"], a["mcp_tool"], f"{a['permission']} ({a['risk']})")
                for a in c["actions"]]

    def agent_tools(sub_agents: list[dict[str, Any]], ctx: Any, recorder: Any) -> list[StructuredTool]:
        return [tool(f"agent:{sa['name']}", agent_tool_name(sa["name"]),
                     f"[agent:{sa['name']}] Call the {sa['name']} agent with text=<content>.")
                for sa in sub_agents]

    base = runner.Recorder

    class CapturingRecorder(base):  # type: ignore[misc, valid-type]
        def event(self, type_: str, *args: Any, **kw: Any) -> Any:
            if type_ == "tool_call" and kw.get("allowed") is False and kw.get("action"):
                box.denied.append(kw["action"])
            return super().event(type_, *args, **kw)

    with _LOCK:
        saved = (binding._server_tools, runner.make_agent_tools, runner.load_resolved, runner.Recorder)
        binding._server_tools = server_tools
        runner.make_agent_tools = agent_tools
        runner.load_resolved = lambda name, **kw: copy.deepcopy(spec)
        runner.Recorder = CapturingRecorder
        try:
            yield box
        finally:
            (binding._server_tools, runner.make_agent_tools, runner.load_resolved,
             runner.Recorder) = saved
