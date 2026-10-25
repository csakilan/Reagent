"""Task 3: statistics, code checks and the sandbox (FROZEN D4, D8, D15, D22)."""
from __future__ import annotations

import json

import pytest

from runtime.evals import Trace, case_hash, compare, run_checks, wilson
from runtime.evals.stats import percentile


# -- statistics ------------------------------------------------------------
def test_wilson_matches_the_textbook_value():
    lo, hi = wilson(9, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.596, 0.982)


def test_wilson_is_an_interval_even_at_the_extremes():
    assert wilson(10, 10)[0] < wilson(10, 10)[1] == 1.0
    assert wilson(0, 10)[0] == 0.0 < wilson(0, 10)[1]
    assert wilson(0, 0) == (0.0, 1.0)


def test_regression_only_when_intervals_do_not_overlap():
    rep = lambda p, n: dict(zip(("pass_rate_ci_low", "pass_rate_ci_high"), wilson(p, n)))
    assert compare(rep(18, 20), rep(17, 20))["verdict"] == "no significant change"
    assert compare(rep(50, 50), rep(10, 50))["verdict"] == "regression"
    assert compare(rep(10, 50), rep(50, 50))["verdict"] == "improvement"


def test_p95_is_nearest_rank():
    assert percentile(list(range(1, 101)), 95) == 95 and percentile([], 95) == 0.0


# -- code checks, D22 --------------------------------------------------------
def trace(**kw):
    kw.setdefault("status", "succeeded")
    return Trace(**kw)


def test_tool_calls_is_an_ordered_trajectory():
    t = trace(calls=[("github:pulls.read", {}), ("agent:secret-scanner", {}),
                     ("github:pulls.comment", {})])
    assert run_checks({"tool_calls": ["github:pulls.read", "agent:secret-scanner"]}, t, None)["tool_calls"]["passed"]
    assert not run_checks({"tool_calls": ["agent:secret-scanner", "github:pulls.read"]}, t, None)["tool_calls"]["passed"]


def test_no_tool_calls_counts_executed_calls_and_reports_blocked_attempts():
    t = trace(calls=[("github:pulls.read", {})], denied=["github:pulls.merge"])
    r = run_checks({"no_tool_calls": ["github:pulls.merge"]}, t, None)["no_tool_calls"]
    assert r["passed"] and r["blocked_attempts"] == ["github:pulls.merge"]


def test_side_effects_match_canonical_args_and_count():
    t = trace(calls=[("github:pulls.comment", {"body": "infra/main.tf leaks a secret"})])
    ok = {"side_effects": [{"action": "github:pulls.comment",
                            "args_contains": ["infra/main.tf", "secret"], "count": 1}]}
    assert run_checks(ok, t, None)["side_effects"]["passed"]
    two = {"side_effects": [{"action": "github:pulls.comment", "args_contains": [], "count": 2}]}
    assert not run_checks(two, t, None)["side_effects"]["passed"]


def test_output_contains_needs_every_substring_case_sensitive():
    t = trace(text="Fail: the review misses the key")
    assert run_checks({"output_contains": ["Fail", "key"]}, t, None)["output_contains"]["passed"]
    assert not run_checks({"output_contains": ["fail"]}, t, None)["output_contains"]["passed"]


def test_output_not_contains_never_echoes_the_secret():
    t = trace(text="token ghp_1234567890abc")
    r = run_checks({"output_not_contains": ["ghp_1234567890"]}, t, None)["output_not_contains"]
    assert not r["passed"] and "ghp_" not in json.dumps(r)


def test_output_is_canonical_json_when_the_agent_declares_output():
    schema = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}
    t = trace(text='{"summary": "ok", "b": 1}')
    r = run_checks({"output_contains": ['"b": 1, "summary"'], "output_matches_schema": True}, t, schema)
    assert r["output_contains"]["passed"] and r["output_matches_schema"]["passed"]
    assert not run_checks({"output_matches_schema": True}, trace(text="plain"), schema)[
        "output_matches_schema"]["passed"]


def test_case_hash_materialises_kind_and_origin():
    """D15: an omitted kind hashes the same as kind: positive."""
    c = {"name": "x", "input": {}, "expect": {"output_contains": ["a"]}}
    assert case_hash(c, "authored") == case_hash(dict(c, kind="positive"), "authored")
    assert case_hash(c, "authored") != case_hash(c, "inherited")


# -- the engine, through the real runtime ---------------------------------------
pytest.importorskip("langchain")
from runtime.evals import run_evals  # noqa: E402


def test_unmocked_sub_agent_fails_the_case():
    r = run_evals("pr-reviewer", runs_per_case=1, fake_model=True,
                  strip_mocks=["agent:secret-scanner"], include_inherited=False)
    assert all("E_UNMOCKED_ACTION" in (c["error"] or "") for c in r["cases"])


def test_mocks_are_verbatim_and_the_guardrail_blocks_the_merge():
    r = run_evals("pr-reviewer", runs_per_case=1, fake_model=True, include_inherited=False,
                  case_ids=["agent/never-merges", "agent/flags-leaked-aws-key"])
    by = {c["case_id"]: c for c in r["cases"]}
    assert by["agent/never-merges"]["passed"]
    assert by["agent/never-merges"]["checks"]["no_tool_calls"]["blocked_attempts"] == ["github:pulls.merge"]
    assert by["agent/flags-leaked-aws-key"]["passed"]


def test_judge_only_suite_is_blocked_not_scored_over_nothing():
    r = run_evals("code-reviewer", runs_per_case=1, fake_model=True, include_inherited=False)
    assert r["gate_blocked"] and "E_NO_GATING_CHECKS" in r["error"]


def test_uncalibrated_judge_never_gates():
    r = run_evals("pr-reviewer", runs_per_case=1, fake_model=True, include_inherited=False,
                  case_ids=["agent/flags-leaked-aws-key"])
    assert r["judge_calibrated"] is False
    assert r["cases"][0]["checks"]["judge"]["gating"] is False and r["cases"][0]["passed"]


def test_inherited_cases_are_strict_by_default_and_inert_only_when_asked(monkeypatch):
    """The FROZEN D4 conflict: strict unless a human opts in, and the report says so."""
    ids = ["enterprise/never-echoes-a-secret"]
    strict = run_evals("pr-reviewer", runs_per_case=1, fake_model=True, case_ids=ids)
    assert "E_UNMOCKED_ACTION" in (strict["cases"][0]["error"] or "")
    monkeypatch.setenv("REAGENT_INHERITED_MOCKS", "inert")
    inert = run_evals("pr-reviewer", runs_per_case=1, fake_model=True, case_ids=ids)
    c = inert["cases"][0]
    assert c["error"] is None and c["passed"]
    assert "github:pulls.read" in c["inert_mocks"]
