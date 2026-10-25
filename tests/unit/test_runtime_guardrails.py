"""WF1 guardrails: redaction and permission checks. Pure Python, runs in CI."""
from __future__ import annotations

import json
import logging

import pytest

from runtime.guardrails.redaction import Redactor, RedactingFilter

PLANTED = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


@pytest.fixture
def r() -> Redactor:
    return Redactor(pii=True)


@pytest.mark.parametrize("text,rule", [
    (f'secret_key = "{PLANTED}"', "known_secret"),
    ("AKIAIOSFODNN7EXAMPLE", "known_secret"),
    ("AKIAZZZZZZZZZZZZZZZZ", "aws_access_key_id"),
    ("xK9mP2vL7nQ4wR8tY3uI6oA1sD5fG0hJkLzXcVbN", "aws_secret_access_key"),
    ("ghp_1234567890abcdefghijklmnopqrstuvwxyzAB", "github_token"),
    ("xoxb-123456789012-abcdefghijkl", "slack_token"),
    ("sk-or-v1-abcdefghijklmnopqrstuvwxyz0123", "openrouter_key"),
    ("postgresql://tsdbadmin:hunter2@db.example.com:5432/tsdb", "connection_string"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "private_key"),
    ("password: correct-horse-battery", "password_assignment"),
    ("mail alice@nmc2.example", "email"),
])
def test_each_rule_masks_and_reports_by_name(r, text, rule):
    out, hits = r.redact_text(text)
    assert hits.get(rule), (out, hits)
    assert f"[REDACTED:{rule}]" in out


@pytest.mark.parametrize("text", [
    "commit 3f786850e387550fdab836ed7e6dc881de23001b",   # sha1 is not a secret
    "Review of nmc2/infra#42, see infra/main.tf line 5",
    "pr_number 4111 and run 1234567890123",               # not Luhn-valid
])
def test_ordinary_text_survives(r, text):
    assert r.redact_text(text) == (text, {})


def test_hits_never_carry_the_value(r):
    _, hits = r.redact_text(PLANTED)
    assert PLANTED not in json.dumps(hits)


def test_nested_structures_are_redacted(r):
    obj, hits = r.redact({"a": [{"diff": f"+ key={PLANTED}"}], "n": 3})
    assert PLANTED not in json.dumps(obj) and obj["n"] == 3 and hits


def test_env_secret_values_are_redacted(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "zzz-live-key-value-123")
    out, _ = Redactor().redact_text("key is zzz-live-key-value-123")
    assert "zzz-live-key-value-123" not in out


def test_secrets_cannot_be_switched_off_by_the_spec():
    red = Redactor.from_effective({"guardrails": {"redaction": {"secrets": False}}})
    assert PLANTED not in red.redact_text(PLANTED)[0]


def test_log_path_is_redacted(caplog):
    lg = logging.getLogger("runtime.test.redaction")
    lg.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="runtime.test.redaction"):
        lg.info("leaked %s", PLANTED)
    assert PLANTED not in caplog.text and "REDACTED" in caplog.text


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------

from runtime.guardrails.permissions import GrantSet, assert_no_drift  # noqa: E402
from contracts.errors import Code, ReAgentError  # noqa: E402

EFFECTIVE = {
    "tool_permissions": [
        {"permission": "github:pulls.read", "connector": "github", "action": "pulls.read",
         "mcp_tool": "pulls_read", "resources": {"repo": ["nmc2/infra", "nmc2/api-*"]},
         "risk": "read", "requires_approval": False},
        {"permission": "github:pulls.comment", "connector": "github", "action": "pulls.comment",
         "mcp_tool": "pulls_comment", "resources": {"repo": ["nmc2/infra"]},
         "risk": "write", "requires_approval": True},
    ],
    "denylist": ["github:pulls.merge", "github:repos.*"],
    "sub_agents": [{"name": "secret-scanner", "version": "1.1.0"}],
}


@pytest.fixture
def gs() -> GrantSet:
    return GrantSet.from_effective(EFFECTIVE)


def test_tools_are_built_from_mcp_tool_not_action(gs):
    assert set(gs.grants) == {"github__pulls_read", "github__pulls_comment"}


def test_granted_call_in_scope_is_allowed(gs):
    assert gs.check("github__pulls_read", {"repo": "nmc2/infra", "pr_number": 1}) is None


def test_ungranted_call_is_denied_with_the_qualified_action(gs):
    rej = gs.check("github__issues_create", {"repo": "nmc2/infra"})
    assert rej.code == Code.TOOL_CALL_DENIED and rej.field == "github:issues.create"


def test_denylist_beats_everything(gs):
    rej = gs.check("github__pulls_merge", {"repo": "nmc2/infra"})
    assert rej.code == Code.TOOL_CALL_DENIED and rej.extra["deny_rule"] == "github:pulls.merge"


def test_resource_outside_scope_is_denied(gs):
    assert gs.check("github__pulls_read", {"repo": "nmc2/secrets"}) is not None


def test_star_does_not_cross_a_slash(gs):
    assert gs.check("github__pulls_read", {"repo": "nmc2/api-v2"}) is None
    assert gs.check("github__pulls_read", {"repo": "nmc2/api-v2/evil"}) is not None


def test_unknown_bare_name_maps_through_the_contract(gs):
    assert gs.permission_for("pulls_merge") == "github:pulls.merge"


def test_sub_agent_is_a_tool_with_a_legal_name(gs):
    assert gs.permission_for("agent__secret-scanner") == "agent:secret-scanner"
    assert gs.check("agent__secret-scanner", {"text": "x"}) is None


def test_drift_is_loud(gs):
    with pytest.raises(ReAgentError) as e:
        assert_no_drift(gs, {"github": {"pulls_read"}})
    assert e.value.rejections[0].code == Code.CONNECTOR_DRIFT
    assert_no_drift(gs, {"github": {"pulls_read", "pulls_comment", "pulls_merge"}})


def test_root_intersection_only_narrows(gs):
    cut = gs.intersect_connectors({"github:pulls.read"})
    assert set(cut.grants) == {"github__pulls_read"}
