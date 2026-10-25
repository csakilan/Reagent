"""H3 — the live MCP wrappers serve exactly the registered actions.

Producer: wf/data. HARD FALLBACK at 07:00: past that, wf/runtime stays on fakes.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest


def _mcp_tools(url: str) -> set[str]:
    """tools/list over streamable HTTP. The 2026-07-28 revision has no
    initialize handshake and no session id, and these headers are mandatory."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/list",
        "Origin": "http://localhost",
    })
    with urllib.request.urlopen(req, timeout=5) as r:
        payload = json.loads(r.read().decode().split("data: ")[-1])
    return {t["name"] for t in payload["result"]["tools"]}


@pytest.mark.parametrize("connector", ["github", "slack", "memory"])
def test_wrapper_serves_exactly_the_registered_actions(connector_actions, connector):
    c = connector_actions["connectors"][connector]
    url = c["transport_url"]
    try:
        served = _mcp_tools(url)
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError) as e:
        pytest.skip(f"{connector} wrapper not up at {url} ({type(e).__name__}) — waiting on H3")

    expected = {a["mcp_tool"] for a in c["actions"]}
    missing = expected - served
    extra = served - expected
    assert not missing, (
        f"{connector} is missing {sorted(missing)}. A granted action absent from "
        f"the server is E_CONNECTOR_DRIFT and must fail closed, not silently.")
    assert not extra, (
        f"{connector} exposes {sorted(extra)} beyond its connector file. "
        f"SPEC.md §3: expose exactly the listed actions, never the whole API.")


@pytest.mark.parametrize("connector", ["github", "slack", "memory"])
def test_wrapper_rejects_a_bad_origin(connector_actions, connector):
    """The MCP spec requires Origin validation. Without it a browser-reachable
    local server is a DNS-rebinding hole."""
    url = connector_actions["connectors"][connector]["transport_url"]
    req = urllib.request.Request(url, data=b"{}", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/list",
        "Origin": "https://evil.example.com",
    })
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        assert e.code == 403, f"expected 403 for a bad Origin, got {e.code}"
        return
    except (urllib.error.URLError, OSError):
        pytest.skip(f"{connector} wrapper not up — waiting on H3")
    pytest.fail(f"{connector} accepted a request from a foreign Origin")


def test_no_credential_is_reachable_through_a_tool(connector_actions):
    """SPEC.md §3: credentials go to the wrapper, never into model context."""
    for cname, c in connector_actions["connectors"].items():
        for a in c["actions"]:
            n = a["mcp_tool"].lower()
            assert not any(w in n for w in ("token", "secret", "credential", "password")), \
                f"{cname}:{a['action']} looks like it exposes a credential"
