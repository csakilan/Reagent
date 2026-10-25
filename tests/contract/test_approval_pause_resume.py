"""H6 — a write action pauses, queues, and resumes. Producer: wf/runtime.

This needs a Postgres checkpointer and durability="sync". With InMemorySaver or
the default async durability it silently does not work across processes.
"""
from __future__ import annotations

import pytest

from conftest import require_module


@pytest.fixture(scope="module")
def engine():
    require_module("runtime.engine", "H6")
    import runtime.engine as e  # type: ignore
    return e


def test_a_write_action_pauses_and_writes_an_approval_row(engine, database_url):
    import psycopg
    result = engine.run_agent("pr-reviewer", {"repo": "nmc2/infra", "pr_number": 42})
    assert result.get("status") == "awaiting_approval", \
        "github:pulls.comment is risk: write and platform-eng requires approval for write"
    with psycopg.connect(database_url) as c:
        row = c.execute(
            "select action, decision from approvals where run_id=%s",
            (result["run_id"],)).fetchone()
    assert row, "no approvals row written"
    assert row[0] == "github:pulls.comment"
    assert row[1] is None, "a fresh approval must be undecided"


def test_approving_resumes_the_run_in_another_call(engine, database_url):
    """Resume is keyed only by thread_id, so a separate call must be able to
    continue it. This is the whole point of the Postgres checkpointer."""
    import psycopg
    result = engine.run_agent("pr-reviewer", {"repo": "nmc2/infra", "pr_number": 42})
    with psycopg.connect(database_url) as c:
        approval_id = c.execute(
            "select id from approvals where run_id=%s", (result["run_id"],)).fetchone()[0]
    final = engine.decide_approval(approval_id, decision="approved", approver="alice")
    assert final.get("status") == "succeeded"
    with psycopg.connect(database_url) as c:
        d = c.execute("select decision, decided_at from approvals where id=%s",
                      (approval_id,)).fetchone()
    assert d[0] == "approved" and d[1] is not None


def test_rejecting_fails_the_run_without_performing_the_action(engine, database_url):
    import psycopg
    result = engine.run_agent("pr-reviewer", {"repo": "nmc2/infra", "pr_number": 42})
    with psycopg.connect(database_url) as c:
        approval_id = c.execute(
            "select id from approvals where run_id=%s", (result["run_id"],)).fetchone()[0]
    final = engine.decide_approval(approval_id, decision="rejected", approver="alice")
    assert final.get("status") in ("failed", "denied")
    with psycopg.connect(database_url) as c:
        posted = c.execute(
            "select count(*) from run_events where run_id=%s and action='github:pulls.comment' "
            "and allowed = true", (result["run_id"],)).fetchone()[0]
    assert posted == 0, "the comment must not be posted after a rejection"


def test_expiry_denies_rather_than_proceeding(engine, database_url):
    """FROZEN D16. The spec never said; this is the frozen answer."""
    import psycopg
    result = engine.run_agent("pr-reviewer", {"repo": "nmc2/infra", "pr_number": 42})
    with psycopg.connect(database_url) as c:
        approval_id = c.execute(
            "select id from approvals where run_id=%s", (result["run_id"],)).fetchone()[0]
    if not hasattr(engine, "expire_approval"):
        pytest.skip("expire_approval not implemented yet")
    final = engine.expire_approval(approval_id)
    assert final.get("status") in ("failed", "denied")
    with psycopg.connect(database_url) as c:
        d = c.execute("select decision from approvals where id=%s", (approval_id,)).fetchone()
    assert d[0] == "expired"
