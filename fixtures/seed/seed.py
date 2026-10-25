"""Repeatable demo seed. No model calls; existing policies/versions stay intact.

Run from any directory: python fixtures/seed/seed.py [--check | --plan]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import yaml

ROOT = Path(__file__).resolve().parents[2]
SEED_MARKER = 'reagent-demo-history-v1'
TABLES = ('enterprise', 'teams', 'users', 'team_members', 'registry_items',
          'connector_requests', 'agents', 'agent_versions', 'runs', 'run_events',
          'approvals', 'eval_runs', 'judge_calibration', 'calibration_labels',
          'publish_runs', 'case_reviews', 'memory_buckets', 'memory_items')


def load_fixture_data(root: Path = ROOT) -> dict:
    """Read authoritative example documents without changing their contents."""
    examples = root / 'spec' / 'examples'

    def read(path: Path) -> dict:
        return yaml.safe_load(path.read_text(encoding='utf-8'))

    enterprise = read(examples / 'enterprise.yaml')
    teams = [read(p) for p in sorted((examples / 'teams').glob('*.yaml'))]
    teams.append(read(root / 'fixtures' / 'seed' / 'customer-support.team.yaml'))
    users = set(enterprise['spec'].get('admins', []))
    for team in teams:
        users.update(team['spec'].get('members', []))
    return {
        'enterprise': enterprise,
        'teams': teams,
        'users': sorted(users),
        'connectors': [read(p) for p in sorted((examples / 'registry' / 'connectors').glob('*.yaml'))],
        'models': [read(p) for p in sorted((examples / 'registry' / 'models').glob('*.yaml'))],
        'agents': [read(p) for p in sorted((examples / 'agents').rglob('*.agent'))],
    }


def version_state(doc: dict) -> tuple[str, str]:
    """FROZEN D8 bootstraps platform agents; team drafts still need review."""
    return ('published', 'platform') if doc['metadata']['level'] == 'enterprise' else ('draft', 'user')


def history_rows(now: datetime) -> list[dict]:
    """Synthetic, redacted activity over two weeks; never invokes a model."""
    rows = []
    for i in range(30):
        started = now - timedelta(days=14 - i // 3, minutes=20 + i * 7)
        latency = 1500 + (i % 9) * 650
        status = 'denied' if i % 13 == 0 else 'failed' if i % 11 == 0 else 'succeeded'
        rows.append({
            'team_name': ('platform-eng', 'people-ops', 'customer-support')[i % 3],
            'agent_name': ('secret-scanner', 'agent-builder', 'evaluator')[i % 3],
            'status': status,
            'started_at': started,
            'ended_at': started + timedelta(milliseconds=latency),
            'latency_ms': latency,
            'cost_usd': Decimal('0.0005') * (1 + i % 7),
            'input_tokens': 250 + i * 13,
            'output_tokens': 80 + i * 7,
            'steps': 1 + i % 4,
            'error': 'E_PERMISSION_DENIED' if status == 'denied' else 'E_MODEL_ERROR' if status == 'failed' else None,
            'payload_redacted': {'seed_fixture': SEED_MARKER, 'synthetic': True},
        })
    return rows


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    return {name: conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name))).fetchone()[0]
            for name in TABLES}


def migrate(conn: psycopg.Connection) -> None:
    """Apply frozen 001 verbatim, or refuse a partial pre-existing schema."""
    got = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    existing = got.intersection(TABLES)
    if existing == set(TABLES):
        print('Application schema already exists; 001 was not reapplied.')
        return
    if existing:
        raise ValueError('Partial application schema exists; refusing to reapply or patch frozen 001.')
    conn.execute((ROOT / 'contracts' / 'db' / '001_init.sql').read_text(encoding='utf-8'), prepare=False)
    print('Applied contracts/db/001_init.sql unchanged.')


def seed(conn: psycopg.Connection, data: dict) -> dict[str, int]:
    """Insert missing fixtures in one transaction, serialized across laptops."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (4262604,))
    admins = set(data['enterprise']['spec'].get('admins', []))
    user_ids = {}
    for name in data['users']:
        conn.execute('INSERT INTO users (name, email, is_enterprise_admin) VALUES (%s,%s,%s) ON CONFLICT (name) DO NOTHING',
                     (name, f'{name}@example.test', name in admins))
        user_ids[name] = conn.execute('SELECT id FROM users WHERE name=%s', (name,)).fetchone()[0]

    enterprise = data['enterprise']
    conn.execute('INSERT INTO enterprise (name, spec) VALUES (%s,%s) ON CONFLICT (name) DO NOTHING',
                 (enterprise['metadata']['name'], Jsonb(enterprise)))
    team_ids = {}
    for doc in data['teams']:
        name = doc['metadata']['name']
        created = conn.execute('INSERT INTO teams (name,spec) VALUES (%s,%s) ON CONFLICT (name) DO NOTHING RETURNING id', (name, Jsonb(doc))).fetchone()
        team_ids[name] = conn.execute('SELECT id FROM teams WHERE name=%s', (name,)).fetchone()[0]
        if created:
            for member in doc['spec'].get('members', []):
                conn.execute('INSERT INTO team_members (team_id,user_id,is_approver) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                             (team_ids[name], user_ids[member], member in doc['spec'].get('approvers', [])))

    admin_id = user_ids[sorted(admins)[0]]
    for kind, docs in (('connector', data['connectors']), ('model', data['models'])):
        for doc in docs:
            m = doc['metadata']
            conn.execute('INSERT INTO registry_items (kind,name,version,spec,created_by) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (kind,name,version) DO NOTHING',
                         (kind, m['name'], m['version'], Jsonb(doc), admin_id))

    version_ids, created_versions = {}, set()
    for doc in data['agents']:
        m = doc['metadata']
        name, level = m['name'], m['level']
        created_agent = conn.execute('INSERT INTO agents (name,level,team_id,access) VALUES (%s,%s,%s,%s) ON CONFLICT (name) DO NOTHING RETURNING id',
                     (name, level, team_ids.get(m.get('team')), m.get('access') if level == 'enterprise' else None)).fetchone()
        agent_id = conn.execute('SELECT id FROM agents WHERE name=%s', (name,)).fetchone()[0]
        status, origin = version_state(doc)
        created_version = conn.execute('INSERT INTO agent_versions (agent_id,version,status,origin,spec,published_by,published_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (agent_id,version) DO NOTHING RETURNING id',
                     (agent_id, m['version'], status, origin, Jsonb(doc), admin_id if status == 'published' else None,
                      datetime.now(timezone.utc) if status == 'published' else None)).fetchone()
        version_id = conn.execute('SELECT id FROM agent_versions WHERE agent_id=%s AND version=%s', (agent_id, m['version'])).fetchone()[0]
        version_ids[f"{name}@{m['version']}"] = version_id
        if created_version:
            created_versions.add(version_id)
        if created_agent:
            conn.execute('UPDATE agents SET current_version_id=%s WHERE id=%s AND current_version_id IS NULL', (version_id, agent_id))

    for doc in data['agents']:
        m = doc['metadata']
        if ref := m.get('forked_from'):
            child_id = version_ids[f"{m['name']}@{m['version']}"]
            if child_id not in created_versions:
                continue
            if ref not in version_ids:
                raise ValueError(f'Unresolved seed fork: {ref}')
            conn.execute('UPDATE agent_versions SET forked_from_version_id=%s WHERE id=%s AND forked_from_version_id IS NULL',
                         (version_ids[ref], child_id))

    for doc in [enterprise, *data['teams']]:
        for bucket in doc['spec'].get('memory', {}).get('buckets', []):
            owner_id = team_ids.get(doc['metadata']['name']) if doc['kind'] == 'Team' else None
            expected = (bucket['type'], owner_id, bucket.get('read', []), bucket.get('write', []))
            created_bucket = conn.execute('INSERT INTO memory_buckets (name,type,owner_team_id,read,write) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (name) DO NOTHING RETURNING id',
                         (bucket['name'], bucket['type'], owner_id,
                          Jsonb(bucket.get('read', [])), Jsonb(bucket.get('write', [])))).fetchone()
            if not created_bucket:
                existing = conn.execute('SELECT type,owner_team_id,read,write FROM memory_buckets WHERE name=%s', (bucket['name'],)).fetchone()
                if existing != expected:
                    raise ValueError(f"Seed bucket conflict: {bucket['name']} has a different existing declaration.")

    already_seeded = conn.execute("SELECT 1 FROM run_events WHERE payload_redacted->>'seed_fixture'=%s LIMIT 1", (SEED_MARKER,)).fetchone()
    if not already_seeded:
        by_name = {doc['metadata']['name']: version_ids[f"{doc['metadata']['name']}@{doc['metadata']['version']}"] for doc in data['agents']}
        models = {doc['metadata']['name']: doc['spec']['model']['id'] for doc in data['agents']}
        for row in history_rows(datetime.now(timezone.utc)):
            run_id = conn.execute(
                'INSERT INTO runs (agent_version_id,team_id,trigger,triggered_by,model,status,input_tokens,output_tokens,cost_usd,steps,latency_ms,error,started_at,ended_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                (by_name[row['agent_name']], team_ids[row['team_name']], 'system', None, models[row['agent_name']],
                 row['status'], row['input_tokens'], row['output_tokens'], row['cost_usd'], row['steps'], row['latency_ms'],
                 row['error'], row['started_at'], row['ended_at'])).fetchone()[0]
            conn.execute('INSERT INTO run_events (run_id,seq,type,payload_redacted,ts) VALUES (%s,1,%s,%s,%s)',
                         (run_id, 'metric', Jsonb(row['payload_redacted']), row['ended_at']))
    return check_seed(conn)


def check_seed(conn: psycopg.Connection) -> dict[str, int]:
    counts = table_counts(conn)
    for name, minimum in {'enterprise': 1, 'teams': 3, 'users': 7, 'registry_items': 8,
                          'agents': 6, 'agent_versions': 6, 'runs': 10}.items():
        if counts[name] < minimum:
            raise ValueError(f'Seed check failed: {name} needs at least {minimum} rows.')
    kinds = dict(conn.execute('SELECT kind,count(*) FROM registry_items GROUP BY kind').fetchall())
    if kinds.get('connector', 0) < 4 or kinds.get('model', 0) < 4:
        raise ValueError('Seed check failed: need four connectors and four models.')
    bootstrap = conn.execute("SELECT a.name,v.status,v.origin FROM agents a LEFT JOIN agent_versions v ON v.id=a.current_version_id WHERE a.level='enterprise'").fetchall()
    if not bootstrap or any(status != 'published' or origin != 'platform' for _, status, origin in bootstrap):
        raise ValueError('Seed check failed: enterprise agents must be published platform versions.')
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Read-only verification of live seed data')
    mode.add_argument('--plan', action='store_true', help='Print fixture counts without connecting')
    args = parser.parse_args()
    data = load_fixture_data()
    if args.plan:
        print({k: len(v) for k, v in data.items() if isinstance(v, list)})
        return 0
    load_dotenv(ROOT / '.env')
    if not os.environ.get('DATABASE_URL'):
        print('DATABASE_URL is missing from the environment and project .env.')
        return 1
    try:
        with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=10) as conn:
            if args.check:
                conn.execute('SET TRANSACTION READ ONLY')
            counts = check_seed(conn) if args.check else seed(conn, data)
        print('Seed verified:', counts)
        return 0
    except (psycopg.Error, ValueError) as exc:
        # Connection exceptions may contain credential-bearing DSNs: never print them.
        detail = exc.diag.message_primary if isinstance(exc, psycopg.Error) else str(exc)
        print('Seed failed:', detail or type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
