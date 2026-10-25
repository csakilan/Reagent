"""H5 — the eval engine returns a full report. Producer: wf/resolver."""
from __future__ import annotations

import pytest

from conftest import require_module

REQUIRED = {"pass_rate", "pass_rate_ci_low", "pass_rate_ci_high", "pass_k",
            "recall", "false_alarm_rate", "safety_pass_rate",
            "avg_cost", "p95_latency"}


@pytest.fixture(scope="module")
def run_evals():
    require_module("runtime.evals", "H5")
    from runtime.evals import run_evals  # type: ignore
    return run_evals


def test_report_carries_every_metric(run_evals):
    r = run_evals("pr-reviewer", runs_per_case=2, fake_model=True)
    missing = REQUIRED - set(r)
    assert not missing, f"missing metrics: {sorted(missing)}"


def test_confidence_interval_brackets_the_pass_rate(run_evals):
    """SPEC.md §7 is explicit that the platform never claims an improvement that
    is really noise, so the interval has to be real."""
    r = run_evals("pr-reviewer", runs_per_case=5, fake_model=True)
    assert r["pass_rate_ci_low"] <= r["pass_rate"] <= r["pass_rate_ci_high"]
    assert r["pass_rate_ci_low"] < r["pass_rate_ci_high"], "a point estimate is not an interval"


def test_safety_cases_are_reported_separately(run_evals):
    r = run_evals("pr-reviewer", runs_per_case=2, fake_model=True)
    assert 0.0 <= r["safety_pass_rate"] <= 1.0


def test_an_unmocked_action_fails_the_case(run_evals):
    """FROZEN D4. pr-reviewer calls agent:secret-scanner, and a case that does
    not mock it must fail rather than spend money nondeterministically."""
    r = run_evals("pr-reviewer", runs_per_case=1, fake_model=True,
                  strip_mocks=["agent:secret-scanner"])
    assert any("UNMOCKED" in str(c.get("error", "")).upper()
               for c in r.get("cases", [])), \
        "an unmocked sub-agent call must fail the case with E_UNMOCKED_ACTION"


def test_a_judge_only_suite_cannot_gate(run_evals):
    """FROZEN D8. code-reviewer's only case is judge-only, so its pass rate
    would otherwise be computed over an empty set."""
    r = run_evals("code-reviewer", runs_per_case=1, fake_model=True)
    assert r.get("gate_blocked") or "NO_GATING_CHECKS" in str(r.get("error", "")).upper()
