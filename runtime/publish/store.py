"""Pipeline persistence. FROZEN D17's invariants hold in every implementation:

  - status is running | failed | abandoned | succeeded; stage is the current or
    failing step only;
  - at most ONE running pipeline per agent (in Postgres: the unique partial
    index publish_runs_one_active);
  - approvals are recorded per case against its server-side hash (D15).

`MemoryStore` is the no-database store. It is the default until the database
handoff (H4) lands; a Postgres store against contracts/db/001_init.sql is a
drop-in replacement behind the same methods.
"""
from __future__ import annotations

import copy
import datetime as dt
import itertools
import threading
from typing import Any

from contracts.errors import Code, ReAgentError, Rejection

STAGES = ("validate", "security", "generate", "review", "evals", "gate", "publish")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.runs: dict[int, dict[str, Any]] = {}
        self.versions: dict[str, list[dict[str, Any]]] = {}     # agent -> published versions
        self.rejected: dict[str, dict[str, str]] = {}           # agent -> case_hash -> reason

    # -- runs ----------------------------------------------------------------
    def create_run(self, agent: str, draft: dict[str, Any], started_by: str) -> dict[str, Any]:
        with self._lock:
            if any(r["agent"] == agent and r["status"] == "running" for r in self.runs.values()):
                raise ReAgentError(Rejection(Code.PIPELINE_ALREADY_RUNNING, "agent", "default",
                                             attempted=agent))
            rid = next(self._ids)
            run = {"id": rid, "agent": agent,
                   "draft_version": str(draft["metadata"].get("version")),
                   "stage": "validate", "status": "running", "started_by": started_by,
                   "started_at": now(), "finished_at": None, "stage_results": {},
                   "cases": [], "draft": copy.deepcopy(draft), "report": None}
            self.runs[rid] = run
            return run

    def get(self, run_id: int) -> dict[str, Any]:
        if run_id not in self.runs:
            raise KeyError(run_id)
        return self.runs[run_id]

    def save(self, run: dict[str, Any]) -> None:
        """Runs are held by reference; nothing to write."""

    def finish(self, run: dict[str, Any], status: str) -> None:
        run["status"] = status
        run["finished_at"] = now()

    # -- reviews ---------------------------------------------------------------
    def approved_hashes(self, agent: str) -> set[str]:
        """Hashes approved in this agent's previous SUCCEEDED pipelines (carry-over)."""
        out: set[str] = set()
        for r in self.runs.values():
            if r["agent"] == agent and r["status"] == "succeeded":
                out |= {c["case_hash"] for c in r["cases"] if c["decision"] in ("approved", "edited")}
        return out

    def remember_rejection(self, agent: str, case_hash: str, reason: str) -> None:
        self.rejected.setdefault(agent, {})[case_hash] = reason

    # -- versions ----------------------------------------------------------------
    def publish(self, agent: str, version: dict[str, Any]) -> None:
        with self._lock:
            self.versions.setdefault(agent, []).append(version)

    def latest_version(self, agent: str) -> dict[str, Any] | None:
        vs = self.versions.get(agent) or []
        return vs[-1] if vs else None
