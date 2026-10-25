"""Offline checks for the demo seed; live schema checks remain the H4 gate."""
from datetime import datetime, timezone
from pathlib import Path
import json

from fixtures.seed.seed import load_fixture_data, history_rows, version_state, seed
import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_seed_contains_the_required_marketplace_and_identity_data():
    data = load_fixture_data(ROOT)
    assert len(data['teams']) == 3
    assert len(data['connectors']) == 4
    assert len(data['models']) == 4
    assert len(data['agents']) >= 6
    names = {t['metadata']['name'] for t in data['teams']}
    assert {'platform-eng', 'people-ops'} <= names
    for agent in data['agents']:
        if agent['metadata']['level'] == 'team':
            assert agent['metadata']['team'] in names
    json.dumps(data)  # All documents must be JSONB-serializable.


def test_only_platform_agents_bypass_the_publish_pipeline():
    data = load_fixture_data(ROOT)
    for doc in data['agents']:
        expected = ('published', 'platform') if doc['metadata']['level'] == 'enterprise' else ('draft', 'user')
        assert version_state(doc) == expected
    assert any(a['metadata']['name'] == 'secret-scanner' for a in data['agents'])


def test_all_declared_members_approvers_and_admins_have_users():
    data = load_fixture_data(ROOT)
    users = set(data['users'])
    assert set(data['enterprise']['spec']['admins']) <= users
    for team in data['teams']:
        assert set(team['spec']['members']) <= users
        assert set(team['spec']['approvers']) <= set(team['spec']['members'])


def test_memory_buckets_have_global_unique_names_and_scoped_team_acls():
    data = load_fixture_data(ROOT)
    names = []
    for doc in [data['enterprise'], *data['teams']]:
        for bucket in doc['spec'].get('memory', {}).get('buckets', []):
            names.append(bucket['name'])
            if doc['kind'] == 'Team':
                assert set(bucket.get('read', [])) <= {doc['metadata']['name']}
                assert set(bucket.get('write', [])) <= {doc['metadata']['name']}
    assert len(names) == len(set(names))


def test_seeded_history_is_deterministic_distributed_and_explicitly_synthetic():
    now = datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc)
    rows = history_rows(now)
    assert rows == history_rows(now)
    assert len(rows) >= 10
    assert len({r['team_name'] for r in rows}) == 3
    assert len({r['started_at'].date() for r in rows}) >= 7
    assert {'succeeded', 'failed', 'denied'} <= {r['status'] for r in rows}
    for row in rows:
        assert row['started_at'] < row['ended_at'] <= now
        assert row['cost_usd'] > 0
        assert row['payload_redacted']['synthetic'] is True
        assert 'input' not in row and 'output' not in row


def test_every_run_uses_a_bootstrapped_platform_agent_and_known_caller_team():
    data = load_fixture_data(ROOT)
    published = {d['metadata']['name'] for d in data['agents'] if version_state(d)[0] == 'published'}
    teams = {t['metadata']['name'] for t in data['teams']}
    for row in history_rows(datetime.now(timezone.utc)):
        assert row['agent_name'] in published
        assert row['team_name'] in teams


class ExistingDatabase:
    """A database with the fixtures already present and edited memberships."""
    def __init__(self, conflicting_bucket=False):
        self.calls = []
        self.conflicting_bucket = conflicting_bucket
        self.buckets = {}
        data = load_fixture_data(ROOT)
        for doc in [data['enterprise'], *data['teams']]:
            for b in doc['spec'].get('memory', {}).get('buckets', []):
                self.buckets[b['name']] = (b['type'], 1 if doc['kind'] == 'Team' else None,
                                           b.get('read', []), b.get('write', []))

    def execute(self, statement, params=()):
        q = str(statement).upper()
        self.calls.append(q)
        rows = [(1,)]
        if q.startswith('INSERT') and 'RETURNING' in q:
            rows = []  # All rows already exist.
        elif q.startswith('SELECT TYPE,OWNER_TEAM_ID'):
            rows = [('semantic', None, ['*'], ['admins'])] if self.conflicting_bucket else [self.buckets[params[0]]]
        elif 'GROUP BY KIND' in q:
            rows = [('connector', 4), ('model', 4)]
        elif 'COUNT(*)' in q:
            rows = [(30,)]
        elif q.startswith('SELECT A.NAME'):
            rows = [('secret-scanner', 'published', 'platform')]
        class Result:
            def fetchone(self):
                return rows[0] if rows else None
            def fetchall(self):
                return rows
        return Result()


def test_rerun_preserves_existing_memberships_and_immutable_version_lineage():
    conn = ExistingDatabase()
    seed(conn, load_fixture_data(ROOT))
    assert not any(q.startswith('INSERT INTO TEAM_MEMBERS') for q in conn.calls)
    assert not any(q.startswith('UPDATE AGENT_VERSIONS') for q in conn.calls)
    assert not any(q.startswith('UPDATE AGENTS') for q in conn.calls)


def test_rerun_rejects_bucket_name_collision_with_different_owner_or_acl():
    conn = ExistingDatabase(conflicting_bucket=True)
    with pytest.raises(ValueError, match='bucket.*conflict|conflict.*bucket'):
        seed(conn, load_fixture_data(ROOT))
