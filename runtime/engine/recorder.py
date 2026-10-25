"""Run recording: one `runs` row and ordered `run_events` rows per run. WF1.

Every payload passes through the Redactor BEFORE the write. Nothing raw reaches
the database: `payload_redacted` is named that way on purpose, and the trace
viewer renders what is stored.

`runs` is a hypertable keyed (id, started_at), so nothing foreign-keys to it.
Span ids live inside payload_redacted because the table has no span columns.

With no DATABASE_URL the recorder keeps everything in memory, which is enough
for unit tests and for developing against fakes before H4 lands.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from runtime.guardrails.redaction import Redactor

log = logging.getLogger("runtime.recorder")

_mem_ids = itertools.count(1)
_mem_approval_ids = itertools.count(1)
#: In-memory store, used only without a database. run_id -> dict
MEMORY: dict[int, dict[str, Any]] = {}
MEMORY_APPROVALS: dict[int, dict[str, Any]] = {}


def database_url() -> str | None:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url or "HOST." in url:  # the .env.example placeholder is not a database
        return None
    return url


def _connect(url: str):
    import psycopg
    return psycopg.connect(url, autocommit=True, connect_timeout=10)


#: One connection per URL per process, shared by every Recorder in it. Tiger
#: is ~100ms away and a connect costs ~0.6s, so a fresh one per run (and per
#: child run) was most of a run's wall time. Pools stay small (runtime/AGENTS.md).
_SHARED: dict[str, Any] = {}
_SHARED_LOCK = threading.Lock()


def _shared_conn(url: str):
    with _SHARED_LOCK:
        c = _SHARED.get(url)
        if c is None or c.closed or c.broken:
            c = _SHARED[url] = _connect(url)
        return c


class _EventWriter:
    """Background writer for run_events. A trace event never needs to block
    the run, and one INSERT round trip per event made a Tiger run ~13s.
    `seq` is assigned before enqueueing, so order is exact; flush() waits
    until everything queued is committed, and every run flushes before it
    returns, so a caller reading the trace after run_agent() sees all of it.
    Checkpoints are NOT written here: they stay synchronous (durability="sync")."""

    def __init__(self, url: str) -> None:
        import queue
        self.url = url
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.error: Exception | None = None
        self.t = threading.Thread(target=self._loop, name="run-events-writer", daemon=True)
        self.t.start()

    def _loop(self) -> None:
        import queue
        conn = None
        while True:
            batch = [self.q.get()]
            while len(batch) < 200:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            try:
                if conn is None or conn.closed or conn.broken:
                    conn = _connect(self.url)
                with conn.cursor() as cur:
                    cur.executemany(
                        "insert into run_events (run_id, seq, type, action, allowed, payload_redacted)"
                        " values (%s,%s,%s,%s,%s,%s)", batch)
            except Exception as e:  # noqa: BLE001 — surfaced on flush, never swallowed
                self.error = e
                log.error("run_events write failed: %s", type(e).__name__)
            finally:
                for _ in batch:
                    self.q.task_done()

    def put(self, row: tuple) -> None:
        self.q.put(row)

    def flush(self) -> None:
        self.q.join()
        if self.error is not None:
            err, self.error = self.error, None
            raise RuntimeError(f"run_events write failed: {err}") from err


_WRITERS: dict[str, _EventWriter] = {}
_AV_CACHE: dict[tuple, tuple[int, int | None]] = {}


def _writer(url: str) -> _EventWriter:
    with _SHARED_LOCK:
        w = _WRITERS.get(url)
        if w is None or not w.t.is_alive():
            w = _WRITERS[url] = _EventWriter(url)
        return w


@dataclass
class Recorder:
    redactor: Redactor
    url: str | None = field(default_factory=database_url)
    run_id: int | None = None
    team_id: int | None = None       # numeric ids, for the connector headers
    user_id: int | None = None
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- connection ---------------------------------------------------------
    @property
    def db(self):
        if not self.url:
            return None
        return _shared_conn(self.url)

    def flush(self) -> None:
        """Block until every queued run_events row is committed."""
        if self.url:
            _writer(self.url).flush()

    def close(self) -> None:
        # The connection is shared for the process; only drain our events.
        self.flush()

    # -- runs ---------------------------------------------------------------
    def start_run(self, *, agent: str, version: str, level: str, team: str | None,
                  access: str | None, trigger: str, model: str | None,
                  parent_run_id: int | None, user: str | None,
                  spec_doc: dict[str, Any] | None = None) -> int:
        if not self.db:
            self.run_id = next(_mem_ids)
            MEMORY[self.run_id] = {"id": self.run_id, "agent": agent, "version": version,
                                   "team": team, "parent_run_id": parent_run_id,
                                   "status": "running", "events": [], "cost_usd": 0.0,
                                   "steps": 0, "trigger": trigger, "user": user}
            return self.run_id
        av_id, team_id = self._agent_version(agent, version, level, team, access, spec_doc)
        user_id = self._user_id(user)
        self.team_id, self.user_id = team_id, user_id
        row = self.db.execute(
            "insert into runs (parent_run_id, agent_version_id, team_id, trigger, triggered_by, model)"
            " values (%s,%s,%s,%s,%s,%s) returning id",
            (parent_run_id, av_id, team_id, trigger, user_id, model)).fetchone()
        self.run_id = int(row[0])
        return self.run_id

    def attach(self, run_id: int) -> None:
        """Re-attach to an existing run on resume; continue its seq."""
        self.run_id = run_id
        if self.db:
            self.flush()
            r = self.db.execute("select coalesce(max(seq), -1) from run_events where run_id=%s",
                                (run_id,)).fetchone()
            self._seq = int(r[0]) + 1
        else:
            self._seq = len(MEMORY[run_id]["events"])

    def finish_run(self, *, status: str, cost_usd: float, input_tokens: int,
                   output_tokens: int, steps: int, error: str | None,
                   output: Any = None) -> None:
        err = self.redactor.redact_text(error)[0] if error else None
        if not self.db:
            MEMORY[self.run_id].update(status=status, cost_usd=cost_usd, steps=steps, error=err,
                                       input_tokens=input_tokens, output_tokens=output_tokens,
                                       output=output)
            return
        ended = None if status in ("running", "awaiting_approval") else dt.datetime.now(dt.timezone.utc)
        self.db.execute(
            "update runs set status=%s, cost_usd=%s, input_tokens=%s, output_tokens=%s, steps=%s,"
            " error=%s, ended_at=%s,"
            " latency_ms = case when %s::timestamptz is null then latency_ms"
            "   else floor(extract(epoch from (%s::timestamptz - started_at))*1000)::int end"
            " where id=%s",
            (status, round(cost_usd, 6), input_tokens, output_tokens, steps, err, ended,
             ended, ended, self.run_id))

    def run_status(self, run_id: int) -> dict[str, Any]:
        if not self.db:
            r = MEMORY[run_id]
            return {k: r.get(k) for k in ("status", "cost_usd", "steps", "error")}
        row = self.db.execute("select status, cost_usd, steps, error from runs where id=%s",
                              (run_id,)).fetchone()
        return {"status": row[0], "cost_usd": float(row[1]), "steps": row[2], "error": row[3]}

    def events(self, run_id: int) -> list[dict[str, Any]]:
        """All run_events rows for a run, in seq order (already redacted)."""
        if not self.db:
            return [dict(e) for e in MEMORY.get(run_id, {}).get("events", [])]
        self.flush()
        rows = self.db.execute(
            "select seq, type, action, allowed, payload_redacted from run_events"
            " where run_id=%s order by seq", (run_id,)).fetchall()
        return [{"seq": s, "type": t, "action": a, "allowed": al, "payload_redacted": p}
                for s, t, a, al, p in rows]

    def run_row(self, run_id: int) -> dict[str, Any]:
        """The run's own row with its agent, team and user names."""
        if not self.db:
            r = MEMORY[run_id]
            return {k: r.get(k) for k in ("agent", "version", "team", "user", "trigger", "status",
                                          "cost_usd", "steps", "error", "parent_run_id")} | {
                "id": run_id, "latency_ms": None}
        row = self.db.execute(
            "select r.id, a.name, av.version, t.name, u.name, r.trigger, r.status, r.cost_usd,"
            " r.steps, r.error, r.parent_run_id, r.latency_ms"
            " from runs r join agent_versions av on av.id=r.agent_version_id"
            " join agents a on a.id=av.agent_id left join teams t on t.id=r.team_id"
            " left join users u on u.id=r.triggered_by where r.id=%s", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"run {run_id} not found")
        keys = ["id", "agent", "version", "team", "user", "trigger", "status", "cost_usd",
                "steps", "error", "parent_run_id", "latency_ms"]
        d = dict(zip(keys, row))
        d["cost_usd"] = float(d["cost_usd"] or 0)
        return d

    def team_spend_today(self, team: str, agent: str) -> float:
        """What `agent` has cost `team` since midnight UTC (curation's daily cap)."""
        if not self.db:
            return sum(float(v["cost_usd"]) for v in MEMORY.values()
                       if v.get("team") == team and v.get("agent") == agent)
        row = self.db.execute(
            "select coalesce(sum(r.cost_usd),0) from runs r join agent_versions av"
            " on av.id=r.agent_version_id join agents a on a.id=av.agent_id"
            " join teams t on t.id=r.team_id where t.name=%s and a.name=%s"
            " and r.started_at >= date_trunc('day', now())", (team, agent)).fetchone()
        return float(row[0])

    def children_cost(self, run_id: int) -> float:
        """Total cost of every descendant run (runs.cost_usd is own-cost only)."""
        if not self.db:
            kids = [k for k, v in MEMORY.items() if v.get("parent_run_id") == run_id]
            return sum(float(MEMORY[k]["cost_usd"]) + self.children_cost(k) for k in kids)
        row = self.db.execute(
            "with recursive d(id) as (select id from runs where parent_run_id=%s"
            " union all select r.id from runs r join d on r.parent_run_id=d.id)"
            " select coalesce(sum(r.cost_usd),0) from runs r where r.id in (select id from d)",
            (run_id,)).fetchone()
        return float(row[0])

    def agent_for_run(self, run_id: int) -> tuple[str, str]:
        if not self.db:
            r = MEMORY[run_id]
            return r["agent"], r["version"]
        row = self.db.execute(
            "select a.name, av.version from runs r join agent_versions av on av.id=r.agent_version_id"
            " join agents a on a.id=av.agent_id where r.id=%s", (run_id,)).fetchone()
        return row[0], row[1]

    # -- events -------------------------------------------------------------
    def event(self, type_: str, *, action: str | None = None, allowed: bool | None = None,
              payload: dict[str, Any] | None = None, location: str = "trace") -> None:
        """Write one run_events row. Redacts the payload first and emits a
        redaction_hit (rule names and counts only) if anything was caught."""
        hits: dict[str, int] = {}
        if payload is not None:
            payload, hits = self.redactor.redact(payload)
        if action:
            action = self.redactor.redact_text(action)[0]
        self._write(type_, action, allowed, payload)
        for rule, n in hits.items():
            self._write("redaction_hit", None, None,
                        {"pattern": rule, "location": location, "count": n})

    def redaction_hits(self, hits: dict[str, int], location: str) -> None:
        for rule, n in hits.items():
            self._write("redaction_hit", None, None,
                        {"pattern": rule, "location": location, "count": n})

    def _write(self, type_: str, action: str | None, allowed: bool | None,
               payload: dict[str, Any] | None) -> None:
        with self._lock:
            seq = self._seq
            self._seq += 1
        if not self.db:
            MEMORY[self.run_id]["events"].append(
                {"seq": seq, "type": type_, "action": action, "allowed": allowed,
                 "payload_redacted": payload})
            return
        import psycopg.types.json as pj
        _writer(self.url).put((self.run_id, seq, type_, action, allowed,
                               pj.Jsonb(payload) if payload is not None else None))

    def span_open(self, name: str, parent: str | None = None, **extra: Any) -> str:
        sid = uuid.uuid4().hex[:16]
        self.event("span_open", action=extra.pop("action", None),
                   payload={"span_id": sid, "parent_span_id": parent, "name": name, **extra})
        return sid

    def span_close(self, sid: str, name: str, parent: str | None = None, **extra: Any) -> None:
        self.event("span_close", action=extra.pop("action", None),
                   payload={"span_id": sid, "parent_span_id": parent, "name": name, **extra})

    # -- approvals ----------------------------------------------------------
    def create_approval(self, action: str, args: dict[str, Any],
                        expires_at: dt.datetime | None) -> int:
        args_red, hits = self.redactor.redact(args)
        self.redaction_hits(hits, "trace")
        if not self.db:
            aid = next(_mem_approval_ids)
            MEMORY_APPROVALS[aid] = {"id": aid, "run_id": self.run_id, "action": action,
                                     "args_redacted": args_red, "expires_at": expires_at,
                                     "decision": None, "decided_at": None}
            return aid
        import psycopg.types.json as pj
        row = self.db.execute(
            "insert into approvals (run_id, action, args_redacted, expires_at)"
            " values (%s,%s,%s,%s) returning id",
            (self.run_id, action, pj.Jsonb(args_red), expires_at)).fetchone()
        return int(row[0])

    def pending_approval(self, run_id: int, action: str) -> int | None:
        """An undecided approval for this run+action, so a resume that re-raises
        the same interrupt does not queue a duplicate row."""
        if not self.db:
            for a in MEMORY_APPROVALS.values():
                if a["run_id"] == run_id and a["action"] == action and a["decision"] is None:
                    return a["id"]
            return None
        row = self.db.execute(
            "select id from approvals where run_id=%s and action=%s and decision is null"
            " order by id desc limit 1", (run_id, action)).fetchone()
        return int(row[0]) if row else None

    def get_approval(self, approval_id: int) -> dict[str, Any]:
        if not self.db:
            return dict(MEMORY_APPROVALS[approval_id])
        row = self.db.execute(
            "select id, run_id, action, args_redacted, expires_at, decision from approvals where id=%s",
            (approval_id,)).fetchone()
        if not row:
            raise KeyError(f"approval {approval_id} not found")
        return {"id": row[0], "run_id": row[1], "action": row[2], "args_redacted": row[3],
                "expires_at": row[4], "decision": row[5]}

    def decide(self, approval_id: int, decision: str, approver: str | None) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        if not self.db:
            MEMORY_APPROVALS[approval_id].update(decision=decision, decided_at=now,
                                                 approver=approver)
            return
        self.db.execute(
            "update approvals set decision=%s, decided_at=%s, approver_id=%s"
            " where id=%s and decision is null",
            (decision, now, self._user_id(approver), approval_id))

    # -- aggregate limits (FROZEN D7) --------------------------------------
    def aggregate_usage(self, agent: str, team: str | None) -> dict[str, dict[str, float]]:
        """Month-to-date spend and live run count per layer, summed from runs."""
        if not self.db:
            def agg(pred):
                rs = [r for r in MEMORY.values() if pred(r)]
                return {"usd_month": sum(float(r["cost_usd"]) for r in rs),
                        # concurrency counts root runs only: a child shares its root's slot
                        "running": sum(r["status"] == "running" and not r.get("parent_run_id")
                                       for r in rs)}
            return {"enterprise": agg(lambda r: True),
                    "team": agg(lambda r: team is not None and r["team"] == team),
                    "agent": agg(lambda r: r["agent"] == agent)}
        # One round trip for all three layers.
        row = self.db.execute(
            "select coalesce(sum(r.cost_usd),0),"
            " count(*) filter (where r.status='running' and r.parent_run_id is null),"
            " coalesce(sum(r.cost_usd) filter (where t.name=%s),0),"
            " count(*) filter (where r.status='running' and r.parent_run_id is null and t.name=%s),"
            " coalesce(sum(r.cost_usd) filter (where a.name=%s),0),"
            " count(*) filter (where r.status='running' and r.parent_run_id is null and a.name=%s)"
            " from runs r join agent_versions av on av.id=r.agent_version_id"
            " join agents a on a.id=av.agent_id left join teams t on t.id=r.team_id"
            # Every row, children included: each stores its own cost once.
            " where r.started_at >= date_trunc('month', now())",
            (team, team, agent, agent)).fetchone()
        return {"enterprise": {"usd_month": float(row[0]), "running": int(row[1])},
                "team": {"usd_month": float(row[2]), "running": int(row[3])},
                "agent": {"usd_month": float(row[4]), "running": int(row[5])}}

    # -- lookups / get-or-create -------------------------------------------
    def _user_id(self, name: str | None) -> int | None:
        if not name or not self.db:
            return None
        row = self.db.execute("select id from users where name=%s", (name,)).fetchone()
        return int(row[0]) if row else None

    def _agent_version(self, agent: str, version: str, level: str, team: str | None,
                       access: str | None, spec_doc: dict[str, Any] | None) -> tuple[int, int | None]:
        """Find the agent_versions row. If the seed has not created it, create a
        minimal one rather than fail the run: a missing seed row must not be
        what kills the demo. Logged loudly. Cached per process: ids are stable."""
        key = (self.url, agent, version, team)
        if key in _AV_CACHE:
            return _AV_CACHE[key]
        _AV_CACHE[key] = found = self._agent_version_uncached(agent, version, level, team,
                                                              access, spec_doc)
        return found

    def _agent_version_uncached(self, agent: str, version: str, level: str, team: str | None,
                                access: str | None,
                                spec_doc: dict[str, Any] | None) -> tuple[int, int | None]:
        team_id = None
        if team:
            r = self.db.execute("select id from teams where name=%s", (team,)).fetchone()
            if r is None:
                log.warning("team %s not seeded; creating a placeholder row", team)
                r = self.db.execute("insert into teams (name, spec) values (%s, '{}'::jsonb)"
                                    " on conflict (name) do update set name=excluded.name returning id",
                                    (team,)).fetchone()
            team_id = int(r[0])
        r = self.db.execute(
            "select av.id from agent_versions av join agents a on a.id=av.agent_id"
            " where a.name=%s and av.version=%s", (agent, version)).fetchone()
        if r:
            return int(r[0]), team_id
        log.warning("agent %s@%s not seeded; creating a placeholder version row", agent, version)
        import psycopg.types.json as pj
        is_team = level == "team" and team_id is not None
        a = self.db.execute(
            "insert into agents (name, level, team_id, access) values (%s,%s,%s,%s)"
            " on conflict (name) do update set name=excluded.name returning id",
            (agent, "team" if is_team else "enterprise", team_id if is_team else None,
             None if is_team else (access or "service"))).fetchone()
        doc = spec_doc or {"apiVersion": "reagent/v0", "kind": "Agent",
                           "metadata": {"name": agent, "version": version}}
        v = self.db.execute(
            "insert into agent_versions (agent_id, version, status, spec) values (%s,%s,'published',%s)"
            " on conflict (agent_id, version) do update set version=excluded.version returning id",
            (a[0], version, pj.Jsonb(doc))).fetchone()
        return int(v[0]), team_id


def dumps(o: Any) -> str:
    return json.dumps(o, default=str, sort_keys=True)
