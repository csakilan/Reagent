"""Task 5: the CLI is a thin client. Exit non-zero on rejection; print messages verbatim."""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("typer")
from typer.testing import CliRunner  # noqa: E402

from cli import reagent  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
PR = ROOT / "spec/examples/agents/platform-eng/pr-reviewer.agent"
runner = CliRunner()


@pytest.fixture(autouse=True)
def in_process_api(monkeypatch):
    """Point the CLI's HTTP client at the real app, in-process."""
    from fastapi.testclient import TestClient
    from runtime.api.agents import create_app
    app = TestClient(create_app())

    def client(user):
        app.headers.update({"X-ReAgent-User": user or "alice"})
        return app
    monkeypatch.setattr(reagent, "_client", client)


def test_validate_ok():
    r = runner.invoke(reagent.app, ["validate", str(PR)])
    assert r.exit_code == 0 and "OK" in r.output


def test_validate_prints_the_contract_message_verbatim_and_exits_1(tmp_path):
    bad = tmp_path / "pr.agent"
    bad.write_text(PR.read_text(encoding="utf-8").replace("timeout_seconds: 240", "timeout_seconds: 1200"),
                   encoding="utf-8")
    r = runner.invoke(reagent.app, ["validate", str(bad)])
    assert r.exit_code == 1
    assert ("spec.budget.timeout_seconds is 1200, above the limit of 900 set by enterprise. "
            "A lower layer may set a smaller value, never a larger one.") in r.output


def test_missing_file_is_a_usage_error():
    assert runner.invoke(reagent.app, ["validate", "nope.agent"]).exit_code == 2


def test_publish_prints_the_review_link():
    pytest.importorskip("langchain")
    from runtime.publish import STORE
    STORE.runs.clear()
    r = runner.invoke(reagent.app, ["publish", "pr-reviewer"])
    assert r.exit_code == 0 and "/publish?run=" in r.output
    again = runner.invoke(reagent.app, ["publish", "pr-reviewer"])
    assert again.exit_code == 1 and "already running" in again.output
    STORE.runs.clear()
