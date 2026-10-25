"""The release gate must never confuse skipped checks with a working database."""
from types import SimpleNamespace

import pytest

from fixtures.seed.seed import ROOT, TABLES, migrate
from runtime.db.bootstrap import ContractGate


def test_h4_requires_all_seven_live_checks_without_skips():
    gate = ContractGate()
    for _ in range(7):
        gate.pytest_runtest_logreport(SimpleNamespace(when='call', passed=True, skipped=False))
    assert gate.ready(0)
    assert not gate.ready(1)
    gate.pytest_collectreport(SimpleNamespace(skipped=True))
    assert not gate.ready(0)


def test_h4_rejects_empty_or_skipped_test_runs():
    gate = ContractGate()
    assert not gate.ready(0)
    gate.pytest_runtest_logreport(SimpleNamespace(when='setup', passed=False, skipped=True))
    assert not gate.ready(0)


class ExistingTables:
    def __init__(self, names):
        self.names = names
        self.calls = []

    def execute(self, statement, **kwargs):
        self.calls.append((statement, kwargs))
        return [(name,) for name in self.names]


def test_migration_executes_the_frozen_sql_verbatim():
    conn = ExistingTables([])
    migrate(conn)
    assert conn.calls[-1] == ((ROOT / 'contracts' / 'db' / '001_init.sql').read_text(encoding='utf-8'), {'prepare': False})


def test_migration_refuses_a_partial_existing_schema():
    conn = ExistingTables(['enterprise'])
    with pytest.raises(ValueError, match='Partial'):
        migrate(conn)
    assert len(conn.calls) == 1


def test_migration_does_not_reapply_to_an_existing_schema():
    conn = ExistingTables(TABLES)
    migrate(conn)
    assert len(conn.calls) == 1
