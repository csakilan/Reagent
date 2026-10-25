"""Task 4: the publish pipeline. SPEC.md §11, FROZEN D8, D15, D17, D24."""
from __future__ import annotations

import copy

import pytest

pytest.importorskip("langchain")

from contracts.errors import ReAgentError  # noqa: E402
from runtime.publish import MemoryStore, abandon, acknowledge, proceed, review, start  # noqa: E402
from runtime.publish import pipeline  # noqa: E402
from runtime.resolver import load_catalog  # noqa: E402

_BASE = load_catalog()


@pytest.fixture
def cat():
    c = copy.deepcopy(_BASE)
    # The inherited default_cases carry no mocks, so under FROZEN D4 every
    # tool-using agent fails them. That conflict is reported, not decided here.
    c.ent["evaluation"]["default_cases"] = []
    return c


@pytest.fixture
def store():
    return MemoryStore()


def draft(cat, pass_rate=0.7):
    d = copy.deepcopy(cat.latest("pr-reviewer"))
    d["spec"]["evaluation"]["thresholds"]["pass_rate"] = pass_rate
    return d


def approve_all(run, store):
    for c in run["cases"]:
        if c["decision"] is None:
            review(run["id"], c["case_id"], "approve", "alice", store=store)
    acknowledge(run["id"], "alice", store=store)


def go(run, store, cat):
    return proceed(run["id"], store=store, catalog=cat, fake_model=True, runs_per_case=1)


def test_stops_at_human_review_and_refuses_to_continue_unreviewed(cat, store):
    run = start("pr-reviewer", "alice", draft(cat), store=store, catalog=cat)
    assert (run["stage"], run["status"]) == ("review", "running")
    with pytest.raises(ReAgentError) as e:
        go(run, store, cat)
    assert e.value.rejections[0].code.value == "E_UNREVIEWED_CASES"


def test_full_run_reaches_stage_7_and_pins_sub_agents(cat, store):
    run = start("pr-reviewer", "alice", draft(cat), store=store, catalog=cat)
    approve_all(run, store)
    run = go(run, store, cat)
    assert (run["stage"], run["status"]) == ("publish", "succeeded"), run["stage_results"]
    v = store.latest_version("pr-reviewer")
    assert v["sub_agents"] == [{"name": "secret-scanner", "version": "1.1.0"}]       # D24
    assert v["report"]["pass_rate"] >= 0.7


def test_one_running_pipeline_per_agent(cat, store):
    run = start("pr-reviewer", "alice", draft(cat), store=store, catalog=cat)
    with pytest.raises(ReAgentError) as e:
        start("pr-reviewer", "bob", draft(cat), store=store, catalog=cat)
    assert e.value.rejections[0].code.value == "E_PIPELINE_ALREADY_RUNNING"
    abandon(run["id"], store=store)
    assert start("pr-reviewer", "bob", draft(cat), store=store, catalog=cat)["status"] == "running"


def test_editing_a_case_after_approval_voids_it(cat, store):
    run = start("pr-reviewer", "alice", draft(cat), store=store, catalog=cat)
    approve_all(run, store)
    run["cases"][0]["_case"]["expect"]["output_not_contains"] = []        # tampered after approval
    run = go(run, store, cat)
    assert run["status"] == "failed"
    assert run["stage_results"]["review"]["rejections"][0]["code"] == "E_CASE_HASH_MISMATCH"


def test_approved_cases_carry_over_to_the_next_pipeline(cat, store):
    run = start("pr-reviewer", "alice", draft(cat), store=store, catalog=cat)
    approve_all(run, store)
    assert go(run, store, cat)["status"] == "succeeded"
    again = start("pr-reviewer", "alice", draft(cat), store=store, catalog=cat)
    assert all(c["decision"] == "approved" for c in again["cases"])


def test_a_rejected_case_needs_a_reason_and_is_not_proposed_again(cat, store):
    d = draft(cat)
    for c in d["spec"]["evaluation"]["cases"]:
        c.get("mocks", {}).pop("memory:read", None)           # leaves a coverage gap to propose
    run = start("pr-reviewer", "alice", d, store=store, catalog=cat)
    gen = [c for c in run["cases"] if c["origin"] == "generated"]
    assert [c["case_id"] for c in gen] == ["agent/exercises-memory-read"]
    with pytest.raises(ValueError):
        review(run["id"], gen[0]["case_id"], "reject", "alice", store=store)
    review(run["id"], gen[0]["case_id"], "reject", "alice", reason="memory reads are incidental",
           store=store)
    abandon(run["id"], store=store)
    again = start("pr-reviewer", "alice", d, store=store, catalog=cat)
    assert not [c for c in again["cases"] if c["origin"] == "generated"]


def test_gate_blocks_below_threshold(cat, store):
    run = start("pr-reviewer", "alice", draft(cat, pass_rate=1.0), store=store, catalog=cat)
    approve_all(run, store)
    run = go(run, store, cat)
    assert (run["stage"], run["status"]) == ("gate", "failed")
    assert run["stage_results"]["gate"]["rejections"][0]["code"] == "E_THRESHOLD_NOT_MET"


def test_layers_moving_mid_pipeline_fails_stage_7(cat, store, monkeypatch):
    real = pipeline.run_evals

    def evals_while_admin_edits_the_team(*a, **kw):
        out = real(*a, **kw)
        cat.teams["platform-eng"]["spec"]["connectors"]["github"]["allow"] = ["pulls.read"]
        return out

    monkeypatch.setattr(pipeline, "run_evals", evals_while_admin_edits_the_team)
    run = start("pr-reviewer", "alice", draft(cat), store=store, catalog=cat)
    approve_all(run, store)
    run = go(run, store, cat)
    assert (run["stage"], run["status"]) == ("publish", "failed")
    assert run["stage_results"]["publish"]["rejections"][0]["code"] == "E_LAYERS_CHANGED_MID_PIPELINE"


def test_judge_only_suite_cannot_publish(cat, store):
    """D8, through the pipeline: code-reviewer's suite fails before evals run."""
    run = start("code-reviewer", "bob", store=store, catalog=cat)
    approve_all(run, store)
    run = go(run, store, cat)
    assert run["status"] == "failed"


def test_seeded_platform_agents_skip_the_pipeline(cat, store):
    with pytest.raises(ReAgentError):
        start("evaluator", "frank", store=store, catalog=cat)


def test_inherited_cases_are_not_member_reviewable(cat, store):
    c = copy.deepcopy(_BASE)
    run = start("pr-reviewer", "alice", draft(c), store=store, catalog=c)
    inh = next(x for x in run["cases"] if x["origin"] == "inherited")
    assert inh["case"] is None and inh["decision"] == "approved"
    with pytest.raises(ReAgentError):
        review(run["id"], inh["case_id"], "approve", "alice", store=store)
