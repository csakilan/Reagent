"""Model and connector binding from a resolved spec. WF1.

Reads only `effective`. Never walks `provenance`.

Switches, all env vars, so the same code runs against fakes and live:
  REAGENT_MODEL       fake (default) | live    live = OpenRouter via ChatOpenAI
  REAGENT_CONNECTORS  fake (default) | live    live = MCP wrappers on the
                                               contract's transport_url
Default is fake so tests and the eval engine never burn credits by accident.
"""
from __future__ import annotations

import contextvars
import logging
import os
from typing import Any

from langchain_core.tools import BaseTool

from runtime.guardrails.permissions import (GrantSet, assert_no_drift, connector_actions,
                                            tool_name)

log = logging.getLogger("runtime.binding")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def model_mode() -> str:
    return os.environ.get("REAGENT_MODEL", "fake").strip().lower()


def connector_mode() -> str:
    return os.environ.get("REAGENT_CONNECTORS", "fake").strip().lower()


def _rejects_temperature(or_id: str) -> bool:
    """OpenAI reasoning models (gpt-5*, o-series) refuse `temperature`. With
    require_parameters on, sending it filters out every endpoint and OpenRouter
    answers 404 "No endpoints found". Found live with secret-scanner on gpt-5-mini."""
    name = or_id.split("/", 1)[-1]
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


def build_model(model: dict[str, Any], *, fallback: bool = False, has_tools: bool = True):
    """ChatOpenAI against OpenRouter. Cost is read from
    response_metadata["cost"], which langchain-openai only fills from 1.1.11."""
    or_id = model.get("fallback_openrouter_model" if fallback else "openrouter_model")
    if not or_id:
        return None
    if model_mode() != "live":
        from runtime.engine.fakes import FakeChatModel
        return FakeChatModel(model_name=f"fake/{or_id}")
    import httpx
    from langchain_openai import ChatOpenAI
    params = dict(model.get("params") or {})
    temperature = None if _rejects_temperature(or_id) else params.get("temperature")
    return ChatOpenAI(
        model=or_id,
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        stream_usage=True,
        temperature=temperature,
        max_tokens=params.get("max_tokens"),
        # OFF by default. Verified live: with require_parameters on, OpenRouter
        # intermittently 404s "No endpoints found" for qwen-coder at the
        # "Filter by Parameters" step, and the fallback model fails the same
        # way, killing the run. OpenRouter already routes tool requests only to
        # tool-capable providers. Opt in with REAGENT_REQUIRE_PARAMETERS=1.
        extra_body=({"provider": {"require_parameters": True}}
                    if has_tools and os.environ.get("REAGENT_REQUIRE_PARAMETERS") == "1" else None),
        max_retries=1,
        timeout=120,
        # A client of its own. langchain-openai otherwise shares one cached async
        # client process-wide, and a pooled connection opened in an earlier event
        # loop (run_agent, then decide_approval in a new asyncio.run) fails with
        # "Event loop is closed" on the next call. Found live in memory curation.
        http_async_client=httpx.AsyncClient(timeout=120),
    )


def wrapper_headers(connector: str, grants: GrantSet, identity: dict[str, str]) -> dict[str, str]:
    """HTTP headers for one wrapper. The shared secret authenticates the
    RUNTIME to the wrapper; it is never a tool argument, never in a prompt,
    and its value is on the redactor's env list. Wrapper auth is not an
    approval: PermissionMiddleware and HITL still gate every call."""
    h = dict(identity)
    secret = os.environ.get("CONNECTOR_SHARED_SECRET")
    if secret:
        h["Authorization"] = "Bearer " + secret
    obo = any(g.connector == connector and g.auth == "on_behalf_of" for g in grants.grants.values())
    h["X-Reagent-Auth-Mode"] = "on_behalf_of" if obo else "service"
    return h


#: Headers for the wrapper call in progress. A contextvar rather than a second
#: argument, because WF2's eval sandbox replaces _server_tools(connector) with
#: a one-argument mock; changing the signature broke every sandboxed eval.
_WRAPPER_HEADERS: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "wrapper_headers", default={})


async def _server_tools(connector: str) -> list[BaseTool]:
    """Everything the server exposes. Not filtered: there is no per-tool filter
    in langchain-mcp-adapters, only per-server. Keep this signature: WF2's eval
    sandbox monkeypatches it."""
    headers = _WRAPPER_HEADERS.get()
    if connector_mode() != "live":
        from runtime.engine.fakes import fake_connector
        return fake_connector(connector)
    from langchain_mcp_adapters.client import MultiServerMCPClient
    url = connector_actions()["connectors"][connector]["transport_url"]
    cfg: dict[str, Any] = {"transport": "streamable_http", "url": url}
    if headers:
        cfg["headers"] = headers
    client = MultiServerMCPClient({connector: cfg})
    return await client.get_tools(server_name=connector)


def _leaf(e: BaseException) -> BaseException:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


async def load_tools(grants: GrantSet, identity: dict[str, str] | None = None) -> list[BaseTool]:
    """Layer 1 of 2: keep ungranted tools out of the model's context. This is
    advisory; PermissionMiddleware is the boundary.

    Fails with E_CONNECTOR_DRIFT if a granted action is missing on its server,
    so a renamed upstream tool is loud rather than silently granting nothing."""
    by_connector: dict[str, list[BaseTool]] = {}
    for conn in sorted({g.connector for g in grants.grants.values()}):
        try:
            token = _WRAPPER_HEADERS.set(wrapper_headers(conn, grants, identity or {}))
            try:
                by_connector[conn] = await _server_tools(conn)
            finally:
                _WRAPPER_HEADERS.reset(token)
        except Exception as e:  # noqa: BLE001
            from contracts.errors import Code, ReAgentError, Rejection
            leaf = _leaf(e)  # the MCP client wraps errors in TaskGroup exception groups
            raise ReAgentError(Rejection(Code.CONNECTOR_ERROR, field=conn, blocked_by=conn,
                                         attempted=f"{type(leaf).__name__}: {leaf}"[:200])) from e
    assert_no_drift(grants, {c: {t.name for t in ts} for c, ts in by_connector.items()})
    wanted = {(g.connector, g.mcp_tool): g for g in grants.grants.values()}
    out: list[BaseTool] = []
    for conn, tools in by_connector.items():
        for t in tools:
            g = wanted.get((conn, t.name))
            if g is None:
                continue
            t.name = tool_name(conn, t.name)
            t.description = f"[{g.permission}] {t.description or ''}".strip()
            out.append(t)
    return out
