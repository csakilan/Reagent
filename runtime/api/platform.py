"""The `platform` connector: the MCP server meta-agents call (SPEC.md §10).

Served by the runtime app at /mcp/platform (contracts/connector_actions.json),
because every action is a view of the runtime's own state: the catalog, the
resolver, the validator, outcome search. Every action is filtered by the CALLER,
exactly as the HTTP routes are, by calling those routes' own functions.

Who is calling: X-Reagent-User, the root run's user, set by the runtime alone
(runner.arun_agent) and trusted only with the runtime's shared secret
(Authorization: Bearer $CONNECTOR_SHARED_SECRET), like every wrapper header.

Served: resolve_team, search_agents, get_agent, validate.
Stubbed: run_evals, get_runs, record_grade, propose_agent, request_connector
answer E_NOT_AVAILABLE. They must still be LISTED: load_tools fails a run with
E_CONNECTOR_DRIFT when a granted action is missing on its server.
"""
from __future__ import annotations

import contextlib
import hmac
import json
import os
from typing import Any

from fastapi import HTTPException
# Module level, not inside build_server: with postponed annotations FastMCP
# resolves `ctx: Context` from this module's globals.
from mcp.server.fastmcp import Context, FastMCP

PATH = "/mcp/platform"
SERVED = ("resolve_team", "search_agents", "get_agent", "validate")
STUBBED = ("run_evals", "get_runs", "record_grade", "propose_agent", "request_connector")


class PlatformError(ValueError):
    """Becomes the tool's error text; the code leads, as the wrappers' do."""


# --------------------------------------------------------------------------
# The actions, as plain functions of (user, args)
# --------------------------------------------------------------------------

def _http(fn: Any, *a: Any, **kw: Any) -> Any:
    try:
        return fn(*a, **kw)
    except HTTPException as e:
        code = {403: "E_PLATFORM_FORBIDDEN", 404: "E_PLATFORM_NOT_FOUND"}.get(e.status_code, "E_PLATFORM_ERROR")
        raise PlatformError(f"{code}: {e.detail}") from None


def search_agents(user: str, query: str, limit: int = 5) -> dict[str, Any]:
    """The caller's collection ranked by outcome (runtime/api/agent_search.py)."""
    from runtime.api.agents import get_agents
    limit = max(1, min(int(limit or 5), 20))
    hits = _http(get_agents, q=query, tag=None, rewrite=False, limit=limit, x_reagent_user=user)["agents"]
    keep = ("name", "version", "team", "description", "desired_outcome", "pass_rate",
            "production_success_rate", "match")
    return {"query": query, "agents": [{k: a.get(k) for k in keep} for a in hits]}


def get_agent(user: str, name: str) -> dict[str, Any]:
    from runtime.api.agents import get_agent as route
    return _http(route, name, x_reagent_user=user)


def resolve_team(user: str, team: str | None = None) -> dict[str, Any]:
    """What a new agent in the caller's team may use: connector actions with their
    resource scope, allowed models, the denylist."""
    from runtime.api.agents import get_team_grants
    from runtime.resolver import load_catalog
    team = team or load_catalog().team_of(user)
    if not team:
        raise PlatformError("E_PLATFORM_NO_TEAM: the caller is in no team; pass `team`")
    return _http(get_team_grants, team, x_reagent_user=user)


def validate(user: str, spec_yaml: str) -> dict[str, Any]:
    from runtime.api.agents import post_validate
    return _http(post_validate, "draft", {"spec": spec_yaml}, x_reagent_user=user)


def _stub(action: str):
    def run(user: str, **_: Any) -> dict[str, Any]:
        raise PlatformError(f"E_NOT_AVAILABLE: platform:{action} is not served in this build")
    return run


ACTIONS: dict[str, Any] = {"resolve_team": resolve_team, "search_agents": search_agents,
                           "get_agent": get_agent, "validate": validate,
                           **{a: _stub(a) for a in STUBBED}}


def call(action: str, user: str | None, args: dict[str, Any]) -> Any:
    """One action for one caller. Shared by the MCP server and the fake connector."""
    if not user:
        raise PlatformError("E_PLATFORM_NO_PRINCIPAL: no X-Reagent-User on this call")
    return ACTIONS[action](user, **{k: v for k, v in args.items() if v is not None})


# --------------------------------------------------------------------------
# The MCP server
# --------------------------------------------------------------------------

def _caller(ctx: Any) -> str | None:
    """X-Reagent-User, only when the call carries the runtime's shared secret."""
    req = getattr(getattr(ctx, "request_context", None), "request", None)
    headers = getattr(req, "headers", None) or {}
    secret = os.environ.get("CONNECTOR_SHARED_SECRET", "")
    if not secret or not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + secret):
        raise PlatformError("E_PLATFORM_UNAUTHORIZED: platform calls come from the runtime only")
    return headers.get("x-reagent-user")


def _mcp_names() -> dict[str, str]:
    from runtime.guardrails.permissions import connector_actions
    return {a["action"]: a["mcp_tool"] for a in connector_actions()["connectors"]["platform"]["actions"]}


def build_server():
    from mcp.server.transport_security import TransportSecuritySettings

    server = FastMCP("platform-connector", stateless_http=True, json_response=True,
                     streamable_http_path=PATH,
                     transport_security=TransportSecuritySettings(
                         enable_dns_rebinding_protection=True,
                         allowed_hosts=["127.0.0.1:*", "localhost:*", "testserver"],
                         allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]))
    names = _mcp_names()

    def out(v: Any) -> str:
        return json.dumps(v, default=str)

    @server.tool(name=names["search_agents"],
                 description="Search the caller's team collection by desired outcome. Best first, "
                             "each with why it matched.")
    def _search(query: str, ctx: Context, limit: int = 5) -> str:
        return out(call("search_agents", _caller(ctx), {"query": query, "limit": limit}))

    @server.tool(name=names["get_agent"], description="One agent visible to the caller, with its YAML.")
    def _get(name: str, ctx: Context) -> str:
        return out(call("get_agent", _caller(ctx), {"name": name}))

    @server.tool(name=names["resolve_team"],
                 description="Connector actions, resources and models the caller's team may use.")
    def _resolve(ctx: Context, team: str | None = None) -> str:
        return out(call("resolve_team", _caller(ctx), {"team": team}))

    @server.tool(name=names["validate"], description="Validate a draft .agent YAML; returns rejections.")
    def _validate(spec_yaml: str, ctx: Context) -> str:
        return out(call("validate", _caller(ctx), {"spec_yaml": spec_yaml}))

    def stub_for(action: str):
        def stub(ctx: Context) -> str:
            return out(call(action, _caller(ctx), {}))
        return stub
    for action in STUBBED:
        server.tool(name=names[action], description=f"platform:{action} (not available yet)")(stub_for(action))
    return server


_SERVER = None


def server():
    global _SERVER
    if _SERVER is None:
        _SERVER = build_server()
    return _SERVER


def mount(app: Any) -> None:
    """Add /mcp/platform to a FastAPI app. Pair with `lifespan()` in the app's lifespan."""
    app.router.routes.extend(server().streamable_http_app().routes)


@contextlib.asynccontextmanager
async def lifespan():
    async with server().session_manager.run():
        yield
