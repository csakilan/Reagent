"""Permission model built from effective.tool_permissions. WF1.

`effective.tool_permissions` is the complete and only source of what an agent
may call (FROZEN D12: memory is already expanded into it). This module is pure
Python; the LangChain middleware that enforces it lives in middleware.py.

Two layers, both required (docs/research/langchain.md):
  1. filter the loaded tool list, which only keeps ungranted tools out of the
     model's context. Advisory.
  2. deny in wrap_tool_call. This is the boundary.
"""
from __future__ import annotations

import fnmatch
import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from contracts.errors import Code, ReAgentError, Rejection

ROOT = pathlib.Path(__file__).resolve().parents[2]
_ACTIONS: dict[str, Any] | None = None


def connector_actions() -> dict[str, Any]:
    """contracts/connector_actions.json. Never recompute the dot→underscore map."""
    global _ACTIONS
    if _ACTIONS is None:
        _ACTIONS = json.loads((ROOT / "contracts" / "connector_actions.json").read_text())
    return _ACTIONS


def wire_map(connector: str) -> dict[str, str]:
    """mcp_tool -> permission for one connector, from the contract."""
    c = connector_actions()["connectors"].get(connector) or {}
    return {a["mcp_tool"]: a["permission"] for a in c.get("actions", [])}


def tool_name(connector: str, mcp_tool: str) -> str:
    """The name the model sees. Namespaced by connector because MCP wire names
    collide across servers (memory `read` vs anything else called `read`).
    Built from mcp_tool, never from the dotted action."""
    return f"{connector}__{mcp_tool}"


def agent_tool_name(agent: str) -> str:
    """`agent:<name>` is the permission string; colons are illegal in tool names."""
    return "agent__" + agent.replace(":", "_")


@dataclass(frozen=True)
class Grant:
    permission: str            # github:pulls.comment
    connector: str
    action: str
    mcp_tool: str
    resources: dict[str, list[str]]
    risk: str
    requires_approval: bool
    auth: str = "service"

    @property
    def tool(self) -> str:
        return tool_name(self.connector, self.mcp_tool)


@dataclass
class GrantSet:
    grants: dict[str, Grant] = field(default_factory=dict)       # keyed by tool name
    denylist: list[str] = field(default_factory=list)
    sub_agents: dict[str, str] = field(default_factory=dict)     # tool name -> agent name

    @classmethod
    def from_effective(cls, effective: dict[str, Any]) -> "GrantSet":
        gs = cls(denylist=list(effective.get("denylist") or []))
        for t in effective.get("tool_permissions") or []:
            g = Grant(
                permission=t["permission"], connector=t["connector"], action=t["action"],
                mcp_tool=t["mcp_tool"], resources=dict(t.get("resources") or {}),
                risk=t.get("risk", "read"), requires_approval=bool(t.get("requires_approval")),
                auth=t.get("auth", "service"),
            )
            gs.grants[g.tool] = g
        for sa in effective.get("sub_agents") or []:
            gs.sub_agents[agent_tool_name(sa["name"])] = sa["name"]
        return gs

    def intersect_connectors(self, allowed: Iterable[str] | None) -> "GrantSet":
        """FROZEN D1: an enterprise agent called inside a team's run is cut down
        to the root team's grants. `allowed` is a set of permission strings."""
        if allowed is None:
            return self
        allowed = set(allowed)
        return GrantSet(
            grants={k: g for k, g in self.grants.items() if g.permission in allowed},
            denylist=self.denylist, sub_agents=dict(self.sub_agents))

    def permissions(self) -> set[str]:
        return {g.permission for g in self.grants.values()}

    def permission_for(self, name: str) -> str:
        """Best-effort qualified permission string for any tool name the model
        emitted, granted or not, so a denial is recorded with a readable action."""
        if name in self.grants:
            return self.grants[name].permission
        if name in self.sub_agents:
            return f"agent:{self.sub_agents[name]}"
        if name.startswith("agent__"):
            return "agent:" + name[len("agent__"):]
        if "__" in name:
            conn, wire = name.split("__", 1)
            return wire_map(conn).get(wire, f"{conn}:{wire}")
        for conn in connector_actions()["connectors"]:
            perm = wire_map(conn).get(name)
            if perm:
                return perm
        return name

    def denied_by_list(self, permission: str) -> str | None:
        for pat in self.denylist:
            if fnmatch.fnmatchcase(permission, pat):
                return pat
        return None

    def check(self, name: str, args: dict[str, Any]) -> Rejection | None:
        """None if the call may run, else the Rejection to record."""
        perm = self.permission_for(name)
        if name in self.sub_agents:
            return None
        pat = self.denied_by_list(perm)
        if pat:
            return Rejection(Code.TOOL_CALL_DENIED, field=perm, blocked_by="denylist",
                             attempted=perm, extra={"deny_rule": pat})
        g = self.grants.get(name)
        if g is None:
            return Rejection(Code.TOOL_CALL_DENIED, field=perm, blocked_by="resolved permissions",
                             attempted=perm)
        bad = out_of_scope(g, args)
        if bad:
            rtype, value = bad
            return Rejection(Code.TOOL_CALL_DENIED, field=f"{perm}.resources.{rtype}",
                             blocked_by="resolved permissions", attempted=f"{perm} on {value}",
                             allowed=g.resources.get(rtype), extra={"resource": value})
        return None


def _glob_match(value: str, patterns: list[str]) -> bool:
    """`*` never crosses a `/` (FROZEN D18)."""
    for p in patterns:
        rx = "".join("[^/]*" if c == "*" else ("[^/]" if c == "?" else re.escape(c)) for c in p)
        if re.fullmatch(rx, value):
            return True
    return False


def out_of_scope(g: Grant, args: dict[str, Any]) -> tuple[str, str] | None:
    """If a call names a resource outside the grant's scope, return it."""
    for rtype, patterns in g.resources.items():
        if rtype not in args or not patterns:
            continue
        vals = args[rtype] if isinstance(args[rtype], list) else [args[rtype]]
        for v in vals:
            if isinstance(v, str) and not _glob_match(v, patterns):
                return rtype, v
    return None


def assert_no_drift(grants: GrantSet, server_tools: dict[str, set[str]]) -> None:
    """Every granted action must exist on its server, else E_CONNECTOR_DRIFT.
    `server_tools` maps connector -> set of wire names the server exposes."""
    rejections = []
    for g in grants.grants.values():
        exposed = server_tools.get(g.connector)
        if exposed is None:
            continue  # connector not loaded in this process; its own check runs where it is
        if g.mcp_tool not in exposed:
            rejections.append(Rejection(Code.CONNECTOR_DRIFT, field=g.permission,
                                        blocked_by=g.connector, attempted=g.mcp_tool))
    if rejections:
        raise ReAgentError(*rejections)
