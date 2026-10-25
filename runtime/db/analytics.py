"""Read-only /v0/metrics repository, scoped by the authenticated session user.

Uses a caller-owned psycopg connection with its default tuple row factory.
No raw prompts, outputs, event payloads or credentials are selected. Connector
policy fields are read internally to filter action names and never returned.
PostgreSQL does the team/run access filtering before rows enter Python.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fnmatch import fnmatchcase
import json
import math
from typing import Any, Protocol

UTC = timezone.utc
TERMINAL = {'succeeded', 'failed', 'denied', 'cancelled'}


class Cursor(Protocol):
    def fetchone(self) -> tuple | None: ...
    def fetchall(self) -> list[tuple]: ...


class Connection(Protocol):
    def execute(self, query: str, params: tuple = ()) -> Cursor: ...


class MetricsAccessError(ValueError):
    """API should map this to its generic not-found/forbidden response."""


def document(value: Any) -> dict:
    return value if isinstance(value, dict) else json.loads(value)


def timestamp(value: datetime | str) -> datetime:
    value = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Metrics timestamps must include a timezone')
    return value.astimezone(UTC)


def monthly_cap(doc: dict, section: str) -> Decimal | None:
    value = doc.get('spec', {}).get(section, {}).get('usd_per_month')
    return Decimal(str(value)) if value is not None else None


def percentile(values: list[int], fraction: float) -> int:
    """Nearest-rank percentiles, preserving the contract's integer latency."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def action_visible(action: str, team_doc: dict, parent_connectors: dict,
                   parent_denials: list[str]) -> bool:
    connector, separator, bare = action.partition(':')
    if not separator:
        return False
    grants = team_doc.get('spec', {}).get('connectors', {})
    if connector not in grants or connector not in parent_connectors:
        return False
    grant, parent = grants[connector], parent_connectors[connector]
    parent_allow = parent.get('allow', [])
    child_allow = grant.get('allow', parent_allow)
    denials = [*parent_denials, *team_doc.get('spec', {}).get('guardrails', {}).get('deny', [])]
    denials.extend(f'{connector}:{p}' for source in (parent, grant) for p in source.get('deny', []))
    return (any(fnmatchcase(bare, p) for p in parent_allow)
            and any(fnmatchcase(bare, p) for p in child_allow)
            and not any(fnmatchcase(action, p) for p in denials))


def get_metrics(conn: Connection, *, user_id: int, team: str | None = None,
                agent: str | None = None, since: datetime | None = None,
                until: datetime | None = None) -> dict:
    """Return the frozen Metrics shape; names never substitute for identity.

    Window is [since, until), defaulting to the last 30 days in UTC. Cost counts
    every stored run row as required for aggregate accounting. Success and
    latency use terminal runs; pending/running rows still count as activity.
    ``budget_usd`` is a visible monthly cap, not a prorated window budget or an
    authorization decision. Multiple teams have no invented combined cap.
    """
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id < 1:
        raise MetricsAccessError('Metrics scope is not available')
    stop = timestamp(until or datetime.now(UTC))
    start = timestamp(since or (stop - timedelta(days=30)))
    if start >= stop:
        raise ValueError('Metrics start must precede end')
    identity = conn.execute('SELECT is_enterprise_admin FROM users WHERE id=%s', (user_id,)).fetchone()
    if not identity:
        raise MetricsAccessError('Metrics scope is not available')
    admin = bool(identity[0])
    parent_connectors, parent_denials = {}, []
    if not admin:
        policy = conn.execute("SELECT spec->'spec'->'connectors',spec->'spec'->'guardrails'->'deny' FROM enterprise").fetchone()
        if policy:
            parent_connectors = document(policy[0]) if policy[0] is not None else {}
            parent_denials = (json.loads(policy[1]) if isinstance(policy[1], str) else policy[1]) or []
    if admin:
        teams = conn.execute('SELECT id,name,spec FROM teams').fetchall()
    else:
        teams = conn.execute('SELECT t.id,t.name,t.spec FROM teams t WHERE EXISTS '
            '(SELECT 1 FROM team_members m WHERE m.team_id=t.id AND m.user_id=%s)', (user_id,)).fetchall()
    if team is not None:
        teams = [t for t in teams if t[1] == team]
        if not teams:
            raise MetricsAccessError('Metrics scope is not available')
    team_docs = {t[0]: document(t[2]) for t in teams}
    team_ids = tuple(team_docs)
    conditions, params = ['r.started_at >= %s', 'r.started_at < %s'], [start, stop]
    agent_conditions, agent_params = [], []
    if not admin or team is not None:
        if not team_ids:
            conditions.append('1=0')
            agent_conditions.append('1=0')
        else:
            placeholders = ','.join('%s' for _ in team_ids)
            conditions.append(f'r.team_id IN ({placeholders})')
            params.extend(team_ids)
            # Evaluation reports have no caller-team column, so only team-owned
            # agents have member-visible reports. Enterprise suites stay hidden.
            agent_conditions.append(f'a.team_id IN ({placeholders})')
            agent_params.extend(team_ids)
    if not admin:
        if team_ids:
            placeholders = ','.join('%s' for _ in team_ids)
            conditions.append(f"((a.level='team' AND a.team_id IN ({placeholders})) OR "
                              "(a.level='enterprise' AND a.access IN ('run_only','service')))")
            params.extend(team_ids)
        agent_conditions.append("a.level='team'")
    agent_doc = None
    agent_team_id = None
    if agent is not None:
        row = conn.execute('SELECT a.team_id,a.level,a.access,v.spec FROM agents a '
            'JOIN agent_versions v ON v.id=a.current_version_id WHERE a.name=%s', (agent,)).fetchone()
        if not row or (not admin and (row[1] == 'team' and row[0] not in team_ids or row[1] == 'enterprise' and row[2] == 'private')):
            raise MetricsAccessError('Metrics scope is not available')
        # A selected team also scopes agent metadata used for the budget.
        if team is not None and row[1] == 'team' and row[0] not in team_ids:
            raise MetricsAccessError('Metrics scope is not available')
        if row[1] == 'team' or admin:
            agent_doc = document(row[3])
        if row[1] == 'team':
            agent_team_id = row[0]
        conditions.append('a.name=%s')
        params.append(agent)
        agent_conditions.append('a.name=%s')
        agent_params.append(agent)
    base = ('FROM runs r JOIN agent_versions v ON v.id=r.agent_version_id '
            'JOIN agents a ON a.id=v.agent_id WHERE ' + ' AND '.join(conditions))
    run_params = tuple(params)
    runs = conn.execute('SELECT r.id,r.started_at,r.status,r.cost_usd,r.latency_ms ' + base, run_params).fetchall()
    events = conn.execute('SELECT e.type,e.action,e.allowed,r.team_id FROM run_events e '
        'JOIN runs r ON r.id=e.run_id JOIN agent_versions v ON v.id=r.agent_version_id '
        'JOIN agents a ON a.id=v.agent_id WHERE ' + ' AND '.join(conditions), run_params).fetchall()
    approvals = conn.execute('SELECT DISTINCT p.run_id FROM approvals p WHERE p.run_id IN '
        '(SELECT r.id ' + base + ')', run_params).fetchall()
    eval_conditions = ['e.kind=%s', 'e.created_at >= %s', 'e.created_at < %s', 'e.pass_rate IS NOT NULL'] + agent_conditions
    evals = conn.execute('SELECT a.name,v.version,e.pass_rate FROM eval_runs e '
        'JOIN agent_versions v ON v.id=e.agent_version_id JOIN agents a ON a.id=v.agent_id WHERE '
        + ' AND '.join(eval_conditions) + ' ORDER BY e.created_at DESC,e.id DESC',
        ('ci', start, stop, *agent_params)).fetchall()

    buckets: dict[str, dict] = {}
    cost = Decimal('0')
    completed, successes, latencies = 0, 0, []
    for run_id, started_at, status, amount, latency in runs:
        amount = Decimal(str(amount))
        cost += amount
        bucket = timestamp(started_at).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        entry = buckets.setdefault(bucket, {'bucket': bucket, 'runs': 0, 'failures': 0, 'cost_usd': Decimal('0')})
        entry['runs'] += 1
        entry['cost_usd'] += amount
        entry['failures'] += int(status in TERMINAL and status != 'succeeded')
        if status in TERMINAL:
            completed += 1
            successes += int(status == 'succeeded')
            if latency is not None:
                latencies.append(int(latency))
    tools: dict[str, dict] = {}
    denials = violations = 0
    for kind, action, allowed, team_id in events:
        violations += int(kind == 'policy_violation')
        if kind != 'tool_call':
            continue
        denied = allowed is not None and not bool(allowed)
        denials += int(denied)
        if not action or (not admin and not action_visible(action, team_docs.get(team_id, {}), parent_connectors, parent_denials)):
            continue
        entry = tools.setdefault(action, {'action': action, 'calls': 0, 'denials': 0})
        entry['calls'] += 1
        entry['denials'] += int(denied)
    versions: dict[str, dict] = {}
    for name, version, rate in evals:
        label = f'{name}@{version}'
        versions.setdefault(label, {'version': label, 'pass_rate': float(rate)})
    caps = []
    budget_team_doc = team_docs.get(agent_team_id) if agent_team_id is not None else None
    if budget_team_doc is None and len(team_docs) == 1 and (team is not None or not admin):
        budget_team_doc = next(iter(team_docs.values()))
    if budget_team_doc is not None:
        cap = monthly_cap(budget_team_doc, 'limits')
        if cap is not None:
            caps.append(cap)
    if agent_doc is not None:
        cap = monthly_cap(agent_doc, 'budget')
        if cap is not None:
            caps.append(cap)
    return {
        'runs_over_time': [{**buckets[k], 'cost_usd': float(buckets[k]['cost_usd'])} for k in sorted(buckets)],
        'success_rate': successes / completed if completed else 0.0,
        'p50_latency_ms': percentile(latencies, 0.5), 'p95_latency_ms': percentile(latencies, 0.95),
        'cost_usd': float(cost), 'budget_usd': float(min(caps)) if caps else None,
        'human_intervention_rate': len(approvals) / len(runs) if runs else None,
        'top_tools': sorted(tools.values(), key=lambda t: (-t['calls'], t['action']))[:20],
        'denials': denials, 'policy_violations': violations,
        'eval_pass_rate_by_version': sorted(versions.values(), key=lambda v: v['version']),
    }


def main() -> int:
    """Read-only live diagnostic; --user is a local operator choice, not HTTP auth."""
    import argparse
    import os
    from pathlib import Path
    import psycopg
    from dotenv import load_dotenv
    import jsonschema
    import yaml

    parser = argparse.ArgumentParser(description='Read and validate live /v0/metrics data; writes nothing')
    parser.add_argument('--user', required=True, help='Existing seed user, e.g. alice or frank')
    parser.add_argument('--team')
    parser.add_argument('--agent')
    parser.add_argument('--since', type=datetime.fromisoformat)
    parser.add_argument('--until', type=datetime.fromisoformat)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / '.env')
    if not os.environ.get('DATABASE_URL'):
        print('DATABASE_URL is not configured.')
        return 1
    try:
        with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=10) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            row = conn.execute('SELECT id FROM users WHERE name=%s', (args.user,)).fetchone()
            if not row:
                raise MetricsAccessError('Metrics scope is not available')
            result = get_metrics(conn, user_id=row[0], team=args.team, agent=args.agent,
                                 since=args.since, until=args.until)
            schema = yaml.safe_load((root / 'contracts' / 'openapi.yaml').read_text())['components']['schemas']['Metrics']
            jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(result)
        print(json.dumps(result, indent=2, allow_nan=False))
        print('Live analytics: read-only queries and Metrics schema validation passed.')
        return 0
    except (psycopg.Error, ValueError, jsonschema.ValidationError) as exc:
        # Driver exceptions can contain passwords/URLs. Print only their type.
        print('Live analytics check failed:', type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
