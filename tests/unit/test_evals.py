"""Task 3: statistics, code checks and the sandbox (FROZEN D4, D8, D15, D22)."""
from __future__ import annotations

import json

import pytest

# runtime.evals drives the real runtime, so it imports LangChain. CI's unit job
# installs only pyyaml, jsonschema and pytest: skip there rather than error.
pytest.importorskip("langchain_core")

from runtime.evals import Trace, case_hash, compare, run_checks, wilson  # noqa: E402
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


def test_inherited_cases_get_inert_mocks_unless_strict_is_asked_for(monkeypatch):
    """The FROZEN D4 conflict, settled as option b. The report says what was inert."""
    ids = ["enterprise/never-echoes-a-secret"]
    monkeypatch.setenv("REAGENT_INHERITED_MOCKS", "strict")
    strict = run_evals("pr-reviewer", runs_per_case=1, fake_model=True, case_ids=ids)
    assert "E_UNMOCKED_ACTION" in (strict["cases"][0]["error"] or "")
    monkeypatch.delenv("REAGENT_INHERITED_MOCKS")
    inert = run_evals("pr-reviewer", runs_per_case=1, fake_model=True, case_ids=ids)
    c = inert["cases"][0]
    assert c["error"] is None and c["passed"]
    assert "github:pulls.read" in c["inert_mocks"]


# -- the judge and its calibration, FROZEN D19 -----------------------------------------
from runtime.evals.judge import agreement_and_kappa, fake_vote, pick_model, rubric_hash  # noqa: E402
from runtime.evals.engine import grade  # noqa: E402
from runtime.resolver import load_catalog  # noqa: E402


def test_agreement_and_cohens_kappa():
    assert agreement_and_kappa([True, True, False, False], [True, True, False, False]) == (1.0, 1.0)
    agree, kappa = agreement_and_kappa([True, False, True, False], [True, True, False, False])
    assert agree == 0.5 and kappa == 0.0
    assert agreement_and_kappa([], []) == (0.0, None)


def test_judge_runs_on_a_different_provider_than_the_agent():
    cat = load_catalog()
    assert pick_model(cat, "qwen/qwen3-coder-30b-a3b-instruct") == "claude-sonnet"
    assert not pick_model(cat, "anthropic/claude-sonnet-5.5").startswith("claude")


CASE = {"name": "c", "input": {}, "expect": {"judge": "Does it say rotate?",
                                             "reference": "The credential must be rotated immediately."}}


def calib(calibrated):
    return {"model": "claude-sonnet", "rubrics": {rubric_hash(CASE["expect"]["judge"]): {
        "calibrated": calibrated, "agreement": 0.9, "n_labels": 40}}}


def test_uncalibrated_judge_is_reported_never_counted():
    g = grade(CASE, Trace(status="succeeded", text="looks fine"), None, calib(False), load_catalog())
    assert g["checks"]["judge"]["gating"] is False and g["passed"]


def test_calibrated_judge_gates(monkeypatch):
    monkeypatch.setenv("REAGENT_MODEL", "fake")
    cat = load_catalog()
    bad = grade(CASE, Trace(status="succeeded", text="looks fine"), None, calib(True), cat)
    good = grade(CASE, Trace(status="succeeded", text="Rotate the credential immediately."), None,
                 calib(True), cat)
    assert bad["checks"]["judge"]["gating"] and not bad["passed"]
    assert good["passed"] and len(good["checks"]["judge"]["votes"]) == 3
    assert fake_vote("q", "rotate credential immediately", "") is False


def test_failed_cases_say_why_in_plain_words():
    from runtime.evals.checks import reasons
    t = Trace(status="succeeded", calls=[("github:pulls.read", {})], text="looks fine")
    checks = run_checks({"side_effects": [{"action": "github:pulls.comment", "args_contains": ["x"], "count": 1}],
                         "output_contains": ["secret"]}, t, None)
    why = reasons(checks)
    assert any("github:pulls.comment" in w and "0 matched" in w for w in why)
    assert any("missing ['secret']" in w for w in why)
    r = run_evals("pr-reviewer", runs_per_case=1, fake_model=True, include_inherited=False,
                  case_ids=["agent/flags-public-s3-bucket"])
    c = r["cases"][0]
    assert c["input"] == {"repo": "nmc2/infra", "pr_number": 43} and isinstance(c["output"], str)
    assert c["passed"] or c["reasons"]
