"""Real relational query tests via SQLite; production uses psycopg/Postgres.

The adapter changes only parameter markers, not SQL or query results.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import jsonschema
import pytest
import yaml

UTC = timezone.utc
START = datetime(2026, 10, 1, tzinfo=UTC)
END = START + timedelta(days=2)


def analytics():
    assert importlib.util.find_spec('runtime.db.analytics'), 'Analytics implementation missing'
    return importlib.import_module('runtime.db.analytics')


class Connection:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.queries = []

    def execute(self, sql, parameters=()):
        self.queries.append((sql, parameters))
        return self.db.execute(sql.replace('%s', '?'), tuple(
            x.isoformat() if isinstance(x, datetime) else x for x in parameters))


@pytest.fixture
def conn():
    c = Connection()
    c.db.executescript('''
        CREATE TABLE users(id INTEGER, is_enterprise_admin BOOLEAN);
        CREATE TABLE enterprise(spec TEXT);
        CREATE TABLE teams(id INTEGER,name TEXT,spec TEXT);
        CREATE TABLE team_members(team_id INTEGER,user_id INTEGER);
        CREATE TABLE agents(id INTEGER,name TEXT,level TEXT,team_id INTEGER,access TEXT,current_version_id INTEGER);
        CREATE TABLE agent_versions(id INTEGER,agent_id INTEGER,version TEXT,spec TEXT);
        CREATE TABLE runs(id INTEGER,agent_version_id INTEGER,team_id INTEGER,status TEXT,cost_usd NUMERIC,latency_ms INTEGER,started_at TEXT);
        CREATE TABLE run_events(run_id INTEGER,type TEXT,action TEXT,allowed BOOLEAN);
        CREATE TABLE approvals(run_id INTEGER,decision TEXT);
        CREATE TABLE eval_runs(agent_version_id INTEGER,kind TEXT,pass_rate NUMERIC,created_at TEXT,id INTEGER);
        INSERT INTO users VALUES (1,0),(2,0),(3,1),(4,0);
        INSERT INTO team_members VALUES (10,1),(20,2);
        INSERT INTO agents VALUES (100,'reviewer','team',10,NULL,1000),(200,'people-bot','team',20,NULL,2000),
                                  (300,'private-helper','enterprise',NULL,'private',3000),
                                  (400,'evaluator','enterprise',NULL,'run_only',4000);
    ''')
    c.db.execute('INSERT INTO enterprise VALUES (?)', (json.dumps({'spec': {'connectors': {
        'github': {'allow': ['*']}, 'memory': {'allow': ['*']}}}}),))
    spec = {'spec': {'limits': {'usd_per_month': 200}, 'connectors': {
        'github': {'allow': ['pulls.*']}, 'memory': {'allow': ['*']}}}}
    c.db.executemany('INSERT INTO teams VALUES (?,?,?)', [(10, 'platform-eng', json.dumps(spec)),
        (20, 'people-ops', json.dumps({'spec': {'limits': {'usd_per_month': 100}}}))])
    c.db.executemany('INSERT INTO agent_versions VALUES (?,?,?,?)', [
        (1000,100,'1.0.0',json.dumps({'spec': {'budget': {'usd_per_month': 50}}})),
        (2000,200,'1.0.0','{}'), (3000,300,'1.0.0','{}'), (4000,400,'1.0.0','{}')])
    c.db.executemany('INSERT INTO runs VALUES (?,?,?,?,?,?,?)', [
        (1,1000,10,'succeeded',0.1,100,START.isoformat()),
        (2,1000,10,'failed',0.2,200,(START+timedelta(hours=1)).isoformat()),
        (3,1000,10,'awaiting_approval',0.05,None,(START+timedelta(days=1)).isoformat()),
        (4,2000,20,'succeeded',9,9000,START.isoformat()),
        (5,3000,10,'succeeded',7,7000,START.isoformat()),
        (6,1000,10,'succeeded',100,99999,END.isoformat()),
        (7,4000,10,'succeeded',0.03,300,START.isoformat())])
    c.db.executemany('INSERT INTO run_events VALUES (?,?,?,?)', [
        (1,'tool_call','github:pulls.read',1), (1,'tool_call','github:pulls.comment',1),
        (2,'tool_call','github:pulls.read',0), (2,'policy_violation','github:pulls.merge',0),
        (4,'tool_call','slack:messages.read',1), (5,'tool_call','hidden:action',1),
        (7,'tool_call','platform:run_evals',1)])
    c.db.executemany('INSERT INTO approvals VALUES (?,?)', [(1,'approved'),(1,'approved'),(3,None),(4,'expired')])
    c.db.executemany('INSERT INTO eval_runs VALUES (?,?,?,?,?)', [
        (1000,'ci',0.5,START.isoformat(),1),
        (1000,'ci',0.9,(START+timedelta(hours=1)).isoformat(),2),
        (1000,'production',0.2,(START+timedelta(hours=2)).isoformat(),3),
        (2000,'ci',1,START.isoformat(),4),
        (3000,'ci',1,START.isoformat(),5)])
    yield c
    c.db.close()


def metrics(conn, **kwargs):
    return analytics().get_metrics(conn, user_id=kwargs.pop('user_id',1), since=START, until=END, **kwargs)


def test_member_metrics_exclude_other_teams_private_agents_and_end_boundary(conn):
    result = metrics(conn)
    assert result['cost_usd'] == pytest.approx(0.38)
    assert result['success_rate'] == pytest.approx(2/3)
    assert result['p50_latency_ms'] == 200
    assert result['p95_latency_ms'] == 300
    assert result['runs_over_time'] == [
        {'bucket': START.isoformat(), 'runs': 3, 'failures': 1, 'cost_usd': 0.33},
        {'bucket': (START+timedelta(days=1)).isoformat(), 'runs': 1, 'failures': 0, 'cost_usd': 0.05}]
    assert result['human_intervention_rate'] == 0.5  # unique runs, not approval rows
    assert result['denials'] == 1
    assert result['policy_violations'] == 1
    assert result['eval_pass_rate_by_version'] == [{'version': 'reviewer@1.0.0', 'pass_rate': 0.9}]
    assert result['top_tools'] == [
        {'action': 'github:pulls.read', 'calls': 2, 'denials': 1},
        {'action': 'github:pulls.comment', 'calls': 1, 'denials': 0}]
    assert result['budget_usd'] == 200


def test_admin_can_see_all_runs_but_explicit_team_filter_still_applies(conn):
    assert metrics(conn,user_id=3)['cost_usd'] == pytest.approx(16.38)
    assert metrics(conn,user_id=3,team='people-ops')['cost_usd'] == 9


def test_agent_filter_and_visible_budget(conn):
    result = metrics(conn,agent='reviewer')
    assert result['cost_usd'] == pytest.approx(0.35)
    assert result['budget_usd'] == 50


@pytest.mark.parametrize('filter', [{'team':'people-ops'}, {'agent':'people-bot'},
    {'agent':'private-helper'}, {'agent':"reviewer' OR 1=1 --"}])
def test_forbidden_or_missing_filters_never_return_other_tenant_metrics(conn, filter):
    a = analytics()
    with pytest.raises(a.MetricsAccessError):
        metrics(conn,**filter)
    # SQL identifiers are fixed; untrusted names remain bound parameters.
    assert all("reviewer' OR 1=1" not in sql for sql, params in conn.queries)


def test_multiple_team_memberships_do_not_duplicate_runs(conn):
    conn.db.execute('INSERT INTO team_members VALUES (20,1)')
    result = metrics(conn)
    assert result['cost_usd'] == pytest.approx(9.38)
    assert result['budget_usd'] is None  # no invented combined team budget


def test_agent_budget_uses_its_own_team_when_user_has_multiple_memberships(conn):
    conn.db.execute('INSERT INTO team_members VALUES (20,1)')
    conn.db.execute('UPDATE agent_versions SET spec=? WHERE id=1000',
        (json.dumps({'spec': {'budget': {'usd_per_month': 500}}}),))
    assert metrics(conn,agent='reviewer')['budget_usd'] == 200


def test_run_team_alone_cannot_expose_another_teams_agent(conn):
    conn.db.execute('UPDATE runs SET team_id=10 WHERE id=4')
    assert metrics(conn)['cost_usd'] == pytest.approx(0.38)


def test_unknown_user_is_denied_and_member_without_teams_gets_empty_report(conn):
    a = analytics()
    with pytest.raises(a.MetricsAccessError):
        metrics(conn,user_id=999)
    empty = metrics(conn,user_id=4)
    assert empty['runs_over_time'] == []
    assert empty['cost_usd'] == empty['success_rate'] == 0
    assert empty['p50_latency_ms'] == empty['p95_latency_ms'] == 0
    assert empty['human_intervention_rate'] is None


def test_report_matches_frozen_metrics_schema_and_json_types(conn):
    root = Path(__file__).resolve().parents[2]
    schema = yaml.safe_load((root/'contracts/openapi.yaml').read_text())['components']['schemas']['Metrics']
    result = metrics(conn)
    jsonschema.Draft202012Validator(schema,format_checker=jsonschema.FormatChecker()).validate(result)
    json.dumps(result,allow_nan=False)


def test_empty_window_and_timezone_validation(conn):
    a = analytics()
    with pytest.raises(ValueError):
        a.get_metrics(conn,user_id=1,since=END,until=START)
    with pytest.raises(ValueError):
        a.get_metrics(conn,user_id=1,since=START.replace(tzinfo=None),until=END)


def test_nonterminal_runs_do_not_inflate_success_denominator(conn):
    conn.db.execute("UPDATE runs SET status='running',latency_ms=NULL WHERE team_id=10 AND agent_version_id IN (1000,4000)")
    result = metrics(conn)
    assert result['success_rate'] == 0
    assert result['p95_latency_ms'] == 0


@pytest.mark.parametrize('layer', ['team', 'enterprise'])
def test_top_tools_apply_qualified_guardrail_denials(conn, layer):
    table = 'teams' if layer == 'team' else 'enterprise'
    where = ' WHERE id=10' if layer == 'team' else ''
    doc = json.loads(conn.db.execute('SELECT spec FROM ' + table + where).fetchone()[0])
    doc['spec']['guardrails'] = {'deny': ['github:pulls.comment']}
    conn.db.execute('UPDATE '+table+' SET spec=?'+where, (json.dumps(doc),))
    result = metrics(conn)
    assert all(t['action'] != 'github:pulls.comment' for t in result['top_tools'])


def test_top_tools_inherit_omitted_allowlist_and_intersect_parent(conn):
    doc = json.loads(conn.db.execute('SELECT spec FROM teams WHERE id=10').fetchone()[0])
    doc['spec']['connectors']['github'] = {}
    conn.db.execute('UPDATE teams SET spec=? WHERE id=10', (json.dumps(doc),))
    assert len(metrics(conn)['top_tools']) == 2
    conn.db.execute('UPDATE enterprise SET spec=?', (json.dumps({'spec': {'connectors': {
        'github': {'allow': ['pulls.read']}}}}),))
    assert metrics(conn)['top_tools'] == [{'action':'github:pulls.read','calls':2,'denials':1}]
