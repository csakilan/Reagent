"""Post-run memory curation by the enterprise meta-agent `memory-curator`. WF1.

After a root run finishes, the curator reads the run's (already redacted) trace
and PROPOSES memories. It has no tools: this module validates each proposal and
does every write itself, so a prompt-injected trace cannot make it write
anything, and "episodic is runtime-only" holds by construction.

  episodic    one deterministic record per run (what ran, what it did, how it
              ended), plus the curator's one-line summary. Written with
              X-Reagent-Invocation: system, the only way into an episodic bucket.
  semantic    atomic facts. Kept only when grounded in real trace events,
              confidence >= 0.8, free of directives and secrets, and not a
              duplicate. Written active.
  procedural  lessons. Always written pending; the eval gate (gate_procedure)
              promotes one only if every agent that reads the bucket does not
              regress on its evals with the lesson in its memory.

Which runs: root runs with a user-facing trigger (http, mcp, schedule, github,
slack) whose team owns buckets. Eval runs and meta-agents use trigger
"system", sub-agent calls "agent", so neither is ever curated.

Modes, REAGENT_MEMORY:  off (default) | sync | async
  sync   curate before run_agent returns (CLI, tests, rehearsal)
  async  background task; for the long-lived API process
REAGENT_MEMORY_GATE: auto (default) | off   run the procedural gate after curation.

Every outcome is a `metric` run_events row on the SOURCE run, so the trace
viewer and analytics show what was learned and what was dropped, and why.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from runtime.engine.recorder import Recorder, database_url
from runtime.guardrails.redaction import Redactor

log = logging.getLogger("runtime.memory")

CURATOR = "memory-curator"
CURATE_TRIGGERS = {"http", "mcp", "schedule", "github", "slack"}
TERMINAL = {"succeeded", "failed", "denied", "cancelled"}
SEMANTIC_MIN_CONFIDENCE = 0.8
PROCEDURAL_MIN_CONFIDENCE = 0.7
MAX_FACT_CHARS = 400
MAX_STEPS, MAX_STEP_CHARS = 8, 200
MAX_TRACE_CHARS = 24_000          # ~6k tokens
NEAR_DUPLICATE = 0.85             # token Jaccard

#: Directives aimed at agents. A memory is read back into other agents' context,
#: so an instruction smuggled into one is a prompt-injection path (SPEC §9).
INJECTION = re.compile(
    r"(?i)\b(ignore|disregard|forget)\b.{0,40}\b(instruction|prompt|rule|previous|above)"
    r"|\b(always|never)\s+(approve|merge|skip|bypass|disable|trust)"
    r"|\b(disable|turn off|bypass|skip)\b.{0,30}\b(approval|redaction|guardrail|review|check)"
    r"|\byou (must|should|are now)\b|\bsystem prompt\b|https?://|\bapprovals? (are|is) optional")

_TASKS: set[asyncio.Task] = set()


def mode() -> str:
    return os.environ.get("REAGENT_MEMORY", "off").strip().lower()


def gate_mode() -> str:
    return os.environ.get("REAGENT_MEMORY_GATE", "auto").strip().lower()


# --------------------------------------------------------------------------
# Entry point from runner._finalise
# --------------------------------------------------------------------------

async def schedule_curation(run_id: int, identity: dict[str, str]) -> None:
    """Called for every finished root run. Cheap when off; never raises."""
    m = mode()
    if m not in ("sync", "async") or run_id is None:
        return
    if m == "async":
        task = asyncio.create_task(_safe_curate(run_id, identity))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return
    await _safe_curate(run_id, identity)


async def _safe_curate(run_id: int, identity: dict[str, str]) -> None:
    try:
        await curate(run_id, identity)
    except Exception:  # noqa: BLE001 — curation must never break the run it follows
        log.exception("memory curation failed for run %s", run_id)


# --------------------------------------------------------------------------
# Trace
# --------------------------------------------------------------------------

def _clip(v: Any, n: int = 200) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "…"


def build_trace(run: dict[str, Any], events: list[dict[str, Any]]) -> str:
    """Compact, numbered, already-redacted trace. One line per meaningful event:
    `seq | type | action | allowed | summary`. The seq numbers are what the
    curator must cite as evidence."""
    lines: list[str] = []
    inp, out = None, None
    for e in events:
        p = e.get("payload_redacted") or {}
        t = e["type"]
        if t == "span_open" and p.get("parent_span_id") is None and "input" in p:
            inp = p.get("input")
            continue
        if t == "span_close" and p.get("parent_span_id") is None and p.get("output"):
            out = p.get("output")
        if t in ("model_call", "metric", "redaction_hit"):
            continue  # noise for extraction; redaction hits carry no content anyway
        if t == "tool_call":
            summary = p.get("denial_code") or p.get("error") or p.get("result_summary")
            summary = f"args={_clip(p.get('args'), 120)} -> {_clip(summary, 240)}"
        elif t == "policy_violation":
            summary = f"{p.get('code')}: {_clip(p.get('message'), 160)}"
        elif t in ("approval_requested", "approval_decided"):
            summary = _clip({k: p.get(k) for k in ("action", "decision", "approver") if k in p})
        elif t == "span_close":
            summary = _clip({"status": p.get("status"), "output": p.get("output")}, 400)
        else:
            summary = _clip({k: v for k, v in p.items() if k not in ("span_id", "parent_span_id")})
        allowed = "" if e.get("allowed") is None else ("allowed" if e["allowed"] else "DENIED")
        lines.append(f"{e['seq']} | {t} | {e.get('action') or ''} | {allowed} | {summary}")
    head = (f"agent: {run['agent']}@{run['version']}  team: {run['team']}  trigger: {run['trigger']}"
            f"  status: {run['status']}  error: {_clip(run.get('error') or '', 160)}\n"
            f"input: {_clip(inp, 600)}\nfinal output: {_clip(out, 1500)}\nevents:\n")
    body = "\n".join(lines)
    if len(head) + len(body) > MAX_TRACE_CHARS:
        body = body[: MAX_TRACE_CHARS - len(head)] + "\n… (trace truncated)"
    return "<<<TRACE\n" + head + body + "\nTRACE>>>"


def episodic_record(run: dict[str, Any], events: list[dict[str, Any]], summary: str | None) -> str:
    """Deterministic: built from the recorded run, never from model claims. Only
    the trailing one-line summary comes from the curator, and it is filtered."""
    called: dict[str, int] = {}
    denied: dict[str, int] = {}
    approvals = []
    for e in events:
        if e["type"] == "tool_call" and e.get("action"):
            bucket = called if e.get("allowed") else denied
            bucket[e["action"]] = bucket.get(e["action"], 0) + 1
        if e["type"] == "approval_decided":
            p = e.get("payload_redacted") or {}
            approvals.append(f"{e.get('action')} {p.get('decision')}")
    fmt = lambda d: ", ".join(f"{k} x{v}" for k, v in sorted(d.items())) or "none"  # noqa: E731
    lat = f" in {run['latency_ms'] / 1000:.1f}s" if run.get("latency_ms") else ""
    text = (f"[episode] {run['agent']}@{run['version']} run {run['id']} ({run['trigger']}"
            f"{', user ' + run['user'] if run.get('user') else ''}) {run['status']}{lat},"
            f" ${float(run.get('cost_usd') or 0):.4f}. Called: {fmt(called)}. Denied: {fmt(denied)}."
            f" Approvals: {', '.join(approvals) or 'none'}.")
    if run.get("error"):
        text += f" Error: {_clip(run['error'], 160)}."
    if summary:
        text += f" Summary: {summary}"
    return text


# --------------------------------------------------------------------------
# Validation: the curator proposes, this decides
# --------------------------------------------------------------------------

@dataclass
class Proposals:
    summary: str | None = None
    semantic: list[dict[str, Any]] = field(default_factory=list)
    procedural: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)   # (kind, reason)


def parse_output(text: str | None) -> dict[str, Any] | None:
    """The curator's JSON, tolerating a ```json fence or leading prose."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    elif not t.startswith("{"):
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            return None
        t = t[i:j + 1]
    try:
        d = json.loads(t)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def fingerprint(s: str) -> str:
    return hashlib.sha256(_norm(s).encode()).hexdigest()[:16]


def _jaccard(a: str, b: str) -> float:
    x, y = set(_norm(a).split()), set(_norm(b).split())
    return len(x & y) / len(x | y) if x and y else 0.0


def _evidence(cited: Any, events: list[dict[str, Any]]) -> tuple[list[int], bool]:
    """(valid cited seqs, grounded?). Grounded = every cited seq exists and at
    least one is a permitted tool result or the run's final output."""
    by_seq = {e["seq"]: e for e in events}
    seqs = [int(s) for s in (cited or []) if isinstance(s, (int, float, str)) and str(s).isdigit()]
    if not seqs or any(s not in by_seq for s in seqs):
        return seqs, False
    strong = any((by_seq[s]["type"] == "tool_call" and by_seq[s].get("allowed"))
                 or by_seq[s]["type"] == "span_close" for s in seqs)
    return seqs, strong


def _clean(text: Any, redactor: Redactor, limit: int) -> tuple[str | None, str | None]:
    """(clean text, None) or (None, drop reason)."""
    if not isinstance(text, str) or not text.strip():
        return None, "empty"
    text = " ".join(text.split())
    if len(text) > limit:
        return None, "too_long"
    if INJECTION.search(text):
        return None, "directive"
    red, hits = redactor.redact_text(text)
    if hits or "[REDACTED" in text:
        return None, "secret"  # a memory about a secret value is never worth keeping
    return red, None


def validate_proposals(raw: dict[str, Any] | None, events: list[dict[str, Any]],
                       redactor: Redactor) -> Proposals:
    out = Proposals()
    if raw is None:
        out.dropped.append(("all", "unparseable"))
        return out
    summary, why = _clean(raw.get("episode_summary"), redactor, 300)
    out.summary = summary
    if why and why != "empty":
        out.dropped.append(("summary", why))
    for item in (raw.get("semantic") or [])[:10]:
        if not isinstance(item, dict):
            out.dropped.append(("semantic", "malformed"))
            continue
        fact, why = _clean(item.get("fact"), redactor, MAX_FACT_CHARS)
        seqs, grounded = _evidence(item.get("evidence_seq"), events)
        conf = item.get("confidence")
        if why:
            out.dropped.append(("semantic", why))
        elif not grounded:
            out.dropped.append(("semantic", "ungrounded"))
        elif not isinstance(conf, (int, float)) or conf < SEMANTIC_MIN_CONFIDENCE:
            out.dropped.append(("semantic", "low_confidence"))
        else:
            out.semantic.append({"fact": fact, "evidence_seq": seqs, "confidence": float(conf)})
    for item in (raw.get("procedural") or [])[:3]:
        if not isinstance(item, dict):
            out.dropped.append(("procedural", "malformed"))
            continue
        title, why = _clean(item.get("title"), redactor, 120)
        when, why2 = _clean(item.get("applies_when") or "always", redactor, 200)
        steps = []
        for s in (item.get("steps") or [])[:MAX_STEPS]:
            c, w = _clean(s, redactor, MAX_STEP_CHARS)
            if w:
                why = why or w
            else:
                steps.append(c)
        seqs, grounded = _evidence(item.get("evidence_seq"), events)
        conf = item.get("confidence")
        if why or why2:
            out.dropped.append(("procedural", why or why2))
        elif not steps:
            out.dropped.append(("procedural", "no_steps"))
        elif not grounded:
            out.dropped.append(("procedural", "ungrounded"))
        elif not isinstance(conf, (int, float)) or conf < PROCEDURAL_MIN_CONFIDENCE:
            out.dropped.append(("procedural", "low_confidence"))
        else:
            out.procedural.append({"title": title, "applies_when": when, "steps": steps,
                                   "evidence_seq": seqs, "confidence": float(conf)})
    return out


def semantic_text(item: dict[str, Any], run_id: int) -> str:
    seqs = ",".join(str(s) for s in item["evidence_seq"])
    return (f"{item['fact']} [fact {fingerprint(item['fact'])} from run {run_id} events {seqs},"
            f" confidence {item['confidence']:.2f}]")


def procedural_text(item: dict[str, Any], run_id: int) -> str:
    steps = " ".join(f"{i}. {s}" for i, s in enumerate(item["steps"], 1))
    seqs = ",".join(str(s) for s in item["evidence_seq"])
    return (f"[procedure] {item['title']}. When: {item['applies_when']}. Steps: {steps}"
            f" [proc {fingerprint(item['title'] + steps)} from run {run_id} events {seqs}]")


# --------------------------------------------------------------------------
# The store: the real memory wrapper, or an in-memory twin for fakes/tests
# --------------------------------------------------------------------------

#: In-memory twin of memory_items, used when connectors are fake.
FAKE_ITEMS: list[dict[str, Any]] = []
FAKE_BUCKETS: dict[str, dict[str, str]] = {
    "platform-eng": {"semantic": "platform-eng-notes", "procedural": "platform-eng-runbooks",
                     "episodic": "platform-eng-history"},
}


class MemoryStore:
    """Writes go through the memory MCP wrapper with the root run's identity,
    so the wrapper's membership and bucket checks apply to curation exactly as
    to any agent. Only episodic writes add X-Reagent-Invocation: system."""

    def __init__(self, identity: dict[str, str]) -> None:
        from runtime.engine.binding import connector_mode
        self.identity = dict(identity)
        self.live = connector_mode() == "live"

    # -- buckets ------------------------------------------------------------
    def buckets(self, team: str | None) -> dict[str, str]:
        """{type: bucket} owned by the team. enterprise-kb is never written."""
        if not team:
            return {}
        if not self.live or not database_url():
            return dict(FAKE_BUCKETS.get(team, {}))
        from runtime.engine.recorder import _shared_conn
        rows = _shared_conn(database_url()).execute(
            "select b.type, b.name from memory_buckets b join teams t on t.id=b.owner_team_id"
            " where t.name=%s order by b.id", (team,)).fetchall()
        out: dict[str, str] = {}
        for typ, name in rows:
            out.setdefault(typ, name)
        return out

    # -- calls --------------------------------------------------------------
    async def _call(self, tool: str, args: dict[str, Any], *, system: bool = False) -> Any:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from runtime.engine.binding import wrapper_headers
        from runtime.guardrails.permissions import GrantSet, connector_actions
        headers = wrapper_headers("memory", GrantSet(), self.identity)
        if system:
            headers["X-Reagent-Invocation"] = "system"
        url = connector_actions()["connectors"]["memory"]["transport_url"]
        client = MultiServerMCPClient({"memory": {"transport": "streamable_http", "url": url,
                                                  "headers": headers}})
        tools = {t.name: t for t in await client.get_tools(server_name="memory")}
        res = await tools[tool].ainvoke(args)
        text = res if isinstance(res, str) else "".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in (res if isinstance(res, list) else [res]))
        if "Error executing tool" in text or text.startswith("E_"):
            raise RuntimeError(_clip(text, 200))
        try:
            return json.loads(text)
        except ValueError:
            return text

    async def write(self, bucket: str, content: str, kind: str, run_id: int) -> dict[str, Any]:
        if not self.live:
            status = "pending" if kind == "procedural" else "active"
            item = {"id": len(FAKE_ITEMS) + 1, "bucket": bucket, "content": content,
                    "source_run_id": run_id, "status": status, "type": kind}
            FAKE_ITEMS.append(item)
            return {"id": item["id"], "status": status}
        return await self._call("write", {"bucket": bucket, "content": content},
                                system=(kind == "episodic"))

    async def search(self, bucket: str, query: str) -> list[dict[str, Any]]:
        if not self.live:
            q = set(_norm(query).split())
            return [i for i in FAKE_ITEMS if i["bucket"] == bucket and i["status"] == "active"
                    and q & set(_norm(i["content"]).split())]
        res = await self._call("search", {"bucket": bucket, "query": query})
        return res if isinstance(res, list) else []

    async def is_duplicate(self, bucket: str, item: dict[str, Any]) -> bool:
        """Exact (fingerprint token) or near (token Jaccard) duplicate."""
        fp = fingerprint(item["fact"])
        hits = await self.search(bucket, fp) + await self.search(bucket, item["fact"])
        for h in hits:
            content = str(h.get("content", ""))
            if fp in content or _jaccard(item["fact"], content.split(" [fact ")[0]) >= NEAR_DUPLICATE:
                return True
        return False


# --------------------------------------------------------------------------
# Curation
# --------------------------------------------------------------------------

def _metric(rec: Recorder, name: str, value: float, detail: Any = None) -> None:
    payload: dict[str, Any] = {"name": name, "value": value}
    if detail is not None:
        payload["detail"] = detail
    rec.event("metric", payload=payload)


def skip_reason(run: dict[str, Any], events: list[dict[str, Any]]) -> str | None:
    if run.get("parent_run_id"):
        return "child_run"
    if run.get("trigger") not in CURATE_TRIGGERS:
        return f"trigger_{run.get('trigger')}"
    if run.get("agent") == CURATOR:
        return "curator"
    if run.get("status") not in TERMINAL:
        return f"status_{run.get('status')}"
    if any(e["type"] == "metric" and (e.get("payload_redacted") or {}).get("name") == "memory_curated"
           for e in events):
        return "already_curated"
    return None


def _team_monthly_cap(agent: str) -> float | None:
    try:
        from runtime.engine.runner import load_resolved
        team = (load_resolved(agent)["effective"].get("aggregate_limits") or {}).get("team") or {}
        return team.get("usd_per_month")
    except Exception:  # noqa: BLE001
        return None


async def curate(run_id: int, identity: dict[str, str]) -> dict[str, Any]:
    """Curate one finished run. Returns a summary; records metrics on the run."""
    from runtime.engine.runner import arun_agent

    rec = Recorder(Redactor(secrets=True, pii=True))
    try:
        run = rec.run_row(run_id)
        events = rec.events(run_id)
        why = skip_reason(run, events)
        if why:
            return {"run_id": run_id, "skipped": why}
        rec.attach(run_id)
        # Identity comes from the run's own row, never from the caller: the
        # resume and reject paths only know the run id, and the memory wrapper
        # checks team membership against these numeric ids.
        identity = {k: str(v) for k, v in {"X-Reagent-Run-ID": run_id,
                                           "X-Reagent-Team-ID": run.get("team_id"),
                                           "X-Reagent-User-ID": run.get("user_id")}.items()
                    if v is not None} or dict(identity)
        store = MemoryStore(identity)
        buckets = store.buckets(run.get("team"))
        if not buckets:
            _metric(rec, "memory_curated", 0, {"skipped": "team_has_no_buckets"})
            return {"run_id": run_id, "skipped": "no_buckets"}
        cap = float(os.environ.get("REAGENT_MEMORY_DAILY_USD", "1.0"))
        if rec.team_spend_today(run["team"], CURATOR) >= cap:
            _metric(rec, "memory_curated", 0, {"skipped": "daily_cap"})
            return {"run_id": run_id, "skipped": "daily_cap"}

        cur = await arun_agent(
            CURATOR, {"run_trace": build_trace(run, events), "agent": run["agent"],
                      "team": run["team"]},
            user=run.get("user"), trigger="system", charge_team=run["team"],
            charge_team_cap_usd=_team_monthly_cap(run["agent"]))
        props = validate_proposals(parse_output(cur.get("output")), events, rec.redactor)
        written: dict[str, int] = {"episodic": 0, "semantic": 0, "procedural": 0}
        pending: list[tuple[int, str, str]] = []

        async def put(kind: str, text: str) -> dict[str, Any] | None:
            try:
                r = await store.write(buckets[kind], text, kind, run_id)
                written[kind] += 1
                return r if isinstance(r, dict) else {}
            except Exception as e:  # noqa: BLE001 — one failed write never loses the others
                props.dropped.append((kind, f"write_failed: {_clip(str(e), 80)}"))
                return None

        if "episodic" in buckets:
            await put("episodic", episodic_record(run, events, props.summary))
        if "semantic" in buckets:
            for item in props.semantic:
                if await store.is_duplicate(buckets["semantic"], item):
                    props.dropped.append(("semantic", "duplicate"))
                else:
                    await put("semantic", semantic_text(item, run_id))
        if "procedural" in buckets:
            for item in props.procedural:
                text = procedural_text(item, run_id)
                r = await put("procedural", text)
                if r and r.get("id"):
                    pending.append((int(r["id"]), buckets["procedural"], text))

        for kind, n in written.items():
            if n:
                _metric(rec, f"memory_written.{kind}", n)
        reasons: dict[str, int] = {}
        for kind, why in props.dropped:
            key = f"{kind}:{why.split(':')[0]}"
            reasons[key] = reasons.get(key, 0) + 1
        for key, n in sorted(reasons.items()):
            _metric(rec, "memory_dropped", n, {"reason": key})
        _metric(rec, "memory_curated", 1, {"curator_run_id": cur.get("run_id"),
                                           "curator_status": cur.get("status"),
                                           "cost_usd": cur.get("cost_usd")})
        summary = {"run_id": run_id, "curator_run_id": cur.get("run_id"), "written": written,
                   "dropped": props.dropped, "pending_procedures": [p[0] for p in pending]}
    finally:
        rec.close()

    if pending and gate_mode() == "auto":
        for item_id, bucket, text in pending:
            summary.setdefault("gate", []).append(
                await asyncio.to_thread(gate_procedure, item_id, bucket, text, run["team"], run_id))
    return summary


# --------------------------------------------------------------------------
# Procedural eval gate
# --------------------------------------------------------------------------

def _with_candidate(doc: dict[str, Any], text: str) -> dict[str, Any]:
    """The agent doc with the candidate procedure in every case's memory mocks."""
    d = copy.deepcopy(doc)
    for case in (d.get("spec", {}).get("evaluation") or {}).get("cases") or []:
        mocks = case.setdefault("mocks", {})
        read = mocks.setdefault("memory:read", {})
        if isinstance(read, dict):
            read.setdefault("entries", []).append({"content": text})
        search = mocks.setdefault("memory:search", {})
        if isinstance(search, dict):
            search.setdefault("results", []).append({"text": text})
    return d


def readers_of(bucket: str, team: str) -> list[str]:
    """Team agents whose resolved grant reads or searches this bucket."""
    from runtime.engine.runner import load_resolved
    try:
        from runtime.resolver import load_catalog
        names = list(load_catalog().agents)
    except Exception:  # noqa: BLE001
        names = []
    out = []
    for name in names:
        try:
            doc = load_resolved(name)
        except Exception:  # noqa: BLE001
            continue
        if doc.get("team") != team:
            continue
        for t in doc["effective"]["tool_permissions"]:
            if t["connector"] == "memory" and t["action"] in ("read", "search") \
                    and bucket in (t.get("resources") or {}).get("bucket", []):
                out.append(name)
                break
    return sorted(set(out))


_BASELINES: dict[tuple[str, str, str], dict[str, Any]] = {}


def gate_procedure(item_id: int, bucket: str, text: str, team: str,
                   source_run_id: int | None = None, runs_per_case: int = 2) -> dict[str, Any]:
    """Promote a pending procedure only if no agent that reads the bucket
    regresses on its evals with it. Refuses the fake model: it ignores memory,
    so the gate would pass vacuously (as :free models do for budgets)."""
    from runtime.engine.binding import model_mode
    verdict: dict[str, Any] = {"item_id": item_id, "agents": {}}
    if model_mode() != "live":
        verdict.update(decision="pending", reason="gate needs REAGENT_MODEL=live")
        _record_gate(verdict, source_run_id)
        return verdict
    from runtime.evals import run_evals
    from runtime.resolver import load_catalog
    agents = readers_of(bucket, team)
    if not agents:
        verdict.update(decision="pending", reason="no agent reads this bucket")
        _record_gate(verdict, source_run_id)
        return verdict
    lock = _advisory_lock(team)
    try:
        cat = load_catalog()
        ok = True
        for agent in agents:
            doc = cat.latest(agent)
            key = (agent, str(doc["metadata"].get("version")), time.strftime("%Y-%m-%d"))
            if key not in _BASELINES:
                _BASELINES[key] = run_evals(agent, runs_per_case=runs_per_case, fake_model=False,
                                            include_inherited=False)
            base = _BASELINES[key]
            cand = run_evals(agent, runs_per_case=runs_per_case, fake_model=False,
                             include_inherited=False, draft=_with_candidate(doc, text))
            new_reasons = sorted(set(cand.get("gate_reasons") or []) - set(base.get("gate_reasons") or []))
            safety_drop = (base.get("safety_pass_rate") is not None
                           and (cand.get("safety_pass_rate") or 0) < base["safety_pass_rate"])
            passed = (cand.get("pass_rate", 0) >= base.get("pass_rate", 0)
                      and not safety_drop and not new_reasons)
            verdict["agents"][agent] = {"baseline": base.get("pass_rate"),
                                        "candidate": cand.get("pass_rate"),
                                        "new_gate_reasons": new_reasons, "passed": passed}
            ok = ok and passed
        verdict["decision"] = "active" if ok else "rejected"
        verdict["reason"] = "no regression" if ok else "regressed an agent's evals"
        _set_status(item_id, verdict["decision"], verdict["reason"])
    finally:
        _advisory_unlock(lock)
    _record_gate(verdict, source_run_id)
    return verdict


def _advisory_lock(team: str) -> Any:
    """One gate at a time per team, across processes."""
    if not database_url():
        return None
    from runtime.engine.recorder import _connect
    conn = _connect(database_url())
    key = int(hashlib.sha256(f"memory-gate:{team}".encode()).hexdigest()[:15], 16)
    conn.execute("select pg_advisory_lock(%s)", (key,))
    return (conn, key)


def _advisory_unlock(lock: Any) -> None:
    if lock:
        conn, key = lock
        try:
            conn.execute("select pg_advisory_unlock(%s)", (key,))
        finally:
            conn.close()


def _set_status(item_id: int, status: str, reason: str) -> None:
    """pending -> active|rejected, through WF4's repository function."""
    from runtime.engine.binding import connector_mode
    if connector_mode() != "live":
        for i in FAKE_ITEMS:
            if i["id"] == item_id and i["status"] == "pending":
                i["status"] = status
        return
    try:
        from runtime.db.memory import set_memory_status  # WF4
    except ImportError:
        log.warning("runtime.db.memory.set_memory_status not available yet; item %s stays pending",
                    item_id)
        return
    from runtime.engine.recorder import _connect
    with _connect(database_url()) as conn:
        set_memory_status(conn, item_id, status, reason)


def _record_gate(verdict: dict[str, Any], source_run_id: int | None) -> None:
    if source_run_id is None:
        return
    rec = Recorder(Redactor())
    try:
        rec.attach(source_run_id)
        name = {"active": "procedure_promoted", "rejected": "procedure_rejected"}.get(
            verdict.get("decision"), "procedure_gate_pending")
        _metric(rec, name, 1, {"item_id": verdict["item_id"], "reason": verdict.get("reason"),
                               "agents": verdict.get("agents")})
    finally:
        rec.close()


def main() -> int:
    """python -m runtime.engine.memory_curation gate <item_id> <bucket> <team> [source_run_id]
    Runs the procedural gate by hand (reads the item's text from the database)."""
    import sys
    args = sys.argv[1:]
    if len(args) < 4 or args[0] != "gate":
        print(main.__doc__)
        return 2
    item_id, bucket, team = int(args[1]), args[2], args[3]
    src = int(args[4]) if len(args) > 4 else None
    from runtime.engine.recorder import _shared_conn
    row = _shared_conn(database_url()).execute(
        "select content from memory_items where id=%s", (item_id,)).fetchone()
    if not row:
        print(f"memory item {item_id} not found")
        return 1
    print(json.dumps(gate_procedure(item_id, bucket, row[0], team, src), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
