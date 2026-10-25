"""Task 1: the validator. Schema errors name the field; coverage gaps fail (FROZEN D9)."""
from __future__ import annotations

import copy

import pytest
import yaml

from runtime.resolver import load_catalog
from runtime.resolver.source import DEFAULT_SPEC_ROOT
from runtime.resolver.validate import main, validate_document, validate_draft_text

_BASE = load_catalog()


@pytest.fixture
def cat():
    return copy.deepcopy(_BASE)


def pr(cat):
    return copy.deepcopy(cat.latest("pr-reviewer"))


def found(rep, code):
    return [r for r in rep.rejections if r["code"] == code]


def test_every_example_passes_except_the_judge_only_one():
    """code-reviewer is kept judge-only on purpose: tests/contract/test_eval_report.py
    uses it as the FROZEN D8 example of a suite that cannot gate. pr-reviewer is
    its fork with a real suite."""
    from runtime.resolver.validate import validate_tree
    reports = validate_tree(DEFAULT_SPEC_ROOT.resolve())
    failing = {r.name: {x["code"] for x in r.rejections} for r in reports if not r.ok}
    assert set(failing) == {"code-reviewer"}
    assert "E_NO_GATING_CHECKS" in failing["code-reviewer"]


def test_malformed_file_fails_with_a_readable_error_naming_the_field(cat):
    doc = pr(cat)
    doc["spec"]["budget"]["timeout_seconds"] = "soon"
    rep = validate_document(doc, cat)
    assert not rep.ok
    r = rep.rejections[0]
    assert r["code"] == "E_SCHEMA_INVALID" and r["field"] == "spec.budget.timeout_seconds"
    assert r["message"] == "spec.budget.timeout_seconds: 'soon' is not of type 'integer'"


def test_unparseable_yaml_names_the_line(cat):
    out = validate_draft_text("kind: Agent\nmetadata: [unclosed\n", cat)
    assert out["valid"] is False and "line" in out["rejections"][0]["message"]


def test_coverage_gap_is_a_failure_not_information(cat):
    doc = pr(cat)
    doc["spec"]["evaluation"]["cases"] = [c for c in doc["spec"]["evaluation"]["cases"]
                                          if c.get("kind") != "negative"]
    rep = validate_document(doc, cat)
    assert any(r["attempted"] == "negative" for r in found(rep, "E_COVERAGE_UNMET"))


def test_inherited_default_cases_do_not_count_toward_min_cases(cat):
    """The enterprise ships two safety default_cases; the agent still needs its own."""
    doc = pr(cat)
    doc["spec"]["evaluation"]["cases"] = [c for c in doc["spec"]["evaluation"]["cases"]
                                          if c.get("kind") != "safety"]
    rep = validate_document(doc, cat)
    assert any(r["attempted"] == "safety" for r in found(rep, "E_COVERAGE_UNMET"))


def test_every_callable_action_needs_a_case_including_memory(cat):
    """D12: memory expands into tools BEFORE coverage, so it cannot escape it."""
    doc = pr(cat)
    for c in doc["spec"]["evaluation"]["cases"]:
        (c.get("mocks") or {}).pop("memory:read", None)
    rep = validate_document(doc, cat)
    assert "memory:read" in [r["attempted"] for r in found(rep, "E_COVERAGE_UNMET")]


def test_sub_agent_calls_must_be_mocked(cat):
    """D4."""
    doc = pr(cat)
    doc["spec"]["evaluation"]["cases"][0]["mocks"].pop("agent:secret-scanner")
    rep = validate_document(doc, cat)
    assert [r["field"] for r in found(rep, "E_UNMOCKED_ACTION")] == ["spec.evaluation.cases[0].mocks"]


def test_mock_keys_must_be_real_actions(cat):
    """The schema types `mocks` as a bare object; the validator fills that hole."""
    doc = pr(cat)
    doc["spec"]["evaluation"]["cases"][0]["mocks"]["github:pulls.reed"] = {}
    rep = validate_document(doc, cat)
    assert found(rep, "E_UNKNOWN_ACTION")[0]["attempted"] == "github:pulls.reed"


def test_judge_only_suite_cannot_gate(cat):
    """D8."""
    doc = copy.deepcopy(cat.latest("code-reviewer"))
    doc["spec"]["evaluation"]["cases"] = doc["spec"]["evaluation"]["cases"][:1]
    assert found(validate_document(doc, cat), "E_NO_GATING_CHECKS")


def test_policy_violation_fails_validation_even_though_the_schema_accepts_it(cat):
    """The schema accepts redaction.secrets: false. The audit found ALL PASS on this."""
    doc = pr(cat)
    doc["spec"]["guardrails"]["redaction"] = {"secrets": False}
    codes = {r["code"] for r in validate_document(doc, cat).rejections}
    assert {"E_LOCKED_PATH", "E_SAFETY_FLAG_DISABLED"} <= codes


def test_team_layer_is_validated_on_its_own(cat):
    team = copy.deepcopy(cat.teams["platform-eng"])
    cat.teams["platform-eng"]["spec"]["memory"]["buckets"].append(
        {"name": "enterprise-kb", "type": "semantic", "read": ["*"], "write": ["*"]})
    rep = validate_document(cat.teams["platform-eng"], cat)
    assert found(rep, "E_BUCKET_REDEFINED")
    assert validate_document(team, copy.deepcopy(_BASE)).ok


def test_cli_exits_non_zero_on_a_rejection(tmp_path, cat):
    doc = pr(cat)
    doc["spec"]["budget"]["timeout_seconds"] = 1200
    p = tmp_path / "pr-reviewer.agent"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert main([str(p)]) == 1
