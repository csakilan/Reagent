"""Model and connector binding from a resolved spec. WF1.

Reads only `effective`. Never walks `provenance`.

Switches, all env vars, so the same code runs against fakes and live:
  REAGENT_MODEL       fake (default) | live    live = OpenRouter via ChatOpenAI
  REAGENT_CONNECTORS  fake (default) | live    live = MCP wrappers on the
                                               contract's transport_url
Default is fake so tests and the eval engine never burn credits by accident.
"""
from __future__ import annotations

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


def build_model(model: dict[str, Any], *, fallback: bool = False):
    """ChatOpenAI against OpenRouter. Cost is read from
    response_metadata["cost"], which langchain-openai only fills from 1.1.11."""
    or_id = model.get("fallback_openrouter_model" if fallback else "openrouter_model")
    if not or_id:
        return None
    if model_mode() != "live":
        from runtime.engine.fakes import FakeChatModel
        return FakeChatModel(model_name=f"fake/{or_id}")
    from langchain_openai import ChatOpenAI
    params = dict(model.get("params") or {})
    return ChatOpenAI(
        model=or_id,
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        stream_usage=True,
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        # supported_parameters is a union across providers; force routing to
        # endpoints that genuinely support tool calls.
        extra_body={"provider": {"require_parameters": True}},
        max_retries=1,
        timeout=120,
    )


async def _server_tools(connector: str) -> list[BaseTool]:
    """Everything the server exposes. Not filtered: there is no per-tool filter
    in langchain-mcp-adapters, only per-server."""
    if connector_mode() != "live":
        from runtime.engine.fakes import fake_connector
        return fake_connector(connector)
    from langchain_mcp_adapters.client import MultiServerMCPClient
    url = connector_actions()["connectors"][connector]["transport_url"]
    client = MultiServerMCPClient({connector: {"transport": "streamable_http", "url": url}})
    return await client.get_tools(server_name=connector)


async def load_tools(grants: GrantSet) -> list[BaseTool]:
    """Layer 1 of 2: keep ungranted tools out of the model's context. This is
    advisory; PermissionMiddleware is the boundary.

    Fails with E_CONNECTOR_DRIFT if a granted action is missing on its server,
    so a renamed upstream tool is loud rather than silently granting nothing."""
    by_connector: dict[str, list[BaseTool]] = {}
    for conn in sorted({g.connector for g in grants.grants.values()}):
        try:
            by_connector[conn] = await _server_tools(conn)
        except Exception as e:  # noqa: BLE001
            from contracts.errors import Code, ReAgentError, Rejection
            raise ReAgentError(Rejection(Code.CONNECTOR_ERROR, field=conn, blocked_by=conn,
                                         attempted=f"{type(e).__name__}: {e}"[:200])) from e
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
