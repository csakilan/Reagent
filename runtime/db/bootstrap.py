"""Apply frozen schema, seed demo data, verify H4, and release the handoff.

Run: python runtime/db/bootstrap.py
This command makes no model calls and never prints connection credentials.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import psycopg
import pytest

from fixtures.seed.seed import load_fixture_data, migrate, seed


class ContractGate:
    """A successful pytest exit alone can mean every live check skipped."""

    def __init__(self) -> None:
        self.passed = 0
        self.skipped = 0

    def pytest_runtest_logreport(self, report) -> None:
        if report.when == 'call' and report.passed:
            self.passed += 1
        if report.skipped:
            self.skipped += 1

    def pytest_collectreport(self, report) -> None:
        if report.skipped:
            self.skipped += 1

    def ready(self, exit_code: int) -> bool:
        return exit_code == 0 and self.passed >= 7 and self.skipped == 0


def main() -> int:
    load_dotenv(ROOT / '.env')
    if not os.environ.get('DATABASE_URL'):
        print('DATABASE_URL is missing from the environment and project .env.')
        return 1
    data = load_fixture_data()
    try:
        with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=10) as conn:
            # One transaction: an SQL error rolls back, and 001 is never patched.
            migrate(conn)
            counts = seed(conn, data)
        print('Committed seed data:', counts)
    except (psycopg.Error, ValueError) as exc:
        detail = exc.diag.message_primary if isinstance(exc, psycopg.Error) else str(exc)
        print('Bootstrap stopped; H4 not released:', detail or type(exc).__name__)
        return 1

    gate = ContractGate()
    result = pytest.main([str(ROOT / 'tests' / 'contract' / 'test_db_schema.py'), '-q'], plugins=[gate])
    if not gate.ready(int(result)):
        print(f'H4 not released: {gate.passed} passed, {gate.skipped} skipped, pytest exit {int(result)}.')
        return 1
    print('All live H4 checks passed. Releasing the handoff.')
    return subprocess.run([sys.executable, str(ROOT / 'scripts' / 'handoff.py'), 'ready', 'H4', '--yes'],
                          cwd=ROOT, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
