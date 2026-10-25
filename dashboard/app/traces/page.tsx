"use client";

import { useState } from "react";
import Link from "next/link";
import { getDemoUser, isHandoffReady, traceForRun } from "../../lib/api";
import type { components } from "../../lib/api-schema";

type Event = components["schemas"]["run_events.schema"];
type Trace = { run_id: number; children?: number[]; events: Event[] };
const payload = (event: Event) => event.payload_redacted ?? {};

export default function TracePage() {
  const [runId, setRunId] = useState("1");
  const [trace, setTrace] = useState<Trace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function load() {
    const id = Number(runId);
    if (!Number.isInteger(id) || id < 1) { setError("Enter a valid run ID."); return; }
    if (!isHandoffReady("H2")) { setError("H2 is not ready yet; the trace fixture is missing in this checkout."); return; }
    setLoading(true); setError("");
    try { const result = await traceForRun(id, getDemoUser()); if (result.data) setTrace(result.data); else setError("Trace unavailable for this run."); }
    catch { setError("Runtime API unavailable. Trace fixture files are not present in this checkout."); }
    finally { setLoading(false); }
  }
  const events = trace?.events.slice().sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0)) ?? [];
  const spans = new Map<string, string | null>();
  const spanTimes = new Map<string, { start?: number; end?: number }>();
  for (const event of events) if (event.span_id) {
    spans.set(event.span_id, event.parent_span_id ?? null);
    if (event.ts && (event.type === "span_open" || event.type === "span_close")) {
      const times = spanTimes.get(event.span_id) ?? {};
      times[event.type === "span_open" ? "start" : "end"] = new Date(event.ts).getTime();
      spanTimes.set(event.span_id, times);
    }
  }
  const depth = (event: Event) => { let parent = event.parent_span_id; let level = 0; while (parent && level < 8) { level++; parent = spans.get(parent) ?? null; } return level; };
  return <div className="shell"><aside className="rail"><div className="brand">ReAgent<small>POLICY CONTROL PLANE</small></div><nav className="nav"><Link href="/">Resolved view</Link><Link className="active" href="/traces">Trace viewer</Link><Link href="/publish">Publish pipeline</Link><Link href="/agents">Collection</Link><Link href="/approvals">Approvals</Link></nav></aside><main>
    <header className="page-head"><div><div className="eyebrow">Runs / Trace</div><h1>Trace viewer</h1><p className="subtitle">Span waterfall · redacted event payloads</p></div><form className="toolbar" onSubmit={(e) => { e.preventDefault(); void load(); }}><label className="eyebrow" htmlFor="run-id">RUN ID</label><input id="run-id" className="select" value={runId} onChange={(e) => setRunId(e.target.value)} /><button className="button primary">Load trace</button></form></header>
    <div className="layer-key"><span>Events ordered by sequence</span>{trace && <span>RUN {trace.run_id} · {trace.children?.length ?? 0} child runs</span>}</div>
    {!trace && !loading && <div className="empty-state">Enter a run ID to inspect its recorded spans.</div>}
    {error && <p className="notice">{error}</p>}
    {loading && <div className="resolved">{Array.from({ length: 7 }, (_, i) => <div className="skeleton" key={i} />)}</div>}
    {trace && <section className="resolved waterfall"><div className="resolved-head"><span>Event</span><span>Span / action · duration · post-redaction details</span></div>{events.map((event, index) => { const isDenied = event.type === "tool_call" && event.allowed === false; const isRedaction = event.type === "redaction_hit"; const detail = payload(event); const times = event.span_id ? spanTimes.get(event.span_id) : undefined; const spanDuration = times?.start !== undefined && times.end !== undefined ? times.end - times.start : 0; const duration = typeof detail.duration_ms === "number" ? detail.duration_ms : typeof detail.latency_ms === "number" ? detail.latency_ms : spanDuration; return <div className={`trace-row ${isDenied || event.type === "policy_violation" ? "denied-row" : ""}`} key={`${event.seq}-${index}`}><div className="trace-kind">{event.type}{isDenied && <b>DENIED</b>}{isRedaction && <b>REDACTED</b>}</div><div className="trace-main" style={{ paddingLeft: `${16 + depth(event) * 20}px` }}><div className="trace-line"><code>{event.action ?? event.span_id ?? event.type}</code><span>{event.ts ? new Date(event.ts).toLocaleTimeString() : ""}</span><span>{duration ? `${duration} ms` : ""}</span></div><div className="duration-track"><i style={{ width: `${Math.max(2, Math.min(100, duration / 10))}%` }} /></div><pre>{JSON.stringify(detail, null, 2)}</pre></div></div>; })}</section>}
  </main></div>;
}
