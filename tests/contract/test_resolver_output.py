"""H1 — the resolver emits a valid resolved spec. Producer: wf/resolver.

Consumed by wf/runtime and wf/dashboard, which are both blocked until this is
green, so this is the highest-value seam on the board.
"""
from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from conftest import require_module


@pytest.fixture(scope="module")
def resolve():
    require_module("runtime.resolver", "H1")
    from runtime.resolver import resolve_agent  # type: ignore
    return resolve_agent


@pytest.mark.parametrize("agent", ["pr-reviewer", "people-ops-bot"])
def test_output_validates_against_the_contract(resolve, resolved_schema, agent):
    doc = resolve(agent, caller="alice")
    errs = sorted(Draft202012Validator(resolved_schema).iter_errors(doc),
                  key=lambda e: list(e.path))
    assert not errs, [(list(e.path), e.message[:90]) for e in errs[:5]]


def test_matches_the_golden_answer_key(resolve, root):
    """The fixture is a hand-resolved answer key. Where the resolver disagrees
    with it, one of the two is wrong and it must be settled, not papered over."""
    golden = json.loads(
        (root / "fixtures" / "resolved" / "pr-reviewer.admin.json").read_text())
    doc = resolve("pr-reviewer", caller="frank")

    assert doc["effective"]["limits"] == golden["effective"]["limits"]
    assert sorted(doc["effective"]["denylist"]) == sorted(golden["effective"]["denylist"])
    assert ({(t["connector"], t["action"]) for t in doc["effective"]["tool_permissions"]}
            == {(t["connector"], t["action"]) for t in golden["effective"]["tool_permissions"]})
    assert doc["effective"]["model"]["openrouter_model"] == \
        golden["effective"]["model"]["openrouter_model"]


def test_memory_is_expanded_into_tools(resolve):
    """FROZEN D12. Without this the agent has no memory access at all."""
    doc = resolve("pr-reviewer", caller="frank")
    mem = [t for t in doc["effective"]["tool_permissions"] if t["connector"] == "memory"]
    assert mem, "memory entries were not expanded into tools"
    assert {t["action"] for t in mem} >= {"read", "search"}


def test_a_member_cannot_see_enterprise_values(resolve):
    """FROZEN D3 and SPEC.md §2."""
    doc = resolve("pr-reviewer", caller="alice")
    assert doc["caller"]["is_enterprise_admin"] is False
    assert doc["effective"]["prompt"] is None
    ent = [p for p in doc["provenance"]["prompt"] if p["source"] == "enterprise"]
    assert ent and all(p["value"] is None and p.get("redacted") for p in ent)


def test_widening_is_refused_and_names_the_layer(resolve):
    """Demo step 4. The message text is contract."""
    doc = resolve("pr-reviewer", caller="alice",
                  overrides={"spec": {"budget": {"timeout_seconds": 900}}})
    assert doc["rejections"], "a 900s timeout must be refused against the 240s agent budget"
    r = doc["rejections"][0]
    assert r["code"].startswith("E_")
    assert "timeout_seconds" in r["field"]
    assert r["blocked_by"] in ("enterprise", "agent", "team:platform-eng")
    assert r["field"].split(".")[-1] in r["message"]
    assert r["blocked_by"] in r["message"]


def test_ungranted_action_is_refused(resolve):
    """Demo step 4, the merge action. platform-eng was never granted pulls.merge."""
    doc = resolve("pr-reviewer", caller="alice",
                  overrides={"spec": {"tools": [{"connector": "github",
                                                 "actions": ["pulls.merge"]}]}})
    assert doc["rejections"]
    assert any("merge" in r.get("message", "") or "merge" in str(r.get("attempted", ""))
               for r in doc["rejections"])
