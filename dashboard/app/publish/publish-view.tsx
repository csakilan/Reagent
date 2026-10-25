"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { call, continuePublish, dryRunCase, listAgents, publishRun, reviewCase, startPublish, type AgentSummary, type EvalReport, type PublishRun, type Rejection } from "../../lib/api";
import { RECORDED_PUBLISH_RUN_ID, recordedAgents, recordedPublishRun } from "../../lib/fixtures";
import { useDemoUser, useLive } from "../../lib/live";
import { Failure, Icon, KeyValues, PageHead, Payload, Rejections, Skeleton, SourceTag, Verdict, pct, usd, when } from "../ui";

type Case = NonNullable<PublishRun["cases"]>[number] & { case_hash?: string; approved_hash?: string };
type CaseBody = { input?: unknown; mocks?: unknown; expect?: Record<string, unknown> } & Record<string, unknown>;
type DryRun = { output?: Record<string, unknown>; tool_calls?: string[]; passed?: boolean; cost_usd?: number };
type Problem = { detail: string; rejections: Rejection[] } | null;

const STAGES = [
  { key: "validate", label: "Validate" },
  { key: "security", label: "Security review" },
  { key: "generate", label: "Generate tests" },
  { key: "review", label: "Human review" },
  { key: "evals", label: "Run evals" },
  { key: "gate", label: "Gate" },
  { key: "publish", label: "Publish" },
] as const;

function Pipeline({ run }: { run: PublishRun | null }) {
  const current = run ? STAGES.findIndex(stage => stage.key === run.stage) : -1;
  const results = (run?.stage_results ?? {}) as Record<string, { passed?: boolean; at?: string }>;
  return <ol className="pipeline" aria-label="Seven stage publish pipeline">
    {STAGES.map((stage, index) => {
      if (!run) return <li key={stage.key} className={`stage stage-idle ${stage.key === "review" ? "stage-gate" : ""}`}>
        {stage.key === "review" && <span className="gate-mark">Human stop</span>}
        <span className="stage-index">{String(index + 1).padStart(2, "0")}</span><b>{stage.label}</b>
      </li>;
      const done = run.status === "succeeded" || index < current || (index === current && results[stage.key]?.passed);
      const failed = index === current && (run.status === "failed" || run.status === "abandoned");
      const active = index === current && run.status === "running" && !done;
      const state = done ? "done" : failed ? "failed" : active ? "active" : "idle";
      return <li key={stage.key} className={`stage stage-${state} ${stage.key === "review" ? "stage-gate" : ""}`} aria-current={active ? "step" : undefined}>
        {stage.key === "review" && <span className="gate-mark">Human stop</span>}
        <span className="stage-index">{String(index + 1).padStart(2, "0")}</span>
        <b>{stage.label}</b>
        <span className="stage-state">
          {state === "done" && <Verdict kind="pass">passed</Verdict>}
          {state === "failed" && <Verdict kind="fail">{run.status}</Verdict>}
          {state === "active" && <Verdict kind="pending">{stage.key === "review" ? "waiting on a reviewer" : "running"}</Verdict>}
          {state === "idle" && <span className="muted">not started</span>}
        </span>
        {results[stage.key]?.at && <small>{when(results[stage.key].at)}</small>}
      </li>;
    })}
  </ol>;
}

function checksOf(body: CaseBody | null) {
  if (!body?.expect) return {};
  const rest = { ...body.expect };
  delete rest.judge; delete rest.reference;
  return rest;
}

function CaseRow({ runId, item, onChange }: { runId: number; item: Case; onChange: () => void }) {
  const body = item.case as CaseBody | null;
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [draft, setDraft] = useState(() => JSON.stringify(body ?? {}, null, 2));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const [dry, setDry] = useState<DryRun | null>(null);
  const caseId = item.case_id ?? "";

  async function act(promise: Promise<{ data?: unknown; error?: unknown; response: Response }>, after?: (data: unknown) => void) {
    setBusy(true); setProblem(null);
    const outcome = await call<unknown>(promise);
    setBusy(false);
    if (!outcome.ok) { setProblem({ detail: outcome.detail, rejections: outcome.rejections }); return; }
    after?.(outcome.data);
  }
  const review = (decision: "approve" | "edit" | "reject", extra?: { reason?: string; edited?: Record<string, unknown> }) =>
    act(reviewCase(runId, caseId, decision, extra?.reason, extra?.edited), () => { setMode("view"); onChange(); });
  function saveEdit() {
    try { review("edit", { edited: JSON.parse(draft) }); } catch { setProblem({ detail: "The edited case is not valid JSON.", rejections: [] }); }
  }

  const decision = item.decision;
  return <article className={`case-row ${decision === "rejected" ? "is-rejected" : ""}`}>
    <div className="case-id">
      <code>{caseId}</code>
      <div className="tags"><span className="tag">{item.origin}</span><span className="tag">{item.kind}</span></div>
      {decision === "approved" || decision === "edited" ? <Verdict kind="pass">{decision}{item.reviewed_by ? ` · ${item.reviewed_by}` : item.origin === "inherited" ? " · inherited" : ""}</Verdict>
        : decision === "rejected" ? <Verdict kind="fail">rejected{item.reviewed_by ? ` · ${item.reviewed_by}` : ""}</Verdict>
        : <Verdict kind="pending">needs review</Verdict>}
    </div>
    {body ? <>
      <div className="case-cell"><h4>Input</h4><Payload value={body.input} compact /><h4>Mocked sandbox</h4><Payload value={body.mocks} compact /></div>
      <div className="case-cell">
        <h4>Code checks</h4><Payload value={checksOf(body)} compact />
        <h4>Judge rubric</h4><p>{String(body.expect?.judge ?? "No judge question")}</p>
        <h4>Answer key</h4><p>{String(body.expect?.reference ?? "No reference answer")}</p>
      </div>
    </> : <div className="case-cell case-span"><p className="muted">Inherited from {caseId.split("/")[0]}. Members see the case name and its result only; the server already approved it.</p></div>}
    <div className="case-actions">
      {body && <>
        <button className="button" type="button" disabled={busy} onClick={() => act(dryRunCase(runId, caseId), data => setDry(data as DryRun))}>Dry run</button>
        <button className="button" type="button" disabled={busy || decision === "approved"} onClick={() => review("approve")}>Approve</button>
        <button className="button" type="button" disabled={busy} onClick={() => setMode(mode === "edit" ? "view" : "edit")}>Edit</button>
        <button className="button button-denied" type="button" disabled={busy} onClick={() => setMode(mode === "reject" ? "view" : "reject")}>Reject</button>
      </>}
    </div>
    {(mode !== "view" || dry || problem) && <div className="case-extra">
      {mode === "edit" && <div className="case-form">
        {(decision === "approved" || decision === "edited") && <p className="hint"><Icon name="pending" /> Saving an edit voids the existing approval. The server re-hashes the case; it must be approved again.</p>}
        <textarea className="code-input" rows={12} value={draft} onChange={event => setDraft(event.target.value)} spellCheck={false} aria-label={`Edit ${caseId}`} />
        <div className="toolbar"><button className="button primary" type="button" disabled={busy} onClick={saveEdit}>Save edit</button><button className="button" type="button" onClick={() => setMode("view")}>Cancel</button></div>
      </div>}
      {mode === "reject" && <form className="case-form" onSubmit={event => { event.preventDefault(); review("reject", { reason }); }}>
        <label className="field"><span>Reason · fed back to the evaluator so it is not proposed again</span><input className="input" required value={reason} onChange={event => setReason(event.target.value)} /></label>
        <div className="toolbar"><button className="button button-denied" type="submit" disabled={busy || !reason.trim()}>Reject case</button><button className="button" type="button" onClick={() => setMode("view")}>Cancel</button></div>
      </form>}
      {dry && <div className="dry-run">
        <div className="dry-run-head"><b>Dry run</b>{dry.passed === undefined ? null : <Verdict kind={dry.passed ? "pass" : "fail"}>{dry.passed ? "passed" : "failed"}</Verdict>}<span>{usd(dry.cost_usd)}</span>{dry.tool_calls?.length ? <span>tools <code>{dry.tool_calls.join(", ")}</code></span> : null}<button className="text-button" type="button" onClick={() => setDry(null)}>Close</button></div>
        <Payload value={dry.output} />
      </div>}
      {problem && (problem.rejections.length ? <Rejections items={problem.rejections} /> : <div className="notice notice-denied">{problem.detail}</div>)}
    </div>}
  </article>;
}

function Report({ report }: { report: EvalReport }) {
  return <section className="panel">
    <h2>Eval report {report.gate_blocked ? <Verdict kind="fail">gate blocked</Verdict> : <Verdict kind="pass">gate clear</Verdict>}</h2>
    <KeyValues rows={[
      ["Pass rate", `${pct(report.pass_rate)} (95% CI ${pct(report.pass_rate_ci_low)} to ${pct(report.pass_rate_ci_high)})`],
      ["pass^k", pct(report.pass_k)], ["Safety", pct(report.safety_pass_rate)], ["Tool-call accuracy", pct(report.tool_call_accuracy)],
      ["Average cost", usd(report.avg_cost)], ["p95 latency", report.p95_latency ? `${report.p95_latency} ms` : undefined],
      ["Judge", report.judge_calibrated ? "calibrated" : "uncalibrated · cannot gate"],
    ]} />
    {report.cases?.length ? <div className="table compact-table">
      <div className="table-head"><span>Case</span><span>Kind</span><span>Passes</span><span>Result</span></div>
      {report.cases.map(item => <div className="table-row" key={item.case_id}><code>{item.case_id}</code><span>{item.kind}</span><span>{item.passes ?? 0} / {item.runs ?? 0}</span><span>{item.passed ? <Verdict kind="pass">pass</Verdict> : <Verdict kind="fail">{item.error ?? "fail"}</Verdict>}</span></div>)}
    </div> : null}
  </section>;
}

function RunReview({ runId }: { runId: number }) {
  const user = useDemoUser();
  const [state, reload] = useLive<PublishRun>(user && `publish:${user}:${runId}`, () => publishRun(runId), { fallback: () => recordedPublishRun(runId) });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const run = state.phase === "ready" ? state.data : null;
  const cases = (run?.cases ?? []) as Case[];
  const reviewed = cases.filter(item => item.decision).length;
  const authoredPending = cases.filter(item => item.origin === "authored" && !item.decision && item.case);

  async function bulkApprove() {
    setBusy(true); setProblem(null);
    for (const item of authoredPending) {
      const outcome = await call(reviewCase(runId, item.case_id ?? "", "approve"));
      if (!outcome.ok) { setProblem({ detail: outcome.detail, rejections: outcome.rejections }); break; }
    }
    setBusy(false); reload();
  }
  async function proceed() {
    setBusy(true); setProblem(null);
    const outcome = await call(continuePublish(runId));
    setBusy(false);
    if (!outcome.ok) setProblem({ detail: outcome.detail, rejections: outcome.rejections });
    reload();
  }

  return <>
    <PageHead crumb="Agents / Publish pipeline" title={<span className="mono-title">{run ? `${run.agent} ${run.draft_version ?? ""}` : `pipeline ${runId}`}</span>}
      subtitle={run ? <>Pipeline {run.id} · started by {run.started_by ?? "—"} {when(run.started_at)} · status <b>{run.status}</b>, stage <b>{run.stage}</b></> : "Seven stages with a human gate"}>
      <SourceTag state={state} />
      <button className="button" type="button" onClick={reload}>Refresh</button>
      <Link className="button" href="/publish">New pipeline</Link>
    </PageHead>
    {state.phase === "loading" && <Skeleton rows={8} columns={4} label="Loading pipeline" />}
    <Failure state={state} notFound={<>Pipeline {runId} does not exist or is not visible to this identity.</>} />
    {run && <>
      <Pipeline run={run} />
      <div className="section-head">
        <div><h2>Case review</h2><p className="subtitle">{reviewed} of {cases.length} reviewed. Nothing publishes with unreviewed tests.</p></div>
        <div className="toolbar">
          <button className="button" type="button" disabled={busy || !authoredPending.length} onClick={bulkApprove}>Approve {authoredPending.length || ""} hand-written</button>
          <button className="button primary" type="button" disabled={busy || run.status !== "running"} onClick={proceed}>Continue to evals</button>
        </div>
      </div>
      {problem && (problem.rejections.length ? <Rejections items={problem.rejections} title="Pipeline refused" /> : <div className="notice notice-denied">{problem.detail}</div>)}
      <section className="table case-table" aria-label="Cases">
        <div className="table-head"><span>Case</span><span>Input · sandbox</span><span>Checks · rubric · answer key</span><span>Review</span></div>
        {cases.map(item => <CaseRow key={`${item.case_id}-${item.case_hash ?? ""}-${item.decision ?? ""}`} runId={runId} item={item} onChange={reload} />)}
        {!cases.length && <div className="empty">This pipeline has not generated any cases yet.</div>}
      </section>
      {run.report && <Report report={run.report} />}
    </>}
  </>;
}

function StartForm() {
  const router = useRouter();
  const user = useDemoUser();
  const [agents] = useLive<{ agents: AgentSummary[] }>(user && `agents:${user}`, () => listAgents(), { fallback: recordedAgents });
  const [agent, setAgent] = useState("pr-reviewer");
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const names = agents.phase === "ready" ? agents.data.agents.map(item => item.name) : [];

  async function start() {
    setBusy(true); setProblem(null);
    const outcome = await call<{ publish_run_id: number }>(startPublish(agent));
    setBusy(false);
    if (outcome.ok) router.push(`/publish?run=${outcome.data.publish_run_id}`);
    else setProblem({ detail: outcome.detail, rejections: outcome.rejections });
  }

  return <div className="split">
    <section className="panel">
      <h2>Start a pipeline</h2>
      <p className="panel-note">Validate, security review and test generation run first. Then the pipeline stops until a person has reviewed every case.</p>
      <label className="field"><span>Agent</span><select className="select" value={agent} onChange={event => setAgent(event.target.value)}>{[...new Set([agent, ...names])].map(name => <option key={name}>{name}</option>)}</select></label>
      <button className="button primary" type="button" disabled={busy} onClick={start}>{busy ? "Starting…" : "Start publish pipeline"}</button>
      {problem && (problem.rejections.length ? <Rejections items={problem.rejections} title="Pipeline refused" /> : <div className="notice notice-denied">{problem.detail}</div>)}
    </section>
    <section className="panel">
      <h2>Open a pipeline</h2>
      <p className="panel-note">At most one pipeline runs per agent at a time.</p>
      <form className="inline-form" onSubmit={event => { event.preventDefault(); if (runId) router.push(`/publish?run=${runId}`); }}>
        <input className="input" inputMode="numeric" placeholder="Pipeline id" value={runId} onChange={event => setRunId(event.target.value.replace(/\D/g, ""))} aria-label="Pipeline id" />
        <button className="button" type="submit">Open</button>
      </form>
      <Link className="text-link" href={`/publish?run=${RECORDED_PUBLISH_RUN_ID}`}>Open pipeline {RECORDED_PUBLISH_RUN_ID}</Link>
    </section>
  </div>;
}

export default function PublishView() {
  const params = useSearchParams();
  const run = Number(params.get("run"));
  if (Number.isInteger(run) && run > 0) return <RunReview key={run} runId={run} />;
  return <>
    <PageHead crumb="Agents / Publish pipeline" title="Publish pipeline" subtitle="Validate → security review → generate tests → human review → evals → gate → publish" />
    <Pipeline run={null} />
    <StartForm />
  </>;
}
