"""The platform connector (runtime/api/platform.py), over real HTTP.

The runtime app is served by uvicorn on a free port; calls go through the same
langchain-mcp-adapters client the runtime uses. No database, no model.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest

pytest.importorskip("langchain_mcp_adapters")

SECRET = "platform-test-secret"


@pytest.fixture(scope="module")
def url():
    import os

    import uvicorn
    saved = os.environ.get("CONNECTOR_SHARED_SECRET")
    os.environ["CONNECTOR_SHARED_SECRET"] = SECRET
    from runtime.api.runs import app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp/platform"
    server.should_exit = True
    t.join(5)
    # The app's lifespan switches this process to background memory curation,
    # which is right for a server and wrong for every test that runs after.
    from runtime.engine import memory_curation
    memory_curation._BACKGROUND = False
    if saved is None:
        os.environ.pop("CONNECTOR_SHARED_SECRET", None)
    else:
        os.environ["CONNECTOR_SHARED_SECRET"] = saved


def tools(url, user="bob", secret=SECRET):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    headers = {"X-Reagent-User": user} if user else {}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    client = MultiServerMCPClient({"platform": {"transport": "streamable_http", "url": url,
                                                "headers": headers}})
    return {t.name: t for t in asyncio.run(client.get_tools(server_name="platform"))}


def invoke(url, tool, args, **who):
    res = asyncio.run(tools(url, **who)[tool].ainvoke(args))
    text = res if isinstance(res, str) else "".join(
        b.get("text", "") if isinstance(b, dict) else str(b) for b in (res if isinstance(res, list) else [res]))
    try:
        return json.loads(text)
    except ValueError:
        return text


def test_every_declared_action_is_listed(url):
    from runtime.guardrails.permissions import connector_actions
    want = {a["mcp_tool"] for a in connector_actions()["connectors"]["platform"]["actions"]}
    assert set(tools(url)) == want


def test_search_is_ranked_by_outcome_and_scoped_to_the_caller(url):
    bob = invoke(url, "search_agents", {"query": "catch secrets committed in pull requests"})
    assert bob["agents"][0]["name"] == "pr-reviewer"
    assert bob["agents"][0]["match"]["reason"].startswith("Matched outcome:")
    hr = invoke(url, "search_agents", {"query": "answer HR handbook questions in Slack"})
    assert all(a["team"] == "platform-eng" for a in hr["agents"]), "bob never sees people-ops"
    dana = invoke(url, "search_agents", {"query": "answer HR handbook questions in Slack"}, user="dana")
    assert [a["name"] for a in dana["agents"]] == ["people-ops-bot"]


def test_get_agent_resolve_team_and_validate(url):
    assert "kind: Agent" in invoke(url, "get_agent", {"name": "pr-reviewer"})["spec_yaml"]
    assert "E_PLATFORM_NOT_FOUND" in str(invoke(url, "get_agent", {"name": "people-ops-bot"}))
    team = invoke(url, "resolve_team", {})
    assert team["team"] == "platform-eng" and any(c["connector"] == "github" for c in team["connectors"])
    v = invoke(url, "validate", {"spec_yaml": "kind: Agent\nmetadata: {name: x}\n"})
    assert v["valid"] is False and v["rejections"]


def test_stubs_say_so(url):
    assert "E_NOT_AVAILABLE" in str(invoke(url, "propose_agent", {}))


def test_calls_need_the_runtime_secret_and_a_caller(url):
    assert "E_PLATFORM_UNAUTHORIZED" in str(invoke(url, "search_agents", {"query": "secrets"}, secret="wrong"))
    assert "E_PLATFORM_NO_PRINCIPAL" in str(invoke(url, "search_agents", {"query": "secrets"}, user=None))


def test_agent_builder_loads_its_platform_tools_without_drift(url, monkeypatch):
    """load_tools fails a run with E_CONNECTOR_DRIFT if a granted action is
    missing; this is the check that blocked agent-builder before."""
    from runtime.engine import binding
    from runtime.engine.runner import load_resolved
    from runtime.guardrails.permissions import GrantSet, connector_actions
    monkeypatch.setenv("REAGENT_CONNECTORS", "live")
    monkeypatch.setitem(connector_actions()["connectors"]["platform"], "transport_url", url)
    grants = GrantSet.from_effective(load_resolved("agent-builder")["effective"])
    loaded = asyncio.run(binding.load_tools(grants, {"X-Reagent-User": "bob"}))
    names = {t.name for t in loaded}
    assert "platform__search_agents" in names and "platform__propose_agent" in names
    search = next(t for t in loaded if t.name == "platform__search_agents")
    out = asyncio.run(search.ainvoke({"query": "flag public S3 buckets in terraform PRs"}))
    assert "pr-reviewer" in json.dumps(out, default=str)


def test_fake_connector_runs_the_real_search(monkeypatch):
    from runtime.engine import binding
    from runtime.engine.fakes import fake_connector
    token = binding._WRAPPER_HEADERS.set({"X-Reagent-User": "bob"})
    try:
        tool = next(t for t in fake_connector("platform") if t.name == "search_agents")
    finally:
        binding._WRAPPER_HEADERS.reset(token)
    out = json.loads(asyncio.run(tool.ainvoke({"query": "catch secrets committed in pull requests"})))
    assert out["agents"][0]["name"] == "pr-reviewer"
