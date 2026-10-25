"""H0 — the contracts themselves are coherent.

This is the one seam test that must be GREEN before anybody starts. It does not
depend on any workflow's code, only on contracts/ and fixtures/.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

MCP_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def test_resolved_schema_is_valid(resolved_schema):
    Draft202012Validator.check_schema(resolved_schema)


def test_golden_fixtures_validate(root, resolved_schema):
    V = Draft202012Validator(resolved_schema)
    files = sorted((root / "fixtures" / "resolved").glob("*.json"))
    assert files, "no golden resolved specs — run scripts/gen_fixtures.py"
    for f in files:
        errs = sorted(V.iter_errors(json.loads(f.read_text())),
                      key=lambda e: list(e.path))
        assert not errs, f"{f.name}: {[(list(e.path), e.message[:90]) for e in errs[:4]]}"


def test_member_view_hides_every_enterprise_value(root):
    """FROZEN D3. A member must not learn an enterprise value through /resolved."""
    doc = json.loads((root / "fixtures" / "resolved" / "pr-reviewer.member.json").read_text())
    assert doc["caller"]["is_enterprise_admin"] is False

    leaked = []

    def walk(node, path="provenance"):
        if isinstance(node, dict):
            if {"value", "source"} <= set(node) and node["source"] == "enterprise":
                if node["value"] is not None or not node.get("redacted"):
                    leaked.append(path)
                return
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(doc["provenance"])
    assert not leaked, f"enterprise values exposed to a member at: {leaked}"
    # The assembled prompt contains the enterprise prompt verbatim, so it is the
    # obvious bypass.
    assert doc["effective"]["prompt"] is None


def test_admin_view_keeps_enterprise_values(root):
    doc = json.loads((root / "fixtures" / "resolved" / "pr-reviewer.admin.json").read_text())
    assert doc["caller"]["is_enterprise_admin"] is True
    assert isinstance(doc["effective"]["prompt"], str)
    assert "internal NMC2 agent" in doc["effective"]["prompt"]


def test_every_mcp_tool_name_is_legal(connector_actions):
    """MCP forbids dots. Claude Code silently rewrites them, so a bad name does
    not error, it just stops matching."""
    bad = []
    for cname, c in connector_actions["connectors"].items():
        for a in c["actions"]:
            if not MCP_NAME.match(a["mcp_tool"]):
                bad.append(f"{cname}:{a['action']} -> {a['mcp_tool']}")
            if len(a["prefixed"]) > 128:
                bad.append(f"{a['prefixed']} is {len(a['prefixed'])} chars")
    assert not bad, bad


def test_connector_ports_are_unique(connector_actions):
    """A collision means the second wrapper to start fails to bind, which looks
    like a routing bug for an hour."""
    ports = connector_actions["ports"]
    assert len(set(ports.values())) == len(ports), f"duplicate port owner: {ports}"


def test_connector_actions_is_not_stale(root):
    r = subprocess.run([sys.executable, "scripts/gen_connector_actions.py"],
                       cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    diff = subprocess.run(["git", "diff", "--exit-code",
                           "contracts/connector_actions.json"], cwd=root)
    assert diff.returncode == 0, "stale: run scripts/gen_connector_actions.py and commit"


def test_effective_and_provenance_agree_on_tools(root):
    """The two views are generated from one merge, so they must not drift."""
    doc = json.loads((root / "fixtures" / "resolved" / "pr-reviewer.admin.json").read_text())
    prov = {(t["value"]["connector"], a)
            for t in doc["provenance"]["tools"] for a in t["value"]["actions"]}
    eff = {(t["connector"], t["action"]) for t in doc["effective"]["tool_permissions"]}
    assert prov == eff, f"only in provenance: {prov - eff}; only in effective: {eff - prov}"


def test_denied_actions_are_not_also_granted(root):
    """FROZEN D13: deny beats allow, so an action may never appear in both."""
    import fnmatch
    doc = json.loads((root / "fixtures" / "resolved" / "pr-reviewer.admin.json").read_text())
    deny = doc["effective"]["denylist"]
    for t in doc["effective"]["tool_permissions"]:
        for pattern in deny:
            assert not fnmatch.fnmatch(t["permission"], pattern), \
                f"{t['permission']} is both granted and denied by {pattern}"


def test_error_codes_render_with_field_and_layer(root):
    """SPEC.md §1 requires the error to name the field and the blocking layer,
    and demo step 4 puts it on screen."""
    sys.path.insert(0, str(root / "contracts"))
    import errors as E

    r = E.Rejection(E.Code.LIMIT_ABOVE_PARENT, "spec.budget.timeout_seconds",
                    "enterprise", 900, 240)
    assert "spec.budget.timeout_seconds" in r.message
    assert "enterprise" in r.message
    assert "900" in r.message and "240" in r.message
    assert r.to_dict()["code"] == "E_LIMIT_ABOVE_PARENT"

    for code in E.Code:
        msg = E.Rejection(code, "f", "enterprise", "a", "b").message
        assert msg and not msg.startswith("{"), f"{code} renders badly: {msg!r}"


def test_per_run_and_aggregate_limits_are_kept_apart(root):
    """FROZEN D7. Mixing them lets four agents at 5 concurrent runs reach 20."""
    doc = json.loads((root / "fixtures" / "resolved" / "pr-reviewer.admin.json").read_text())
    per_run = doc["effective"]["limits"]
    assert set(per_run) == {"usd_per_run", "max_steps", "timeout_seconds", "max_call_depth"}
    for k in ("usd_per_month", "max_concurrent_runs"):
        assert k not in per_run, f"{k} is aggregate and must not be in effective.limits"
    agg = doc["effective"]["aggregate_limits"]
    assert set(agg) == {"enterprise", "team", "agent"}


def test_sub_agent_versions_are_pinned_exactly(root):
    """FROZEN D24. A range makes an 'immutable' version change behaviour."""
    doc = json.loads((root / "fixtures" / "resolved" / "pr-reviewer.admin.json").read_text())
    for s in doc["effective"]["sub_agents"]:
        assert re.fullmatch(r"\d+\.\d+\.\d+", s["version"]), \
            f"{s['name']}@{s['version']} is not an exact version"


# --- the API contract ------------------------------------------------------

def test_openapi_covers_every_spec_route(root):
    import yaml
    d = yaml.safe_load((root / "contracts" / "openapi.yaml").read_text())
    need = {
        "/v0/agents", "/v0/agents/{name}", "/v0/agents/{name}/resolved",
        "/v0/agents/{name}/runs", "/v0/agents/{name}/validate",
        "/v0/agents/{name}/evals", "/v0/agents/{name}/fork",
        "/v0/agents/{name}/publish", "/v0/runs/{id}", "/v0/runs/{id}/trace",
        "/v0/publish-runs/{id}", "/v0/publish-runs/{id}/continue",
        "/v0/publish-runs/{id}/cases/{case}/review",
        "/v0/publish-runs/{id}/cases/{case}/dry-run",
        "/v0/meta-agents", "/v0/meta-agents/{name}/runs",
        "/v0/approvals", "/v0/approvals/{id}", "/v0/connector-requests",
        "/v0/admin/registry", "/v0/admin/teams/{id}", "/v0/admin/enterprise",
        "/v0/admin/calibration", "/v0/metrics",
    }
    missing = need - set(d["paths"])
    assert not missing, f"SPEC.md §14 routes missing from the contract: {sorted(missing)}"


def test_run_body_has_no_user_field(root):
    """FROZEN D2. SPEC.md §14 defines the body as {input, user}, which lets any
    caller borrow another user's token scope through an on_behalf_of tool."""
    import yaml
    d = yaml.safe_load((root / "contracts" / "openapi.yaml").read_text())
    schema = (d["paths"]["/v0/agents/{name}/runs"]["post"]["requestBody"]
              ["content"]["application/json"]["schema"])
    assert "user" not in schema["properties"], \
        "the acting principal comes from the session, never the request body"
    assert schema.get("additionalProperties") is False, \
        "the body must be closed, or `user` creeps back in"


def test_approval_decision_cannot_be_set_to_expired_by_a_caller(root):
    """FROZEN D16: expiry is a platform action that DENIES. A caller forcing
    `expired` would be laundering a denial into a decision."""
    import yaml
    d = yaml.safe_load((root / "contracts" / "openapi.yaml").read_text())
    schema = (d["paths"]["/v0/approvals/{id}"]["post"]["requestBody"]
              ["content"]["application/json"]["schema"])
    assert set(schema["properties"]["decision"]["enum"]) == {"approved", "rejected"}


def test_run_events_schema_is_valid(root):
    import json
    from jsonschema import Draft202012Validator
    s = json.loads((root / "contracts" / "run_events.schema.json").read_text())
    Draft202012Validator.check_schema(s)
    # Every type the database CHECK constraint allows must be describable here,
    # or the runtime can write a row the trace viewer cannot render.
    ddl = (root / "contracts" / "db" / "001_init.sql").read_text()
    import re
    m = re.search(r"type\s+text\s+NOT NULL\s*\n\s*CHECK \(type IN \(([^)]+)\)\)", ddl)
    assert m, "could not find the run_events type CHECK in the DDL"
    db_types = {x.strip().strip("'") for x in m.group(1).replace("\n", "").split(",")}
    assert db_types == set(s["properties"]["type"]["enum"]), (
        f"DDL and schema disagree. only in DDL: {db_types - set(s['properties']['type']['enum'])}; "
        f"only in schema: {set(s['properties']['type']['enum']) - db_types}")
