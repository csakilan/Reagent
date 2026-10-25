"""The pipeline on Postgres, and the resolver reading layers from it.

Runs only against a LOCAL database (the docker-compose fallback), because it
truncates and reseeds tables. It refuses any non-localhost URL, so it can never
touch the shared Tiger Cloud database:

    docker compose up -d db
    REAGENT_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/reagent pytest tests/unit/test_publish_pg.py
"""
from __future__ import annotations

import copy
import os
import urllib.parse

import pytest

URL = os.environ.get("REAGENT_TEST_DATABASE_URL")
if not URL:
    pytest.skip("REAGENT_TEST_DATABASE_URL not set (local database only)", allow_module_level=True)
if urllib.parse.urlparse(URL).hostname not in ("localhost", "127.0.0.1"):
    pytest.skip("refusing a non-local database: this test truncates tables", allow_module_level=True)
psycopg = pytest.importorskip("psycopg")
pytest.importorskip("langchain")

from psycopg.types.json import Jsonb  # noqa: E402

from contracts.errors import ReAgentError  # noqa: E402
from runtime.publish import acknowledge, proceed, review, start  # noqa: E402
from runtime.publish.pgstore import PgStore  # noqa: E402
from runtime.resolver import load_catalog, resolve_agent  # noqa: E402
from runtime.resolver.source import load_catalog_db  # noqa: E402

TABLES = ("case_reviews", "publish_runs", "eval_runs", "agents", "agent_versions", "team_members",
          "memory_buckets", "registry_items", "teams", "enterprise", "users")


def seed(cat) -> None:
    """The example files, written the way the data seed lays them out."""
    with psycopg.connect(URL) as c:
        c.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
        people = set(cat.admins()) | {m for t in cat.teams.values() for m in t["spec"]["members"]}
        for p in sorted(people):
            c.execute("INSERT INTO users (name, email, is_enterprise_admin) VALUES (%s, %s, %s)",
                      (p, f"{p}@nmc2.test", p in cat.admins()))
        c.execute("INSERT INTO enterprise (name, spec) VALUES (%s, %s)",
                  (cat.enterprise_name, Jsonb(cat.enterprise)))
        for n, t in cat.teams.items():
            c.execute("INSERT INTO teams (name, spec) VALUES (%s, %s)", (n, Jsonb(t)))
        for kind, docs in (("connector", cat.connectors), ("model", cat.models)):
            for n, d in docs.items():
                c.execute("INSERT INTO registry_items (kind, name, version, spec) VALUES (%s, %s, %s, %s)",
                          (kind, n, d["metadata"]["version"], Jsonb(d)))
        for name, versions in cat.agents.items():
            for v, d in versions.items():
                md = d["metadata"]
                team = c.execute("SELECT id FROM teams WHERE name = %s", (md.get("team"),)).fetchone()
                aid = c.execute("INSERT INTO agents (name, level, team_id, access) VALUES (%s,%s,%s,%s) "
                                "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                                (name, md["level"], team[0] if team else None,
                                 md.get("access") if md["level"] == "enterprise" else None)).fetchone()[0]
                platform = md["level"] == "enterprise"
                c.execute("INSERT INTO agent_versions (agent_id, version, status, spec, origin) "
                          "VALUES (%s, %s, %s, %s, %s)",
                          (aid, v, "published" if platform else "draft", Jsonb(d),
                           "platform" if platform else "user"))


@pytest.fixture
def cat():
    c = copy.deepcopy(load_catalog())
    c.ent["evaluation"]["default_cases"] = []        # see test_publish.py: the D4 conflict
    return c


@pytest.fixture
def store(cat):
    seed(cat)
    return PgStore(URL)


def test_layers_are_read_from_the_database_on_every_resolve(store, cat):
    """SPEC.md §1: a policy change reaches the agent on its next run."""
    db = load_catalog_db(URL)
    assert resolve_agent("pr-reviewer", catalog=db)["rejections"] == []
    with psycopg.connect(URL) as c:
        c.execute("UPDATE enterprise SET spec = jsonb_set(spec, '{spec,limits,timeout_seconds}', '120')")
    r = resolve_agent("pr-reviewer", catalog=load_catalog_db(URL))["rejections"]
    assert r and r[0]["code"] == "E_LIMIT_ABOVE_PARENT" and r[0]["blocked_by"] == "enterprise"


def test_full_pipeline_persists_and_publishes(store, cat):
    d = copy.deepcopy(cat.latest("pr-reviewer"))
    d["spec"]["evaluation"]["thresholds"]["pass_rate"] = 0.7
    run = start("pr-reviewer", "alice", d, store=store, catalog=cat)
    with pytest.raises(ReAgentError):
        start("pr-reviewer", "bob", d, store=store, catalog=cat)       # unique partial index
    for c in store.get(run["id"])["cases"]:
        if c["decision"] is None:
            review(run["id"], c["case_id"], "approve", "alice", store=store)
    acknowledge(run["id"], "alice", store=store)
    run = proceed(run["id"], store=store, catalog=cat, fake_model=True, runs_per_case=1)
    assert (run["stage"], run["status"]) == ("publish", "succeeded"), run["stage_results"]
    with psycopg.connect(URL) as c:
        status, spec = c.execute(
            "SELECT v.status, v.spec FROM agents a JOIN agent_versions v ON v.id = a.current_version_id "
            "WHERE a.name = 'pr-reviewer'").fetchone()
        reviews = c.execute("SELECT count(*) FROM case_reviews WHERE publish_run_id = %s",
                            (run["id"],)).fetchone()[0]
        evals = c.execute("SELECT count(*) FROM eval_runs").fetchone()[0]
    assert status == "published" and reviews == 4 and evals == 1
    assert spec["spec"]["sub_agents"][0]["ref"] == "secret-scanner@1.1.0"          # D24
    assert store.approved_hashes("pr-reviewer")
    with pytest.raises(ReAgentError):                       # a published version is immutable
        start("pr-reviewer", "alice", d, store=store, catalog=cat)


def test_fork_lands_in_the_database_with_its_lineage(store, cat):
    vid = store.create_draft(dict(copy.deepcopy(cat.latest("code-reviewer")),
                                  metadata={"name": "pg-fork", "version": "0.1.0", "level": "team",
                                            "team": "platform-eng", "forked_from": "code-reviewer@1.2.0"}),
                             forked_from=("code-reviewer", "1.2.0"))
    with psycopg.connect(URL) as c:
        src = c.execute("SELECT a.name, s.version FROM agent_versions v JOIN agent_versions s "
                        "ON s.id = v.forked_from_version_id JOIN agents a ON a.id = s.agent_id "
                        "WHERE v.id = %s", (vid,)).fetchone()
    assert src == ("code-reviewer", "1.2.0")
    assert "pg-fork" in load_catalog_db(URL).agents
