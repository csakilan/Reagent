"""H4 — the database exists, is migrated, and is seeded. Producer: wf/data."""
from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg", reason="psycopg not installed yet")

EXPECTED_TABLES = {
    "enterprise", "teams", "users", "team_members", "registry_items",
    "connector_requests", "agents", "agent_versions", "runs", "run_events",
    "approvals", "eval_runs", "judge_calibration", "calibration_labels",
    "publish_runs", "case_reviews", "memory_buckets", "memory_items",
}


@pytest.fixture(scope="module")
def conn(database_url):
    with psycopg.connect(database_url) as c:
        yield c


def test_every_table_exists(conn):
    got = {r[0] for r in conn.execute(
        "select table_name from information_schema.tables where table_schema='public'")}
    assert EXPECTED_TABLES <= got, f"missing: {sorted(EXPECTED_TABLES - got)}"


def test_runs_and_run_events_are_hypertables(conn):
    """The Tiger Data track rests on this, and the composite PK depends on it."""
    got = {r[0] for r in conn.execute(
        "select hypertable_name from timescaledb_information.hypertables")}
    assert {"runs", "run_events"} <= got, f"not hypertables: {got}"


def test_generated_columns_compute(conn):
    got = {f"{r[0]}.{r[1]}" for r in conn.execute(
        "select table_name, column_name from information_schema.columns "
        "where is_generated='ALWAYS'")}
    assert {"agent_versions.description", "agent_versions.desired_outcome",
            "agent_versions.search", "runs.duration_ms"} <= got


def test_embedding_is_writable(conn):
    """A generated vector column COMPILES but cannot be written, which is the
    trap. This proves it is a plain column."""
    row = conn.execute(
        "select is_generated from information_schema.columns "
        "where table_name='agent_versions' and column_name='embedding'").fetchone()
    assert row is not None, "agent_versions.embedding missing"
    assert row[0] == "NEVER", "embedding must be a plain column the app writes"


def test_one_running_pipeline_per_agent(conn):
    """FROZEN D17."""
    got = {r[0] for r in conn.execute(
        "select indexname from pg_indexes where tablename='publish_runs'")}
    assert "publish_runs_one_active" in got


def test_seeded_enough_to_not_look_empty(conn):
    """MVP row 10. An empty marketplace reads as a broken product on stage."""
    counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0]
              for t in ("teams", "users", "registry_items", "agents",
                        "agent_versions", "runs")}
    if all(v == 0 for v in counts.values()):
        pytest.skip("not seeded yet — waiting on H4 task 4")
    assert counts["teams"] >= 2, counts
    assert counts["agents"] >= 4, counts
    assert counts["registry_items"] >= 6, counts
    assert counts["runs"] >= 10, f"analytics needs run history: {counts}"


def test_meta_agents_are_seeded_published(conn):
    """FROZEN D8: they skip the pipeline, so they must already be published."""
    rows = conn.execute(
        "select a.name, v.status, v.origin from agents a "
        "join agent_versions v on v.id = a.current_version_id "
        "where a.level='enterprise'").fetchall()
    if not rows:
        pytest.skip("enterprise agents not seeded yet — waiting on H4")
    for name, status, origin in rows:
        assert status == "published", f"{name} is {status}"
        assert origin == "platform", f"{name} has origin {origin}, expected platform"
