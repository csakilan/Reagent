"""MVP rows 8 and 9: connector requests and admin edits to the layers, with impact."""
from __future__ import annotations

import pytest
import yaml

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from runtime.api import agents as api  # noqa: E402
from runtime.resolver import load_catalog, source  # noqa: E402

client = TestClient(api.create_app())
FRANK, BOB, DANA = ({"X-ReAgent-User": u} for u in ("frank", "bob", "dana"))


@pytest.fixture(autouse=True)
def clean():
    for k in source._LAYERS:
        source._LAYERS[k].clear()
    api._REQUESTS.clear()
    yield
    for k in source._LAYERS:
        source._LAYERS[k].clear()
    api._REQUESTS.clear()


def team_yaml(name):
    return yaml.safe_load(client.get("/v0/admin/layers", headers=FRANK).json()["teams"][name])


def test_request_flow_end_to_end():
    """Row 9's done-when: a member requests a connector, an admin grants it, it is live."""
    req = client.post("/v0/connector-requests", json={"description": "Slack posting in #ask-people-ops-2"}, headers=DANA)
    assert req.status_code == 201 and req.json()["team"] == "people-ops"
    assert client.get("/v0/connector-requests", headers=BOB).json()["requests"] == []
    assert len(client.get("/v0/connector-requests", headers=FRANK).json()["requests"]) == 1

    team = team_yaml("people-ops")
    team["spec"]["connectors"]["slack"]["resources"]["channel"].append("#ask-people-ops-2")
    saved = client.post("/v0/admin/teams/people-ops", json={"spec": yaml.safe_dump(team)}, headers=FRANK).json()
    assert saved["saved"] and "#ask-people-ops-2" in load_catalog().team("people-ops")["connectors"]["slack"]["resources"]["channel"]
    assert client.post(f"/v0/connector-requests/{req.json()['id']}/decide", json={"status": "fulfilled"},
                       headers=FRANK).json()["status"] == "fulfilled"


def test_dry_run_reports_impact_and_saves_nothing():
    team = team_yaml("platform-eng")
    team["spec"]["connectors"]["github"]["allow"] = ["pulls.read"]
    out = client.post("/v0/admin/teams/platform-eng", json={"spec": yaml.safe_dump(team), "dry_run": True}, headers=FRANK).json()
    hit = next(i for i in out["impact"] if i["agent"] == "pr-reviewer")
    assert out["saved"] is False and hit["before"] == "resolves" and hit["after"] == "rejected"
    assert "pulls.comment" in hit["rejections"][0]["message"]
    assert "pulls.comment" in load_catalog().team("platform-eng")["connectors"]["github"]["allow"]


def test_a_team_layer_cannot_widen_the_enterprise():
    team = team_yaml("platform-eng")
    team["spec"]["limits"]["usd_per_month"] = 5000
    r = client.post("/v0/admin/teams/platform-eng", json={"spec": yaml.safe_dump(team)}, headers=FRANK)
    assert r.status_code == 422 and r.json()["rejections"][0]["code"] == "E_LIMIT_ABOVE_PARENT"


def test_only_admins_edit_layers():
    for path in ("/v0/admin/teams/platform-eng", "/v0/admin/enterprise"):
        assert client.post(path, json={"spec": "x"}, headers=BOB).status_code == 403
    assert client.post("/v0/admin/registry", json={"kind": "model", "spec": "x"}, headers=BOB).status_code == 403
    assert client.get("/v0/admin/layers", headers=BOB).status_code == 403


def test_registering_a_model_makes_it_grantable():
    spec = yaml.safe_dump({"apiVersion": "reagent/v0", "kind": "Model",
                           "metadata": {"name": "tiny-model", "version": "1.0.0"},
                           "spec": {"provider": "openrouter", "model": "openai/gpt-oss-20b", "supports_tools": True}})
    r = client.post("/v0/admin/registry", json={"kind": "model", "spec": spec}, headers=FRANK)
    assert r.status_code == 201 and "tiny-model" in load_catalog().models


def test_meta_agents_are_listed_without_configs_and_run_for_the_caller():
    pytest.importorskip("langchain")
    listed = client.get("/v0/meta-agents", headers=BOB).json()["meta_agents"]
    assert {m["name"] for m in listed} == {"agent-builder", "cost-optimizer", "evaluator", "failure-triage", "security-reviewer"}
    assert all(set(m) == {"name", "description", "input"} for m in listed)       # no spec, prompt or tools
    # Team decision: meta-agents run inside the user flow, not on demand. Members are refused.
    assert client.post("/v0/meta-agents/security-reviewer/runs", json={"input": {}}, headers=BOB).status_code == 403
    assert client.post("/v0/meta-agents/secret-scanner/runs", json={"input": {}}, headers=FRANK).status_code == 404


def test_platform_actions_are_filtered_by_the_caller():
    pytest.importorskip("langchain")
    from runtime.api import platform
    assert platform.call("run_evals", "bob", {"agent": "pr-reviewer"})["runs_per_case"] == 1
    with pytest.raises(platform.PlatformError):
        platform.call("run_evals", "bob", {"agent": "people-ops-bot"})
    draft = ("apiVersion: reagent/v0\nkind: Agent\nmetadata: {name: proposed-x, version: 0.1.0, level: team, team: people-ops}\n"
             "spec:\n  use_case: {description: x, desired_outcome: y}\n  model: {id: qwen-coder}\n  prompt: {task: t}\n"
             "  triggers: [{type: http}]\n  ownership: {owners: [bob]}\n")
    out = platform.call("propose_agent", "bob", {"spec_yaml": draft})
    assert out["team"] == "platform-eng" and out["status"] == "draft"      # never another team's
    source._DRAFTS.pop("proposed-x", None)
    assert platform.call("request_connector", "bob", {"description": "slack"})["team"] == "platform-eng"


def _signed(body: dict, secret: str = "test-secret", age: int = 0):
    import hashlib, hmac, json as _json, time
    raw = _json.dumps(body).encode()
    ts = str(int(time.time()) - age)
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig, "Content-Type": "application/json"}


def test_slack_events_verify_the_signature_and_route_mentions(monkeypatch):
    """MVP row 3: an app_mention in #ask-people-ops runs people-ops-bot."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-secret")
    ran = []
    monkeypatch.setattr(api, "_run_from_slack", lambda name, payload: ran.append((name, payload)))
    raw, headers = _signed({"type": "url_verification", "challenge": "abc"})
    assert client.post("/v0/events/slack", content=raw, headers=headers).json() == {"challenge": "abc"}
    raw, headers = _signed({"type": "url_verification", "challenge": "abc"}, secret="wrong")
    assert client.post("/v0/events/slack", content=raw, headers=headers).status_code == 401
    raw, headers = _signed({"type": "url_verification", "challenge": "abc"}, age=600)
    assert client.post("/v0/events/slack", content=raw, headers=headers).status_code == 401   # replay
    event = {"type": "event_callback", "event": {"type": "app_mention", "channel": "#ask-people-ops",
                                                 "text": "<@bot> how many PTO days?", "user": "U1", "ts": "1.2"}}
    raw, headers = _signed(event)
    out = client.post("/v0/events/slack", content=raw, headers=headers).json()
    assert out["agents"] == ["people-ops-bot"] and ran[0][0] == "people-ops-bot"
    assert ran[0][1]["text"].endswith("how many PTO days?")
    raw, headers = _signed({**event, "event": {**event["event"], "channel": "#random"}})
    assert client.post("/v0/events/slack", content=raw, headers=headers).json()["agents"] == []


def test_agent_builder_forks_the_closest_agent_or_the_team_base():
    """Describing an agent in the New agent drawer triggers agent-builder."""
    near = client.post("/v0/agents/build", json={"request": "Review pull requests to our infra repos and flag leaked secrets"},
                       headers=BOB).json()
    assert near["source"].startswith("pr-reviewer@") and near["team"] == "platform-eng" and near["rejections"] == []
    far = client.post("/v0/agents/build", json={"request": "Summarize the week's deploys into a short status note"},
                      headers=BOB).json()
    assert far["source"] == "team:platform-eng"
    built = client.get(f"/v0/agents/{far['name']}", headers=BOB).json()
    assert "deploys" in built["description"] and built["status"] == "draft"
    assert client.get(f"/v0/agents/{far['name']}", headers=DANA).status_code == 404     # another team
    for n in (near["name"], far["name"]):
        source._DRAFTS.pop(n, None)


def test_cost_optimizer_runs_only_after_evals_and_compares_team_models(monkeypatch):
    pytest.importorskip("langchain")
    monkeypatch.setenv("REAGENT_MODEL", "fake")
    monkeypatch.setenv("REAGENT_PIPELINE_RUNS_PER_CASE", "1")
    from runtime.publish import STORE
    STORE.runs.clear()
    A = {"X-ReAgent-User": "alice"}
    rid = client.post("/v0/agents/pr-reviewer/publish", headers=A).json()["publish_run_id"]
    assert client.post(f"/v0/publish-runs/{rid}/cost-check", headers=A).status_code == 409
    for c in client.get(f"/v0/publish-runs/{rid}", headers=A).json()["cases"]:
        if c["decision"] is None:
            client.post(f"/v0/publish-runs/{rid}/cases/{c['case_id']}/review", json={"decision": "approve"}, headers=A)
    client.post(f"/v0/publish-runs/{rid}/continue", headers=A)
    from runtime.publish.live import wait_idle          # evals run in the background
    wait_idle()
    out = client.post(f"/v0/publish-runs/{rid}/cost-check", headers=A).json()
    assert {o["model"] for o in out["options"]} == {"qwen-coder", "claude-sonnet", "gpt-mini"} and out["advice"]
    STORE.runs.clear()
