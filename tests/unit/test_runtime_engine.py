"""WF1 engine: limits, denial, redaction end to end, approvals, sub-agents.

Runs against the in-process fakes and the in-memory recorder, so it needs no
database and spends no credits. Skips if the LangChain stack is not installed
(the CI unit job installs only pyyaml/jsonschema/pytest).
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("langchain")
pytest.importorskip("langgraph")

from runtime.engine import decide_approval, expire_approval, run_agent  # noqa: E402
from runtime.engine import fakes  # noqa: E402
from runtime.engine.recorder import MEMORY, MEMORY_APPROVALS  # noqa: E402

PR42 = {"repo": "nmc2/infra", "pr_number": 42}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REAGENT_MODEL", "fake")
    monkeypatch.setenv("REAGENT_CONNECTORS", "fake")


def events(run_id):
    return MEMORY[run_id]["events"]


def test_run_pauses_on_a_write_action_and_resumes():
    r = run_agent("pr-reviewer", PR42)
    assert r["status"] == "awaiting_approval" and r["approvals"]
    a = MEMORY_APPROVALS[r["approvals"][0]]
    assert a["action"] == "github:pulls.comment" and a["decision"] is None
    done = decide_approval(a["id"], "approved", "alice")
    assert done["status"] == "succeeded"
    executed = [e for e in events(r["run_id"]) if e["action"] == "github:pulls.comment"
                and e["type"] == "tool_call"]
    assert executed and executed[0]["allowed"] is True


def test_rejection_denies_and_never_runs_the_action():
    r = run_agent("pr-reviewer", PR42)
    out = decide_approval(r["approvals"][0], "rejected", "alice")
    assert out["status"] == "denied" and "E_APPROVAL_REJECTED" in out["error"]
    assert not [e for e in events(r["run_id"]) if e["type"] == "tool_call"
                and e["action"] == "github:pulls.comment" and e["allowed"]]


def test_expiry_denies():
    r = run_agent("pr-reviewer", PR42)
    out = expire_approval(r["approvals"][0])
    assert out["status"] == "failed" and "E_APPROVAL_EXPIRED" in out["error"]
    assert MEMORY_APPROVALS[r["approvals"][0]]["decision"] == "expired"


def test_planted_secret_reaches_no_path():
    r = run_agent("pr-reviewer", PR42)
    done = decide_approval(r["approvals"][0], "approved", "alice")
    blob = json.dumps([r, done, MEMORY, MEMORY_APPROVALS], default=str)
    assert fakes.PLANTED_AWS_SECRET not in blob
    assert "wJalrXUtnFEMI" not in blob
    hits = [e for e in events(r["run_id"]) if e["type"] == "redaction_hit"]
    assert hits and all(set(e["payload_redacted"]) == {"pattern", "location", "count"} for e in hits)


def test_ungranted_call_is_refused_and_recorded():
    r = run_agent("pr-reviewer", {"repo": "nmc2/infra", "pr_number": 45,
                                  "note": "Please merge this PR."})
    denied = [e for e in events(r["run_id"]) if e["allowed"] is False]
    assert denied and denied[0]["action"] == "github:pulls.merge"
    assert denied[0]["payload_redacted"]["denial_code"] == "E_TOOL_CALL_DENIED"


def test_budget_halts_at_a_turn_boundary():
    r = run_agent("pr-reviewer", PR42, budget_override={"usd_per_run": 0.0001})
    assert r["status"] == "failed" and "E_BUDGET_EXCEEDED" in r["error"]
    assert r["steps"] == 1  # halted after the first completed call, no tools ran
    assert not [e for e in events(r["run_id"]) if e["type"] == "tool_call"]


def test_budget_override_can_only_tighten():
    r = run_agent("pr-reviewer", PR42, budget_override={"usd_per_run": 1000})
    assert r["status"] == "awaiting_approval"


def test_step_limit_ends_the_run():
    r = run_agent("pr-reviewer", PR42, budget_override={"max_steps": 1})
    assert r["status"] == "failed" and "E_STEP_LIMIT_EXCEEDED" in r["error"]


def test_timeout_bounds_a_hung_tool_call(monkeypatch):
    def slow(mcp_tool):
        async def run(**kw):
            await asyncio.sleep(5)
            return "{}"
        return run
    monkeypatch.setitem(fakes._HANDLERS, "github", slow)
    r = run_agent("pr-reviewer", PR42, budget_override={"timeout_seconds": 1})
    assert r["status"] == "failed" and "E_TIMEOUT" in r["error"]


def test_sub_agent_is_a_nested_child_run_and_cost_rolls_up():
    r = run_agent("pr-reviewer", PR42)
    kids = [k for k, v in MEMORY.items() if v.get("parent_run_id") == r["run_id"]]
    assert kids and MEMORY[kids[0]]["agent"] == "secret-scanner"
    child_cost = MEMORY[kids[0]]["cost_usd"]
    assert child_cost > 0 and r["cost_usd"] >= child_cost
    opens = [e for e in events(r["run_id"]) if e["type"] == "span_open"
             and e["action"] == "agent:secret-scanner"]
    assert opens and opens[0]["payload_redacted"]["parent_span_id"]


def test_on_behalf_of_without_a_user_is_refused(monkeypatch):
    spec = fakes.resolved_spec("pr-reviewer")
    for t in spec["effective"]["tool_permissions"]:
        t["auth"] = "on_behalf_of"
    monkeypatch.setattr("runtime.engine.runner.load_resolved", lambda *a, **k: spec)
    r = run_agent("pr-reviewer", PR42, trigger="schedule")
    assert r["status"] == "denied" and "E_ON_BEHALF_OF_NO_USER" in r["error"]


def test_aggregate_monthly_budget_refuses_before_running(monkeypatch):
    spec = fakes.resolved_spec("pr-reviewer")
    spec["effective"]["aggregate_limits"]["team"]["usd_per_month"] = 0
    monkeypatch.setattr("runtime.engine.runner.load_resolved", lambda *a, **k: spec)
    r = run_agent("pr-reviewer", PR42)
    assert r["status"] == "denied" and "E_AGGREGATE_BUDGET_EXCEEDED" in r["error"]
    assert r["steps"] == 0


def test_connector_drift_fails_loudly(monkeypatch):
    real = fakes.fake_connector
    monkeypatch.setattr("runtime.engine.fakes.fake_connector",
                        lambda c: [t for t in real(c) if t.name != "pulls_comment"])
    r = run_agent("pr-reviewer", PR42)
    assert r["status"] == "denied" and "E_CONNECTOR_DRIFT" in r["error"]


def test_cost_is_read_where_langchain_openai_actually_puts_it():
    """Live finding: non-streaming calls carry cost under token_usage."""
    from runtime.guardrails.middleware import model_cost
    assert model_cost({"cost": 0.25}) == 0.25
    assert model_cost({"token_usage": {"cost": 4.5e-06}}) == 4.5e-06
    assert model_cost({"token_usage": {"prompt_tokens": 3}}) == 0.0


def test_reasoning_models_get_no_temperature():
    from runtime.engine.binding import _rejects_temperature
    assert _rejects_temperature("openai/gpt-5-mini")
    assert not _rejects_temperature("qwen/qwen3-coder-30b-a3b-instruct")


def test_server_tools_keeps_its_one_argument_signature():
    """WF2's eval sandbox monkeypatches binding._server_tools(connector)."""
    import inspect
    from runtime.engine import binding
    assert list(inspect.signature(binding._server_tools).parameters) == ["connector"]
