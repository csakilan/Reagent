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
