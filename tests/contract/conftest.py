"""Shared helpers for the seam tests.

A seam test has three possible outcomes and they mean different things:
  PASSED  both sides exist and agree
  SKIPPED one side does not exist yet. Waiting, not broken.
  FAILED  both sides exist and DISAGREE. Someone must act.

Never turn a FAILED into a SKIPPED to make the board look better.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
FIXTURES = ROOT / "fixtures"


@pytest.fixture(scope="session")
def root() -> pathlib.Path:
    return ROOT


@pytest.fixture(scope="session")
def resolved_schema() -> dict:
    return json.loads((CONTRACTS / "resolved_spec.schema.json").read_text())


@pytest.fixture(scope="session")
def connector_actions() -> dict:
    return json.loads((CONTRACTS / "connector_actions.json").read_text())


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — H4 has not landed yet")
    return url


def require_module(name: str, handoff: str):
    """Skip with a useful message when the implementing side does not exist."""
    pytest.importorskip(
        name, reason=f"{name} not built yet — waiting on {handoff}")


def require_http(url: str, handoff: str):
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=3)
    except urllib.error.HTTPError:
        return  # responding, even if not 200
    except Exception:
        pytest.skip(f"{url} not responding — waiting on {handoff}")
