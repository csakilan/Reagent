"""The pipeline store on Postgres, against contracts/db/001_init.sql. Same methods as MemoryStore.

    REAGENT_PUBLISH_STORE=db   use this store (DATABASE_URL)

The database enforces what MemoryStore checks by hand: the unique partial index
`publish_runs_one_active` allows ONE running pipeline per agent (D17), and
`case_reviews` is UNIQUE (publish_run_id, case_id) with the server-side hash
(D15). Pipeline working state (cases, the draft, the report) rides along in
`stage_results._pipeline`; the API strips it before anything leaves the server.

Stage 7 marks the draft agent_versions row published, points
agents.current_version_id at it, and attaches the eval report as an eval_runs row.
"""
from __future__ import annotations

import copy
import os
from typing import Any

from contracts.errors import Code, ReAgentError, Rejection

DECISION = {"approved": "approved", "edited": "edited", "rejected": "rejected"}
NUMERIC = ("pass_rate", "pass_rate_ci_low", "pass_rate_ci_high", "pass_k", "recall",
           "false_alarm_rate", "safety_pass_rate", "mutation_score", "tool_call_accuracy",
           "human_intervention_rate")


class PgStore:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("DATABASE_URL") or ""

    def _conn(self):
        import psycopg
        from psycopg.types.json import Jsonb  # noqa: F401  (registers adapters)
        return psycopg.connect(self.url, connect_timeout=15, autocommit=False)

    @staticmethod
    def _json(v: Any):
        from psycopg.types.json import Jsonb
        return Jsonb(v)

    # -- lookups -------------------------------------------------------------
    def _user_id(self, c, name: str) -> int:
        row = c.execute("SELECT id FROM users WHERE name = %s", (name,)).fetchone()
        if row is None:
            raise ReAgentError(Rejection(Code.SCHEMA_INVALID, "user", "default", attempted=name,
                                         extra={"message": f"Unknown user {name}."}))
        return row[0]

    def _agent_id(self, c, draft: dict[str, Any]) -> int:
        md = draft["metadata"]
        row = c.execute("SELECT id FROM agents WHERE name = %s", (md["name"],)).fetchone()
        if row:
            return row[0]
        team = None
        if md.get("level", "team") == "team":
            t = c.execute("SELECT id FROM teams WHERE name = %s", (md.get("team"),)).fetchone()
            if t is None:
                raise ReAgentError(Rejection(Code.UNKNOWN_TEAM, "metadata.team", "default",
                                             attempted=md.get("team")))
            team = t[0]
        return c.execute(
            "INSERT INTO agents (name, level, team_id, access) VALUES (%s, %s, %s, %s) RETURNING id",
            (md["name"], md.get("level", "team"), team,
             md.get("access") if md.get("level") == "enterprise" else None)).fetchone()[0]

    def _draft_version_id(self, c, agent_id: int, draft: dict[str, Any]) -> int:
        version = str(draft["metadata"].get("version"))
        row = c.execute("SELECT id, status FROM agent_versions WHERE agent_id = %s AND version = %s",
                        (agent_id, version)).fetchone()
        if row and row[1] != "draft":
            raise ReAgentError(Rejection(Code.SCHEMA_INVALID, "metadata.version", "default",
                                         attempted=version, extra={"message":
                                         f"Version {version} is already {row[1]} and immutable. "
                                         "Bump metadata.version to publish a change."}))
        if row:
            c.execute("UPDATE agent_versions SET spec = %s WHERE id = %s", (self._json(draft), row[0]))
            return row[0]
        return c.execute("INSERT INTO agent_versions (agent_id, version, status, spec) "
                         "VALUES (%s, %s, 'draft', %s) RETURNING id",
                         (agent_id, version, self._json(draft))).fetchone()[0]

    # -- runs ----------------------------------------------------------------
    def create_run(self, agent: str, draft: dict[str, Any], started_by: str) -> dict[str, Any]:
        import psycopg
        with self._conn() as c:
            uid = self._user_id(c, started_by)
            aid = self._agent_id(c, draft)
            vid = self._draft_version_id(c, aid, draft)
            try:
                row = c.execute(
                    "INSERT INTO publish_runs (agent_id, draft_version_id, stage, status, started_by) "
                    "VALUES (%s, %s, 'validate', 'running', %s) RETURNING id, started_at",
                    (aid, vid, uid)).fetchone()
            except psycopg.errors.UniqueViolation:
                c.rollback()
                raise ReAgentError(Rejection(Code.PIPELINE_ALREADY_RUNNING, "agent", "default",
                                             attempted=agent))
        return {"id": row[0], "agent": agent, "draft_version": str(draft["metadata"].get("version")),
                "stage": "validate", "status": "running", "started_by": started_by,
                "started_at": row[1].isoformat(), "finished_at": None, "stage_results": {},
                "cases": [], "draft": copy.deepcopy(draft), "report": None}

    def get(self, run_id: int) -> dict[str, Any]:
        with self._conn() as c:
            row = c.execute(
                "SELECT p.id, a.name, p.stage, p.status, u.name, p.started_at, p.finished_at, "
                "p.stage_results, v.version FROM publish_runs p JOIN agents a ON a.id = p.agent_id "
                "JOIN users u ON u.id = p.started_by JOIN agent_versions v ON v.id = p.draft_version_id "
                "WHERE p.id = %s", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        results = dict(row[7] or {})
        state = results.pop("_pipeline", {})
        run = {"id": row[0], "agent": row[1], "stage": row[2], "status": row[3], "started_by": row[4],
               "started_at": row[5].isoformat(), "finished_at": row[6].isoformat() if row[6] else None,
               "stage_results": results, "draft_version": row[8], **state}
        run.setdefault("cases", [])
        run.setdefault("report", None)
        return run

    def save(self, run: dict[str, Any]) -> None:
        state = {k: run.get(k) for k in ("cases", "draft", "report", "layers_hash")}
        results = dict(run["stage_results"], _pipeline=state)
        with self._conn() as c:
            c.execute("UPDATE publish_runs SET stage = %s, status = %s, finished_at = %s, "
                      "stage_results = %s WHERE id = %s",
                      (run["stage"], run["status"], run.get("finished_at"), self._json(results), run["id"]))
            for case in run["cases"]:
                if case["origin"] == "inherited" or case.get("decision") not in DECISION:
                    continue
                c.execute(
                    "INSERT INTO case_reviews (publish_run_id, case_id, case_hash, origin, decision, "
                    "edited_case, reason, reviewed_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (publish_run_id, case_id) DO UPDATE SET case_hash = EXCLUDED.case_hash, "
                    "decision = EXCLUDED.decision, edited_case = EXCLUDED.edited_case, "
                    "reason = EXCLUDED.reason, reviewed_by = EXCLUDED.reviewed_by, reviewed_at = now()",
                    (run["id"], case["case_id"], case["case_hash"], case["origin"],
                     DECISION[case["decision"]],
                     self._json(case.get("case")) if case["decision"] == "edited" else None,
                     case.get("reason"), self._user_id(c, case.get("reviewed_by") or run["started_by"])))

    def finish(self, run: dict[str, Any], status: str) -> None:
        from .store import now
        run["status"] = status
        run["finished_at"] = now()

    # -- reviews ---------------------------------------------------------------
    def approved_hashes(self, agent: str) -> set[str]:
        with self._conn() as c:
            return {h for (h,) in c.execute(
                "SELECT r.case_hash FROM case_reviews r JOIN publish_runs p ON p.id = r.publish_run_id "
                "JOIN agents a ON a.id = p.agent_id WHERE a.name = %s AND p.status = 'succeeded' "
                "AND r.decision IN ('approved', 'edited')", (agent,))}

    @property
    def rejected(self) -> dict[str, dict[str, str]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT a.name, r.case_hash, coalesce(r.reason, '') FROM case_reviews r "
                "JOIN publish_runs p ON p.id = r.publish_run_id JOIN agents a ON a.id = p.agent_id "
                "WHERE r.decision = 'rejected'").fetchall()
        out: dict[str, dict[str, str]] = {}
        for agent, h, reason in rows:
            out.setdefault(agent, {})[h] = reason
        return out

    def remember_rejection(self, agent: str, case_hash: str, reason: str) -> None:
        pass                                    # the case_reviews row written by save() is the memory

    # -- versions ----------------------------------------------------------------
    def publish(self, agent: str, version: dict[str, Any]) -> None:
        rep = version["report"]
        with self._conn() as c:
            vid = c.execute("SELECT draft_version_id FROM publish_runs WHERE id = %s",
                            (version["publish_run_id"],)).fetchone()[0]
            uid = c.execute("SELECT started_by FROM publish_runs WHERE id = %s",
                            (version["publish_run_id"],)).fetchone()[0]
            c.execute("UPDATE agent_versions SET status = 'published', spec = %s, published_by = %s, "
                      "published_at = now() WHERE id = %s", (self._json(version["spec"]), uid, vid))
            c.execute("UPDATE agents SET current_version_id = %s WHERE name = %s", (vid, agent))
            c.execute(
                "INSERT INTO eval_runs (agent_version_id, kind, " + ", ".join(NUMERIC) +
                ", avg_cost, p95_latency, results) VALUES (%s, 'ci', " + ", ".join(["%s"] * len(NUMERIC)) +
                ", %s, %s, %s)",
                (vid, *[None if rep.get(k) is None else round(float(rep[k]), 4) for k in NUMERIC],
                 rep.get("avg_cost"), int(rep.get("p95_latency") or 0), self._json(rep)))

    def latest_version(self, agent: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT v.version, e.results FROM agents a JOIN agent_versions v ON v.id = a.current_version_id "
                "LEFT JOIN LATERAL (SELECT results FROM eval_runs WHERE agent_version_id = v.id "
                "AND kind = 'ci' ORDER BY created_at DESC LIMIT 1) e ON true WHERE a.name = %s",
                (agent,)).fetchone()
        if row is None or not row[1]:
            return None
        return {"agent": agent, "version": row[0], "report": row[1]}
