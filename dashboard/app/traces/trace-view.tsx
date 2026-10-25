"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { agentDetail, call, listAgents, runResult, runTriage, startRun, traceForRun, type Triage, type AgentSummary, type Rejection, type RunEvent, type RunResult, type Trace } from "../../lib/api";
import { recordedAgents, recordedRun, recordedTrace } from "../../lib/fixtures";
import { useDemoUser, useLive } from "../../lib/live";
import { Failure, Icon, KeyValues, PageHead, Payload, Rejections, Skeleton, SourceTag, Verdict, usd } from "../ui";

type Detail = Record<string, unknown>;
type Row = { event: RunEvent; runId: number; depth: number; start: number; duration: number };

const payloadOf = (event: RunEvent) => (event.payload_redacted ?? {}) as Detail;
const num = (value: unknown) => typeof value === "number" ? value : 0;
const DEFAULT_INPUT = { "pr-reviewer": { repo: "csakilan/reagent-demo", pr_number: 1 } } as Record<string, Record<string, unknown>>;

/** The agent's declared `input` schema, rendered as real fields (GET /v0/agents/{name}). */
type Prop = { type?: string; enum?: unknown[]; description?: string };
type InputSchema = { properties?: Record<string, Prop>; required?: string[] } | null;

function InputFields({ schema, values, onChange }: { schema: InputSchema; values: Record<string, string>; onChange: (key: string, value: string) => void }) {
  const props = Object.entries(schema?.properties ?? {});
  if (!props.length) return <p className="muted">This agent declares no input. It runs on its trigger alone.</p>;
  return <div className="input-fields">{props.map(([key, prop]) => {
    const required = schema?.required?.includes(key);
    const label = <span>{key}{required ? " *" : ""}{prop.description ? <small className="muted"> {prop.description}</small> : null}</span>;
    if (prop.enum) return <label className="field" key={key}>{label}
      <select className="select" value={values[key] ?? ""} onChange={event => onChange(key, event.target.value)}>
        <option value="">Choose…</option>{prop.enum.map(v => <option key={String(v)} value={String(v)}>{String(v)}</option>)}</select></label>;
    if (prop.type === "boolean") return <label className="field-inline" key={key}>
      <input type="checkbox" checked={values[key] === "true"} onChange={event => onChange(key, event.target.checked ? "true" : "false")} />{label}</label>;
    const numeric = prop.type === "integer" || prop.type === "number";
    return <label className="field" key={key}>{label}
      <input className={numeric ? "input mono" : "input"} inputMode={numeric ? "numeric" : undefined} value={values[key] ?? ""}
        onChange={event => onChange(key, event.target.value)} required={required} /></label>;
  })}</div>;
}

/** Field strings to typed input, by the schema. Empty optional fields are left out. */
function typed(schema: InputSchema, values: Record<string, string>): { input?: Record<string, unknown>; error?: string } {
  const out: Record<string, unknown> = {};
  for (const [key, prop] of Object.entries(schema?.properties ?? {})) {
    const raw = (values[key] ?? "").trim();
    if (!raw) { if (schema?.required?.includes(key)) return { error: `${key} is required.` }; continue; }
    if (prop.type === "integer" || prop.type === "number") {
      const n = Number(raw);
      if (!Number.isFinite(n) || (prop.type === "integer" && !Number.isInteger(n))) return { error: `${key} must be ${prop.type === "integer" ? "a whole number" : "a number"}.` };
      out[key] = n;
    } else if (prop.type === "boolean") out[key] = raw === "true";
    else out[key] = raw;
  }
  return { input: out };
}


/** Runs do not store their input, so the dashboard keeps what it sent (per tab). */
const RUN_INPUT_KEY = (id: number) => `reagent-run-input:${id}`;
type RunContext = { agent?: string; version?: string; trigger?: string; subject?: string; via?: string; input?: unknown; inputFrom?: "sent" | "derived" };

/** What the run was about: who ran, how it was triggered, and the PR or channel it worked on. */
function contextOf(trace: Trace, runId: number): RunContext {
  const events = trace.events.slice().sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  const root = events.find(event => event.type === "span_open");
  const head = root ? payloadOf(root) : {};
  const out: RunContext = { agent: head.agent as string | undefined, version: head.version as string | undefined, trigger: head.trigger as string | undefined };
  try {
    const sent = typeof window !== "undefined" ? window.sessionStorage.getItem(RUN_INPUT_KEY(runId)) : null;
    if (sent) { out.input = JSON.parse(sent); out.inputFrom = "sent"; }
  } catch { /* storage blocked: derive from the trace instead */ }
  for (const event of events) {
    if (event.type !== "tool_call" || event.action?.startsWith("agent:")) continue;
    const args = (payloadOf(event).args ?? {}) as Detail;
    const pr = args.pr_number ?? args.number;
    if (typeof args.repo === "string" && pr !== undefined) { out.subject = `${args.repo} #${pr}`; out.via = event.action ?? undefined; }
    else if (typeof args.channel === "string") { out.subject = args.channel; out.via = event.action ?? undefined; }
    if (out.subject) { if (out.input === undefined) { out.input = args; out.inputFrom = "derived"; } break; }
  }
  return out;
}

/** failure-triage, triggered by opening a failed or denied run: cause, evidence, fix. */
function WhyItFailed({ runId, user }: { runId: number; user: string | null }) {
  const [state] = useLive<Triage>(user ? `triage:${user}:${runId}` : null, () => runTriage(runId));
  if (state.phase !== "ready" || !state.data.failed) return null;
  const t = state.data;
  return <section className="run-why" aria-label="Why this run failed">
    <h2>Why this run failed <span className="muted">· failure-triage</span></h2>
    <p><b>{t.cause}</b>{t.code ? <> (<code>{t.code}</code>)</> : null}: {t.explanation}</p>
    {t.evidence && t.evidence.length > 0 && <ul>{t.evidence.map((e, i) => <li key={i}><code>{e}</code></li>)}</ul>}
    <p><b>What to change:</b> {t.fix}</p>
  </section>;
}

function ContextBar({ context }: { context: RunContext }) {
  if (!context.agent && !context.subject) return null;
  return <section className="run-context" aria-label="What this run was about">
    <p>{context.agent && <><code>{context.agent}</code>{context.version ? ` ${context.version}` : ""}</>}
      {context.trigger && <span className="muted"> · triggered by {context.trigger}</span>}
      {context.subject ? <> · working on <b className="mono">{context.subject}</b></> : <span className="muted"> · no repository or channel in this run</span>}</p>
    {context.input !== undefined && <details>
      <summary>{context.inputFrom === "sent" ? "Input sent with this run" : `Arguments of the first ${context.via ?? "tool"} call`}</summary>
      <Payload value={context.input} compact />
    </details>}
  </section>;
}

/** Flattens a run and its sub-agent runs into waterfall rows, timed from each call's own latency. */
function buildRows(root: Trace, children: Map<number, Trace>): { rows: Row[]; total: number } {
  const rows: Row[] = [];
  let clock = 0;
  const walk = (trace: Trace, base: number, seen: Set<number>) => {
    const events = trace.events.slice().sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
    const open: { id: string; row: Row }[] = [];
    let callEnd = 0; // a sub-agent call spans its child run, so the child is drawn inside it
    for (const event of events) {
      const detail = payloadOf(event);
      if (event.type === "span_close") {
        const childId = num(detail.child_run_id);
        const child = childId ? children.get(childId) : undefined;
        if (child && !seen.has(childId)) walk(child, base + open.length, new Set([...seen, childId]));
        clock = Math.max(clock, callEnd);
        const index = open.findIndex(item => item.id === event.span_id);
        const opened = index >= 0 ? open.splice(index)[0] : undefined;
        if (opened) opened.row.duration = clock - opened.row.start;
        rows.push({ event, runId: trace.run_id, depth: base + open.length, start: clock, duration: 0 });
        continue;
      }
      const row: Row = { event, runId: trace.run_id, depth: base + open.length, start: clock, duration: num(detail.latency_ms) || num(detail.duration_ms) };
      rows.push(row);
      if (event.type === "span_open" && event.span_id) open.push({ id: event.span_id, row });
      else if (event.type === "tool_call" && event.action?.startsWith("agent:") && open.length) callEnd = clock + row.duration;
      else clock += row.duration;
    }
  };
  walk(root, 0, new Set([root.run_id]));
  return { rows, total: clock };
}

function summary(event: RunEvent): string {
  const d = payloadOf(event);
  switch (event.type) {
    case "span_open": return [d.agent ?? d.name, d.version && `@${d.version}`, d.trigger && ` · ${d.trigger}`].filter(Boolean).join("");
    case "span_close": return [d.status, d.cost_usd !== undefined && usd(num(d.cost_usd)), d.child_run_id && `run ${d.child_run_id}`].filter(Boolean).join(" · ");
    case "model_call": return `${d.model ?? "model"} · ${num(d.input_tokens)} in / ${num(d.output_tokens)} out${d.fallback_used ? " · fallback" : ""}`;
    case "tool_call": return [d.resource, d.denial_code, d.error].filter(Boolean).join(" · ");
    case "redaction_hit": return `${d.pattern} in ${d.location} × ${d.count ?? 1}`;
    case "approval_requested": return `${d.risk ?? ""} risk · approval ${d.approval_id ?? ""}`;
    case "approval_decided": return `${d.decision ?? ""}${d.approver ? ` by ${d.approver}` : ""}`;
    default: return String(d.code ?? d.message ?? d.error ?? "");
  }
}

function RunForm({ initialAgent }: { initialAgent: string }) {
  const router = useRouter();
  const user = useDemoUser();
  const [agents] = useLive<{ agents: AgentSummary[] }>(user && `agents:${user}`, () => listAgents(), { fallback: recordedAgents });
  const [agent, setAgent] = useState(initialAgent);
  const [detail] = useLive<{ input_schema?: InputSchema }>(user && agent ? `detail:${user}:${agent}` : null, () => agentDetail(agent));
  const schema = detail.phase === "ready" ? detail.data.input_schema ?? null : null;
  const [values, setValues] = useState<Record<string, string>>({});
  // Prefill from the demo defaults when the agent changes.
  useEffect(() => {
    const d = DEFAULT_INPUT[agent] ?? {};
    setValues(Object.fromEntries(Object.entries(d).map(([k, v]) => [k, String(v)])));
  }, [agent]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ detail: string; rejections: Rejection[] } | null>(null);
  const [runId, setRunId] = useState("");

  async function submit() {
    const { input: parsed, error } = typed(schema, values);
    if (!parsed) { setProblem({ detail: error ?? "Check the input.", rejections: [] }); return; }
    setBusy(true); setProblem(null);
    const outcome = await call<RunResult>(startRun(agent, parsed));
    setBusy(false);
    if (outcome.ok) {
      try { window.sessionStorage.setItem(RUN_INPUT_KEY(outcome.data.run_id), JSON.stringify(parsed)); } catch { /* context falls back to the trace */ }
      router.push(`/traces?run=${outcome.data.run_id}`);
    } else setProblem({ detail: outcome.detail, rejections: outcome.rejections });
  }

  const names = agents.phase === "ready" ? agents.data.agents.map(item => item.name) : [];
  return <div className="split">
    <section className="panel">
      <h2>Start a run</h2>
      <p className="panel-note">Runs as <b>{user ?? "…"}</b>. The acting principal comes from the session, never from the body.</p>
      <label className="field"><span>Agent</span>
        <select className="select" value={agent} onChange={event => setAgent(event.target.value)}>
          {[...new Set([agent, ...names])].map(name => <option key={name}>{name}</option>)}
        </select>
      </label>
      {detail.phase === "loading" ? <Skeleton rows={2} columns={1} label="Loading this agent's input" />
        : <InputFields schema={schema} values={values} onChange={(key, value) => setValues(current => ({ ...current, [key]: value }))} />}
      <button className="button primary" type="button" disabled={busy} onClick={submit}>{busy ? "Running…" : "Run and open trace"}</button>
      {problem && (problem.rejections.length ? <Rejections items={problem.rejections} title="Run refused" /> : <div className="notice notice-denied">{problem.detail}</div>)}
    </section>
    <section className="panel">
      <h2>Open a run</h2>
      <p className="panel-note">Sub-agent runs are nested under their parent automatically.</p>
      <form className="inline-form" onSubmit={event => { event.preventDefault(); if (runId) router.push(`/traces?run=${runId}`); }}>
        <input className="input" inputMode="numeric" placeholder="Run id" value={runId} onChange={event => setRunId(event.target.value.replace(/\D/g, ""))} aria-label="Run id" />
        <button className="button" type="submit">Open</button>
      </form>
    </section>
  </div>;
}

function RunTrace({ runId }: { runId: number }) {
  const user = useDemoUser();
  const [run, reloadRun] = useLive<RunResult>(user && `run:${user}:${runId}`, () => runResult(runId), { fallback: () => recordedRun(runId) });
  const [trace, reloadTrace] = useLive<Trace>(user && `trace:${user}:${runId}`, () => traceForRun(runId), { fallback: () => recordedTrace(runId) });
  const [children, setChildren] = useState<Map<number, Trace>>(new Map());
  const [open, setOpen] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (trace.phase !== "ready") return;
    let cancelled = false;
    const fixture = trace.source === "fixture";
    const load = async (ids: number[], found: Map<number, Trace>, depth: number): Promise<void> => {
      if (!ids.length || depth > 3) return;
      const traces = await Promise.all(ids.map(async id => {
        if (fixture) return recordedTrace(id);
        const outcome = await call<Trace>(traceForRun(id));
        return outcome.ok ? outcome.data : undefined;
      }));
      const next: number[] = [];
      traces.forEach(item => { if (item && !found.has(item.run_id)) { found.set(item.run_id, item); next.push(...(item.children ?? [])); } });
      await load(next, found, depth + 1);
    };
    const found = new Map<number, Trace>();
    load(trace.data.children ?? [], found, 0).then(() => { if (!cancelled) setChildren(found); });
    return () => { cancelled = true; };
  }, [trace]);

  const built = useMemo(() => trace.phase === "ready" ? buildRows(trace.data, children) : { rows: [], total: 0 }, [trace, children]);
  const context = useMemo(() => trace.phase === "ready" ? contextOf(trace.data, runId) : {}, [trace, runId]);
  const events = built.rows.map(row => row.event);
  const tools = events.filter(event => event.type === "tool_call");
  const denied = tools.filter(event => event.allowed === false).length;
  const redactions = events.filter(event => event.type === "redaction_hit").reduce((sum, event) => sum + (num(payloadOf(event).count) || 1), 0);
  const result = run.phase === "ready" ? run.data : null;
  const statusKind = result?.status === "succeeded" ? "pass" : result && ["failed", "denied", "cancelled"].includes(result.status) ? "fail" : "pending";
  const toggle = (index: number) => setOpen(current => { const next = new Set(current); if (next.has(index)) next.delete(index); else next.add(index); return next; });

  return <>
    <PageHead crumb="Runs / Trace viewer" title={<span className="mono-title">run {runId}{context.agent ? ` · ${context.agent}` : ""}{context.subject ? ` on ${context.subject}` : ""}</span>} subtitle={result ? <>{result.model ? <>Model <code>{result.model}</code></> : "Redacted span waterfall"}{result.parent_run_id ? <> · child of <Link href={`/traces?run=${result.parent_run_id}`}>run {result.parent_run_id}</Link></> : null}</> : "Redacted span waterfall"}>
      <SourceTag state={trace} />
      <button className="button" type="button" onClick={() => { reloadRun(); reloadTrace(); }}>Refresh</button>
      <Link className="button" href="/traces">New run</Link>
    </PageHead>
    <ContextBar context={context} />
    {result && ["failed", "denied"].includes(result.status) && <WhyItFailed runId={runId} user={user} />}

    {run.phase === "loading" ? <Skeleton rows={1} columns={6} label="Loading run" /> : result && <div className="stat-row">
      <div><span>Status</span><Verdict kind={statusKind}>{result.status.replace("_", " ")}</Verdict></div>
      <div><span>Cost</span><b>{usd(result.cost_usd)}</b></div>
      <div><span>Tokens</span><b>{result.input_tokens ?? 0} / {result.output_tokens ?? 0}</b></div>
      <div><span>Steps</span><b>{result.steps ?? "—"}</b></div>
      <div><span>Tool calls</span><b>{tools.length}{denied ? <em className="denied-text"> · {denied} denied</em> : null}</b></div>
      <div><span>Redactions</span><b>{redactions}</b></div>
    </div>}
    {result?.status === "awaiting_approval" && <div className="notice"><span>This run is paused for a human decision. <Link href="/approvals">Open the approvals queue</Link>.</span></div>}
    {result?.error && <div className="notice notice-denied"><b>Error</b> {result.error}</div>}
    {result && <Rejections items={result.rejections ?? []} title="Run refused" />}

    {trace.phase === "loading" && <Skeleton rows={10} columns={5} label="Loading trace" />}
    <Failure state={trace} notFound={<>Run {runId} does not exist or is not visible to this identity.</>} />
    {trace.phase === "ready" && <section className="table waterfall" aria-label="Span waterfall">
      <div className="table-head"><span>#</span><span>Event</span><span>Span · action</span><span>Timeline</span><span>Time</span></div>
      {built.rows.map((row, index) => {
        const { event } = row;
        const detail = payloadOf(event);
        const isDenied = event.type === "tool_call" && event.allowed === false;
        const isViolation = event.type === "policy_violation" || event.type === "error";
        const isOpen = open.has(index);
        const name = event.action ?? (event.type === "span_open" || event.type === "span_close" ? String(detail.agent ?? detail.name ?? "span") : event.type.replace("_", " "));
        const left = built.total ? (row.start / built.total) * 100 : 0;
        const width = built.total ? Math.max(0.6, (row.duration / built.total) * 100) : 0;
        return <Fragment key={`${row.runId}-${event.seq}-${index}`}>
          <button type="button" className={`trace-row type-${event.type} ${isDenied || isViolation ? "is-denied" : ""} ${isOpen ? "is-open" : ""}`} aria-expanded={isOpen} onClick={() => toggle(index)}>
            <span className="trace-seq">{row.runId !== runId ? `${row.runId}·` : ""}{event.seq}</span>
            <span className="trace-type">{event.type.replace("_", " ")}</span>
            <span className="trace-name" style={{ paddingLeft: `${row.depth * 18}px` }}>
              {row.depth > 0 && <i className="tree-mark" aria-hidden="true" />}
              <code>{name}</code>
              {isDenied && <Verdict kind="fail">denied</Verdict>}
              {event.type === "tool_call" && event.allowed === true && <Verdict kind="pass">allowed</Verdict>}
              {event.type === "redaction_hit" && <mark className="redacted">REDACTED</mark>}
              <small>{summary(event)}</small>
            </span>
            <span className="trace-bar">{(row.duration > 0 || event.type === "span_open") && <i className={`bar ${event.type === "span_open" ? "bar-span" : ""} ${isDenied ? "bar-denied" : ""}`} style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }} />}</span>
            <span className="trace-time">{row.duration ? `${row.duration} ms` : ""}<Icon name={isOpen ? "collapse" : "expand"} /></span>
          </button>
          {isOpen && <div className="trace-detail">
            <KeyValues rows={[["Run", row.runId], ["Span", event.span_id ?? undefined], ["Parent span", event.parent_span_id ?? undefined], ["Allowed", event.allowed === null || event.allowed === undefined ? undefined : String(event.allowed)]]} />
            <Payload value={detail} />
          </div>}
        </Fragment>;
      })}
      {!built.rows.length && <div className="empty">This run recorded no events.</div>}
    </section>}
  </>;
}

export default function TraceView() {
  const params = useSearchParams();
  const run = Number(params.get("run"));
  if (Number.isInteger(run) && run > 0) return <RunTrace key={run} runId={run} />;
  return <>
    <PageHead crumb="Runs / Trace viewer" title="Trace viewer" subtitle="Start a run or open one by id. Payloads are redacted before they are stored." />
    <RunForm initialAgent={params.get("agent") ?? "pr-reviewer"} />
  </>;
}
