"""Memory demo: episodic, semantic and procedural memory from real runs. WF1.

    python -m runtime.engine.memory_demo            # fakes: free, deterministic
    python -m runtime.engine.memory_demo --db       # same, recorded in DATABASE_URL
                                                    # (the trace viewer shows the metrics)
    python -m runtime.engine.memory_demo --live     # real OpenRouter for pr-reviewer AND
                                                    # memory-curator; exact checks off,
                                                    # invariants only (~$0.02)
    ... add --gate with --live to run the REAL procedural eval gate (~$0.05).

Drives fixtures/memory/scenario.yaml: three pr-reviewer runs on platform-eng
(A: PR 42 approved, B: merge request refused, C: PR 42 again), then the
procedural eval gate, then the kill switch. Under the fake model the curator
says exactly what the scenario scripts, so each proposal's outcome is known in
advance and checked. The rest of the pipeline is the real one: the curator run
(trigger=system, charged to the team), build_trace, validate_proposals, the
store, dedup, readers_of, _with_candidate, the gate's status flip, the metrics.

PASS/FAIL lines are requirements; WARN lines are known gaps that do not fail
the exit code.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import pathlib
import re
import sys
from collections import Counter
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "fixtures" / "memory" / "scenario.yaml"
_LINE = re.compile(r"^(\d+) \| (\w+) \| ([^|]*) \| ([^|]*) \|", re.M)

results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "", *, warn: bool = False) -> bool:
    level = "PASS" if ok else ("WARN" if warn else "FAIL")
    results.append((level, name, detail))
    print(f"  {level}  {name}" + (f"  ({detail})" if detail else ""))
    return ok


# --------------------------------------------------------------------------
# The scripted curator
# --------------------------------------------------------------------------

def resolve_evidence(refs: list[Any], trace: str) -> tuple[list[int], list[str]]:
    """Symbolic evidence -> seq numbers in this trace. (seqs, unresolved refs)."""
    lines = [(int(s), t, a.strip(), al.strip()) for s, t, a, al in _LINE.findall(trace)]
    seqs, missing = [], []
    for ref in refs:
        if isinstance(ref, int):
            seqs.append(ref)
            continue
        if ref == "output":
            hit = [s for s, t, a, _ in lines if t == "span_close" and not a]
        else:
            hit = [s for s, t, a, al in lines if t == "tool_call" and a == ref and al == "allowed"]
        if hit:
            seqs.append(hit[-1] if ref == "output" else hit[0])
        else:
            missing.append(str(ref))
    return seqs, missing


class ScriptedCurator:
    """Replaces FakeChatModel._curator so the curator says what the scenario says."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self.current: str | None = None
        self.calls: Counter[str] = Counter()
        self.unresolved: list[str] = []

    def install(self) -> None:
        from langchain_core.messages import AIMessage
        from runtime.engine.fakes import FakeChatModel
        me = self

        def _curator(model: Any, trace: str) -> AIMessage:
            key = me.current or "?"
            me.calls[key] += 1
            script = me.scenario["runs"][key]["curator"]

            def items(kind: str) -> list[dict[str, Any]]:
                out = []
                for p in script.get(kind) or []:
                    seqs, missing = resolve_evidence(p.get("evidence") or [], trace)
                    me.unresolved += [f"run {key}: {m}" for m in missing]
                    out.append({k: v for k, v in p.items()
                                if k not in ("expect", "evidence", "gate", "repeat_of")}
                               | {"evidence_seq": seqs})
                return out
            return AIMessage(content=json.dumps({
                "episode_summary": (script.get("episode_summary") or {}).get("text"),
                "semantic": items("semantic"), "procedural": items("procedural")}))

        FakeChatModel._curator = _curator  # type: ignore[method-assign]


def expected_drops(script: dict[str, Any]) -> Counter[str]:
    c: Counter[str] = Counter()
    if (script.get("episode_summary") or {}).get("expect", "kept") != "kept":
        c[f"summary:{script['episode_summary']['expect']}"] += 1
    for kind in ("semantic", "procedural"):
        for p in script.get(kind) or []:
            if p["expect"] != "kept":
                c[f"{kind}:{p['expect']}"] += 1
    return c


# --------------------------------------------------------------------------
# Reading back what was stored
# --------------------------------------------------------------------------

def all_items(run_ids: list[int]) -> list[dict[str, Any]]:
    """Memory items written by these runs: the wrapper's table, or its twin."""
    from runtime.engine import memory_curation as mc
    from runtime.engine.binding import connector_mode
    from runtime.engine.recorder import database_url
    if connector_mode() != "live" or not database_url():
        return [dict(i) for i in mc.FAKE_ITEMS if i["source_run_id"] in run_ids]
    import psycopg
    with psycopg.connect(database_url()) as c:
        rows = c.execute(
            "select i.id, b.name, b.type, i.content, i.source_run_id, i.status from memory_items i"
            " join memory_buckets b on b.id=i.bucket_id where i.source_run_id = any(%s)"
            " order by i.id", (run_ids,)).fetchall()
    return [dict(zip(("id", "bucket", "type", "content", "source_run_id", "status"), r)) for r in rows]


def metrics(run_id: int) -> list[dict[str, Any]]:
    from runtime.engine.recorder import Recorder
    from runtime.guardrails.redaction import Redactor
    rec = Recorder(Redactor())
    try:
        return [e["payload_redacted"] or {} for e in rec.events(run_id) if e["type"] == "metric"]
    finally:
        rec.close()


def drop_counts(run_id: int) -> Counter[str]:
    return Counter({m["detail"]["reason"]: int(m["value"]) for m in metrics(run_id)
                    if m.get("name") == "memory_dropped"})


def search(bucket: str, query: str) -> list[str]:
    """What an agent reading the bucket would get back (active items only)."""
    from runtime.engine.memory_curation import MemoryStore
    return [str(h.get("content")) for h in asyncio.run(MemoryStore({}).search(bucket, query))]


# --------------------------------------------------------------------------
# Running the scenario
# --------------------------------------------------------------------------

def run_one(key: str, scn: dict[str, Any], curator: ScriptedCurator | None,
            args: argparse.Namespace) -> dict[str, Any]:
    from runtime.engine import decide_approval, run_agent
    spec = scn["runs"][key]
    inp = dict(spec["input"])
    if args.connectors == "live":
        inp.update(repo=args.repo, pr_number=args.pr)
    if curator:
        curator.current = key
    print(f"\nRun {key}: {spec['title']}")
    r = run_agent(spec["agent"], inp, user="alice")
    paused_items = len(all_items([r["run_id"]]))
    final = r
    if r.get("approvals"):
        decision = spec["approval"] if (args.connectors != "live" or args.post) else "rejected"
        final = decide_approval(r["approvals"][0], decision, "alice")
    return {"key": key, "run_id": r["run_id"], "first": r, "final": final,
            "paused_items": paused_items if r.get("approvals") else None}


def invariants(runs: list[dict[str, Any]], scn: dict[str, Any]) -> None:
    """Hold for ANY curator, scripted or real."""
    from runtime.engine import memory_curation as mc
    from runtime.engine.recorder import Recorder
    from runtime.guardrails.redaction import Redactor
    ids = [r["run_id"] for r in runs]
    items = all_items(ids)
    rec = Recorder(Redactor())
    try:
        events = {i: {e["seq"]: e for e in rec.events(i)} for i in ids}
    finally:
        rec.close()
    for r in runs:
        eps = [i for i in items if i["type"] == "episodic" and i["source_run_id"] == r["run_id"]]
        curated = any(m.get("name") == "memory_curated" for m in metrics(r["run_id"]))
        check(f"run {r['key']}: exactly one episode", len(eps) == 1,
              f"{len(eps)} found" + ("" if curated else
                                     f"; run {r['run_id']} ({r['final']['status']}) was never curated"))
    ungrounded = []
    for i in (i for i in items if i["type"] in ("semantic", "procedural")):
        m = re.search(r"from run (\d+) events (\d+(?:,\d+)*)", i["content"])
        if not m or int(m.group(1)) != i["source_run_id"]:
            ungrounded.append(f"item {i['id']}: no provenance")
            continue
        ev = events.get(int(m.group(1)), {})
        seqs = [int(s) for s in m.group(2).split(",")]
        if any(s not in ev for s in seqs) or not any(
                (ev[s]["type"] == "tool_call" and ev[s].get("allowed")) or ev[s]["type"] == "span_close"
                for s in seqs if s in ev):
            ungrounded.append(f"item {i['id']}: cites {seqs}")
    check("every fact and procedure cites real events of its own run", not ungrounded,
          "; ".join(ungrounded[:3]))
    blob = json.dumps(items)
    check("no planted secret in any memory item",
          not any(k in blob for k in scn["planted_secrets"]) and "[REDACTED" not in blob)
    injected = [i["id"] for i in items if mc.INJECTION.search(i["content"])]
    check("no directive aimed at agents in any memory item", not injected, f"items {injected}")
    wrong = [i["bucket"] for i in items if i["bucket"] not in scn["buckets"].values()]
    check("written only to the team's own buckets (never enterprise-kb)", not wrong, ", ".join(wrong))


def act_episodic(runs: dict[str, dict[str, Any]], scn: dict[str, Any],
                 curator: ScriptedCurator | None) -> None:
    from runtime.engine.recorder import Recorder
    from runtime.guardrails.redaction import Redactor
    print("\nEpisodic: what happened")
    a, b = runs["A"], runs["B"]
    if a["paused_items"] is not None:
        check("nothing curated while run A waits for approval", a["paused_items"] == 0,
              f"{a['paused_items']} items before the decision")
    for r in runs.values():
        if curator:
            want = scn["runs"][r["key"]]["expect_status"]
            check(f"run {r['key']} ends {want}", r["final"]["status"] == want,
                  f"status {r['final']['status']}")
    ep = {k: next((i["content"] for i in all_items([r["run_id"]]) if i["type"] == "episodic"), "")
          for k, r in runs.items()}
    print(f"        A: {ep['A'][:150]}…")
    if curator:
        check("episode A is built from the recorded run",
              all(s in ep["A"] for s in ("github:pulls.read x1", "agent:secret-scanner x1",
                                         "Approvals: github:pulls.comment approved", "succeeded")))
        check("episode A keeps the curator's summary", "Summary: Reviewed nmc2/infra#42" in ep["A"])
        check("episode B records the refused merge and the rejection",
              "Denied: github:pulls.merge" in ep["B"] and "github:pulls.comment rejected" in ep["B"],
              ep["B"][ep["B"].find("Denied"):][:90])
        check("episode B drops the injected summary but is still written",
              bool(ep["B"]) and "Summary:" not in ep["B"] and "carol" not in ep["B"])
    rec = Recorder(Redactor())
    try:
        curated = [m for m in metrics(a["run_id"]) if m.get("name") == "memory_curated"]
        cid = (curated[0].get("detail") or {}).get("curator_run_id") if curated else None
        row = rec.run_row(cid) if cid else {}
        check("memory-curator ran as trigger=system, charged to platform-eng",
              row.get("agent") == "memory-curator" and row.get("trigger") == "system"
              and row.get("team") == scn["team"], f"curator run {cid}")
        check("the curator's own run is not curated (no recursion)",
              bool(cid) and not all_items([cid]) and not any(
                  m.get("name") == "memory_curated" for m in metrics(cid)))
        kids = [c if isinstance(c, int) else c.get("run_id") for c in a["first"].get("children") or []]
        check("the secret-scanner child run is not curated on its own",
              bool(kids) and not all_items(kids) and not any(
                  m.get("name") == "memory_curated" for k in kids for m in metrics(k)),
              f"child runs {kids}")
    finally:
        rec.close()
    if curator:
        check("the curator was called once per finished root run",
              dict(curator.calls) == {"A": 1, "B": 1, "C": 1}, str(dict(curator.calls)))


def act_semantic(runs: dict[str, dict[str, Any]], scn: dict[str, Any]) -> None:
    from runtime.engine import memory_curation as mc
    print("\nSemantic: what is true")
    bucket = scn["buckets"]["semantic"]
    for key in ("A", "B", "C"):
        script = scn["runs"][key]["curator"]
        want = [p["fact"] for p in script.get("semantic") or [] if p["expect"] == "kept"]
        got = [i["content"].split(" [fact ")[0] for i in all_items([runs[key]["run_id"]])
               if i["type"] == "semantic"]
        check(f"run {key}: {len(want)} grounded fact(s) written active", got == want,
              f"got {len(got)}" + ("" if got == want else f": {got}"))
        want_d, got_d = expected_drops(script), drop_counts(runs[key]["run_id"])
        check(f"run {key}: every bad proposal dropped for the right reason", got_d == want_d,
              ", ".join(f"{k} x{v}" for k, v in sorted(got_d.items())) or "none dropped"
              + ("" if got_d == want_d else f"; expected {dict(want_d)}"))
    hits = search(bucket, "Which S3 bucket holds the build artifacts?")
    check("recall: a later search finds the learned fact",
          any("nmc2-build-artifacts" in h for h in hits), f"{len(hits)} hit(s)")
    before = len(all_items([runs["A"]["run_id"]]))
    again = asyncio.run(mc.curate(runs["A"]["run_id"], {}))
    check("re-curating run A is a no-op (idempotent)",
          again.get("skipped") == "already_curated" and len(all_items([runs["A"]["run_id"]])) == before,
          str(again.get("skipped")))


@contextlib.contextmanager
def stubbed_gate(scn: dict[str, Any]):
    """run_evals answers from the scenario's gate_stub; the rest of the gate is real."""
    import runtime.engine.binding as binding
    import runtime.evals as evals
    from runtime.engine import memory_curation as mc
    stub = scn["gate_stub"]
    outcome = {p["title"]: p["gate"] for r in scn["runs"].values()
               for p in r["curator"].get("procedural") or [] if p.get("gate")}
    calls: list[dict[str, Any]] = []

    def run_evals(agent: str, runs_per_case: int | None = None, *, draft: Any = None, **kw: Any):
        if draft is None:
            calls.append({"agent": agent, "kind": "baseline"})
            return dict(stub["baseline"])
        blob = json.dumps(draft)
        title = next((t for t in outcome if t in blob), None)
        calls.append({"agent": agent, "kind": outcome.get(title, "?"), "in_mocks": title is not None})
        return dict(stub[outcome.get(title, "baseline")])

    saved = (evals.run_evals, binding.model_mode)
    evals.run_evals, binding.model_mode = run_evals, (lambda: "live")
    mc._BASELINES.clear()
    try:
        yield calls
    finally:
        evals.run_evals, binding.model_mode = saved


def act_procedural(runs: dict[str, dict[str, Any]], scn: dict[str, Any],
                   args: argparse.Namespace) -> None:
    from runtime.engine import memory_curation as mc
    print("\nProcedural: how to do it")
    bucket = scn["buckets"]["procedural"]
    ids = [r["run_id"] for r in runs.values()]
    procs = [i for i in all_items(ids) if i["type"] == "procedural"]
    check("procedures are written pending, never active",
          bool(procs) and all(i["status"] == "pending" for i in procs),
          f"{len(procs)} pending")
    check("a pending procedure is invisible to agents",
          not any("public-read" in h for h in search(bucket, "public-read S3 ACLs severity")))
    gated = [m for m in metrics(runs["A"]["run_id"]) if m.get("name", "").startswith("procedure_")]
    if not args.live:
        check("the gate refuses to judge with the fake model",
              any(m["name"] == "procedure_gate_pending" and "REAGENT_MODEL=live" in str(m.get("detail"))
                  for m in gated), "it would pass vacuously: the fake ignores memory")
    if args.live and not args.gate:
        print("        (gate skipped: add --gate to run real evals, ~$0.05)")
        return

    def find(title: str, key: str) -> dict[str, Any] | None:
        return next((i for i in all_items([runs[key]["run_id"]])
                     if i["type"] == "procedural" and f"] {title}." in i["content"]), None)
    good = find("Flag public-read S3 ACLs as high severity", "A")
    bad = find("Keep reviews of provider bumps to one line", "C")
    if not (good and bad):
        check("both gate candidates are pending", False, f"good={bool(good)} bad={bool(bad)}")
        return
    ctx = contextlib.nullcontext([]) if args.live else stubbed_gate(scn)
    with ctx as calls:
        v1 = mc.gate_procedure(good["id"], bucket, good["content"], scn["team"], runs["A"]["run_id"])
        v2 = mc.gate_procedure(bad["id"], bucket, bad["content"], scn["team"], runs["C"]["run_id"])
    print(f"        gate: {json.dumps(v1['agents'])}")
    if not args.live:
        check("the gate re-ran pr-reviewer's evals with the lesson in its memory mocks",
              [c["agent"] for c in calls if c["kind"] != "baseline"] == ["pr-reviewer"] * 2
              and all(c["in_mocks"] for c in calls if c["kind"] != "baseline"),
              f"{len(calls)} eval runs, baseline cached")
    status = {i["id"]: i["status"] for i in all_items(ids)}
    check("a lesson that does not regress is promoted", v1.get("decision") == "active"
          and status.get(good["id"]) == "active", f"{v1.get('decision')}: {v1.get('reason')}")
    check("a lesson that regresses an agent is rejected", v2.get("decision") == "rejected"
          and status.get(bad["id"]) == "rejected", f"{v2.get('decision')}: {v2.get('reason')}")
    check("the promoted lesson now reaches agents, the rejected one never does",
          any("public-read" in h for h in search(bucket, "public-read S3 ACLs severity"))
          and not any("LGTM" in h for h in search(bucket, "provider bumps LGTM one line")))
    names = {m["name"] for k in ("A", "C") for m in metrics(runs[k]["run_id"])}
    check("each verdict is a metric on its source run",
          {"procedure_promoted", "procedure_rejected"} <= names)
    dups = [i for i in all_items(ids) if i["type"] == "procedural"
            and "] Flag public-read S3 ACLs as high severity." in i["content"]]
    check("a re-proposed lesson does not create a second procedure", len(dups) == 1,
          f"{len(dups)} rows for the same lesson: procedures are not deduped yet", warn=True)


def act_kill_switch(scn: dict[str, Any], curator: ScriptedCurator | None,
                    args: argparse.Namespace) -> None:
    print("\nKill switch: REAGENT_MEMORY=off")
    os.environ["REAGENT_MEMORY"] = "off"
    try:
        r = run_one("A", scn, curator, args)
    finally:
        os.environ["REAGENT_MEMORY"] = "sync"
    check("a run with memory off writes nothing and is not curated",
          not all_items([r["run_id"]]) and not any(
              m.get("name") == "memory_curated" for m in metrics(r["run_id"])))


def show_memory(ids: list[int]) -> None:
    print("\nWhat platform-eng now remembers")
    for i in all_items(ids):
        c = i["content"]
        print(f"  {i['bucket']:<22} {i['status']:<8} run {i['source_run_id']:<3} "
              f"{c[:96] + ('…' if len(c) > 96 else '')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="store_true", help="record runs in DATABASE_URL")
    ap.add_argument("--live", action="store_true",
                    help="real model for pr-reviewer and memory-curator (spends credits)")
    ap.add_argument("--gate", action="store_true", help="with --live: run the real eval gate")
    ap.add_argument("--connectors", choices=["fake", "live"], default="fake",
                    help="live: memory goes through the real wrapper into memory_items")
    ap.add_argument("--repo", default="nmc2/infra")
    ap.add_argument("--pr", type=int, default=42)
    ap.add_argument("--post", action="store_true",
                    help="with live connectors, approve and really post the comments")
    args = ap.parse_args()

    from runtime.engine.rehearse import _load_env
    _load_env()
    if not args.db and args.connectors != "live":
        os.environ.pop("DATABASE_URL", None)
    os.environ.update(REAGENT_MODEL="live" if args.live else "fake", REAGENT_MEMORY="sync",
                      REAGENT_MEMORY_GATE="auto", REAGENT_CONNECTORS=args.connectors)

    import yaml
    from runtime.engine.recorder import database_url
    scn = yaml.safe_load(SCENARIO.read_text())
    db = database_url()
    print(f"model={os.environ['REAGENT_MODEL']}  connectors={args.connectors}  "
          f"db={db.split('@')[-1].split('/')[0] if db else 'in-memory'}  "
          f"curator={'real model' if args.live else 'scripted (' + SCENARIO.name + ')'}")

    curator = None if args.live else ScriptedCurator(scn)
    if curator:
        curator.install()
    runs = {k: run_one(k, scn, curator, args) for k in ("A", "B", "C")}
    if curator and curator.unresolved:
        check("scenario evidence refs resolve against the real traces", False,
              ", ".join(curator.unresolved))

    print("\nInvariants (any curator)")
    invariants(list(runs.values()), scn)
    act_episodic(runs, scn, curator)
    if curator:
        act_semantic(runs, scn)
    act_procedural(runs, scn, args)
    act_kill_switch(scn, curator, args)
    show_memory([r["run_id"] for r in runs.values()])

    bad = [n for lvl, n, _ in results if lvl == "FAIL"]
    warn = sum(1 for lvl, _, _ in results if lvl == "WARN")
    print(f"\n{sum(1 for lvl, _, _ in results if lvl == 'PASS')}/{len(results)} passed"
          + (f", {warn} warning(s)" if warn else "")
          + (f". Trace for run A: GET /v0/runs/{runs['A']['run_id']}/trace" if not bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
