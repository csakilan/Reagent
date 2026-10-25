"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { abandonPublish, call, continuePublish, dryRunCase, listAgents, publishRun, reviewCase, startPublish, type AgentSummary, type EvalReport, type PublishRun, type Rejection } from "../../lib/api";
import { recordedAgents, recordedPublishRun } from "../../lib/fixtures";
import { useDemoUser, useLive } from "../../lib/live";
import { Failure, Icon, PageHead, Payload, Rejections, Skeleton, SourceTag, Verdict, pct, usd, when } from "../ui";

type Case = NonNullable<PublishRun["cases"]>[number] & { case_hash?: string; approved_hash?: string };
type CaseBody = { input?: unknown; mocks?: unknown; expect?: Record<string, unknown> } & Record<string, unknown>;
type DryRun = { output?: Record<string, unknown>; tool_calls?: string[]; passed?: boolean; cost_usd?: number };
type Problem = { detail: string; rejections: (Rejection & { publish_run_id?: number; stage?: string })[] } | null;

/** The server's GitHub-Actions-style step list (runtime/publish/pipeline.py `steps`). */
type Step = { stage: string; title: string; what: string; summary: string;
  status: "passed" | "failed" | "running" | "waiting" | "pending" | "abandoned" };
type Results = Record<string, { passed?: boolean; at?: string; rejections?: Rejection[];
  findings?: { blocking?: { message: string }[]; advisory?: { message: string }[] };
  proposed?: string[]; reasons?: string[]; version?: string }>;
type ReportCase = NonNullable<EvalReport["cases"]>[number] & {
  origin?: string; gating?: boolean; input?: unknown; output?: string; reasons?: string[];
  tool_calls?: string[]; blocked_calls?: string[]; inert_mocks?: string[] };
type Run = PublishRun & { steps?: Step[]; report?: EvalReport & { runs_per_case?: number; gate_reasons?: string[] } };

const FALLBACK_STEPS: Step[] = [
  ["validate", "Validate"], ["security", "Security review"], ["generate", "Generate tests"], ["review", "Human review"],
  ["evals", "Run evals"], ["gate", "Gate"], ["publish", "Publish"],
].map(([stage, title]) => ({ stage, title, what: "", summary: "", status: "pending" }));

function StepStatus({ status }: { status: Step["status"] }) {
  if (status === "passed") return <Verdict kind="pass">passed</Verdict>;
  if (status === "failed") return <Verdict kind="fail">failed</Verdict>;
  if (status === "abandoned") return <Verdict kind="fail">abandoned</Verdict>;
  if (status === "waiting") return <Verdict kind="pending">waiting for you</Verdict>;
  if (status === "running") return <Verdict kind="pending">running</Verdict>;
  return <span className="muted">not started</span>;
}

function checksOf(body: CaseBody | null) {
  if (!body?.expect) return {};
  const rest = { ...body.expect };
  delete rest.judge; delete rest.reference;
  return rest;
}

/** One case awaiting review: a single line, expanding to everything a reviewer needs. */
function ReviewCase({ runId, item, onChange }: { runId: number; item: Case; onChange: () => void }) {
  const body = item.case as CaseBody | null;
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [draft, setDraft] = useState(() => JSON.stringify(body ?? {}, null, 2));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const [dry, setDry] = useState<DryRun | null>(null);
  const caseId = item.case_id ?? "";
  const decision = item.decision;

  async function act(promise: Promise<{ data?: unknown; error?: unknown; response: Response }>, after?: (data: unknown) => void) {
    setBusy(true); setProblem(null);
    const outcome = await call<unknown>(promise);
    setBusy(false);
    if (!outcome.ok) { setProblem({ detail: outcome.detail, rejections: outcome.rejections }); return; }
    after?.(outcome.data);
  }
  const review = (d: "approve" | "edit" | "reject", extra?: { reason?: string; edited?: Record<string, unknown> }) =>
    act(reviewCase(runId, caseId, d, extra?.reason, extra?.edited), () => { setMode("view"); onChange(); });
  function saveEdit() {
    try { review("edit", { edited: JSON.parse(draft) }); } catch { setProblem({ detail: "The edited case is not valid JSON.", rejections: [] }); }
  }

  return <details className="ci-item" open={!decision && !!body}>
    <summary>
      {decision === "approved" || decision === "edited" ? <Verdict kind="pass">{decision}</Verdict>
        : decision === "rejected" ? <Verdict kind="fail">rejected</Verdict> : <Verdict kind="pending">needs review</Verdict>}
      <code>{caseId}</code><span className="tag">{item.kind}</span><span className="tag">{item.origin}</span>
      {item.reviewed_by && <span className="muted">by {item.reviewed_by}</span>}
    </summary>
    <div className="ci-detail">
      {body ? <div className="ci-grid">
        <div><h4>Input</h4><Payload value={body.input} compact /><h4>Mocked tools</h4><Payload value={body.mocks} compact /></div>
        <div><h4>Code checks</h4><Payload value={checksOf(body)} compact />
          {body.expect?.judge !== undefined && <><h4>Judge question</h4><p>{String(body.expect.judge)}</p></>}
          {body.expect?.reference !== undefined && <><h4>Answer key</h4><p>{String(body.expect.reference)}</p></>}</div>
      </div> : <p className="muted">Inherited from {caseId.split("/")[0]} and reviewed once by enterprise admins. Members see its name and result only.</p>}
      {body && <div className="toolbar">
        <button className="button" type="button" disabled={busy || decision === "approved"} onClick={() => review("approve")}>Approve</button>
        <button className="button" type="button" disabled={busy} onClick={() => setMode(mode === "edit" ? "view" : "edit")}>Edit</button>
        <button className="button button-denied" type="button" disabled={busy} onClick={() => setMode(mode === "reject" ? "view" : "reject")}>Reject</button>
        <button className="button" type="button" disabled={busy} onClick={() => act(dryRunCase(runId, caseId), data => setDry(data as DryRun))}>{busy ? "Running…" : "Dry run once"}</button>
      </div>}
      {mode === "edit" && <div className="case-form">
        {(decision === "approved" || decision === "edited") && <p className="hint"><Icon name="pending" /> Saving an edit voids the approval: the server re-hashes the case and it must be approved again.</p>}
        <textarea className="code-input" rows={12} value={draft} onChange={event => setDraft(event.target.value)} spellCheck={false} aria-label={`Edit ${caseId}`} />
        <div className="toolbar"><button className="button primary" type="button" disabled={busy} onClick={saveEdit}>Save edit</button><button className="button" type="button" onClick={() => setMode("view")}>Cancel</button></div>
      </div>}
      {mode === "reject" && <form className="case-form" onSubmit={event => { event.preventDefault(); review("reject", { reason }); }}>
        <label className="field"><span>Why? The reason is fed back so this case is not proposed again.</span><input className="input" required value={reason} onChange={event => setReason(event.target.value)} /></label>
        <div className="toolbar"><button className="button button-denied" type="submit" disabled={busy || !reason.trim()}>Reject case</button><button className="button" type="button" onClick={() => setMode("view")}>Cancel</button></div>
      </form>}
      {dry && <div className="dry-run">
        <div className="dry-run-head"><b>Dry run</b>{dry.passed !== undefined && <Verdict kind={dry.passed ? "pass" : "fail"}>{dry.passed ? "passed" : "failed"}</Verdict>}<span>{usd(dry.cost_usd)}</span>
          {dry.tool_calls?.length ? <span>called <code>{dry.tool_calls.join(" → ")}</code></span> : null}<button className="text-button" type="button" onClick={() => setDry(null)}>Close</button></div>
        <Payload value={dry.output} />
      </div>}
      {problem && (problem.rejections.length ? <Rejections items={problem.rejections} /> : <div className="notice notice-denied">{problem.detail}</div>)}
    </div>
  </details>;
}

/** One eval case result. Failures open by default and say why first. */
function ResultCase({ item }: { item: ReportCase }) {
  const failed = item.gating !== false && !item.passed;
  return <details className="ci-item" open={failed}>
    <summary>
      {item.gating === false ? <span className="muted">not gating</span> : item.passed ? <Verdict kind="pass">passed</Verdict> : <Verdict kind="fail">failed</Verdict>}
      <code>{item.case_id}</code><span className="tag">{item.kind}</span>
      <span className="muted">{item.passes ?? 0} of {item.runs ?? 0} run{item.runs === 1 ? "" : "s"}</span>
    </summary>
    <div className="ci-detail">
      {failed && (item.reasons?.length ? <div className="ci-why"><h4>Why it failed</h4><ul>{item.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul></div>
        : item.error ? <div className="ci-why"><h4>Why it failed</h4><p>{item.error}</p></div> : null)}
      {item.gating === false && <p className="muted">This case has only a judge question and the judge is not calibrated, so it is reported but does not count.</p>}
      <div className="ci-grid">
        <div><h4>Input</h4>{item.input === null || item.input === undefined ? <p className="muted">Hidden: inherited from the enterprise.</p> : <Payload value={item.input} compact />}
          <h4>Tools called</h4><p>{item.tool_calls?.length ? <code>{item.tool_calls.join(" → ")}</code> : <span className="muted">none</span>}</p>
          {item.blocked_calls && item.blocked_calls.length > 0 && <><h4>Blocked by the runtime</h4><p><code>{item.blocked_calls.join(", ")}</code></p></>}
          {item.inert_mocks && item.inert_mocks.length > 0 && <p className="muted">Answered with an empty result (inherited case): {item.inert_mocks.join(", ")}</p>}</div>
        <div><h4>Agent output</h4>{item.output ? <pre className="ci-output">{item.output}</pre> : <p className="muted">No output.</p>}</div>
      </div>
    </div>
  </details>;
}

function StepBody({ step, run, results, children }: { step: Step; run: Run; results: Results; children?: ReactNode }) {
  const res = results[step.stage] ?? {};
  return <div className="ci-detail">
    {step.what && <p className="muted ci-what">{step.what}</p>}
    {res.rejections && res.rejections.length > 0 && <Rejections items={res.rejections} title="Why this step failed" />}
    {step.stage === "security" && res.findings && <ul className="ci-list">
      {(res.findings.blocking ?? []).map((f, i) => <li key={`b${i}`}><Verdict kind="fail">blocking</Verdict> {f.message}</li>)}
      {(res.findings.advisory ?? []).map((f, i) => <li key={`a${i}`}><Verdict kind="pending">advisory</Verdict> {f.message}</li>)}
      {!(res.findings.blocking?.length || res.findings.advisory?.length) && <li className="muted">No findings.</li>}
    </ul>}
    {step.stage === "generate" && <p>{res.proposed?.length ? <>Proposed: {res.proposed.map(p => <code key={p} className="drawer-chip">{p}</code>)}</> : "No new cases needed: the existing ones cover every kind and every action."}</p>}
    {step.stage === "gate" && run.report?.gate_reasons && run.report.gate_reasons.length > 0 && <ul className="ci-list">{run.report.gate_reasons.map(r => <li key={r}>{r}</li>)}</ul>}
    {children}
  </div>;
}

function RunReview({ runId }: { runId: number }) {
  const user = useDemoUser();
  const router = useRouter();
  const [state, reload] = useLive<Run>(user && `publish:${user}:${runId}`, () => publishRun(runId), { fallback: () => recordedPublishRun(runId) as Run | undefined });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const run = state.phase === "ready" ? state.data : null;
  const cases = (run?.cases ?? []) as Case[];
  const open = cases.filter(item => !item.decision);
  const authoredPending = cases.filter(item => item.origin === "authored" && !item.decision && item.case);
  const steps = run?.steps ?? FALLBACK_STEPS;
  const results = (run?.stage_results ?? {}) as Results;
  const waiting = run?.status === "running" && run.stage === "review";

  async function run_(promise: Promise<{ data?: unknown; error?: unknown; response: Response }>) {
    setBusy(true); setProblem(null);
    const outcome = await call(promise);
    setBusy(false);
    if (!outcome.ok) setProblem({ detail: outcome.detail, rejections: outcome.rejections });
    reload();
  }
  async function bulkApprove() {
    setBusy(true); setProblem(null);
    for (const item of authoredPending) {
      const outcome = await call(reviewCase(runId, item.case_id ?? "", "approve"));
      if (!outcome.ok) { setProblem({ detail: outcome.detail, rejections: outcome.rejections }); break; }
    }
    setBusy(false); reload();
  }

  return <>
    <PageHead crumb="Agents / Publish pipeline" title={<span className="mono-title">{run ? `${run.agent} ${run.draft_version ?? ""}` : `pipeline ${runId}`}</span>}
      subtitle={run ? <>Pipeline {run.id} · started by {run.started_by ?? "—"} {when(run.started_at)}</> : "Seven stages with one human gate"}>
      <SourceTag state={state} />
      {run && <span className={`ci-status ci-${run.status}`}>{run.status}</span>}
      <button className="button" type="button" onClick={reload}>Refresh</button>
      {run?.status === "running" && <button className="button button-denied" type="button" disabled={busy}
        onClick={() => run_(abandonPublish(runId))}>Abandon</button>}
      {run && run.status !== "running" && <button className="button" type="button" onClick={() => router.push("/publish")}>New pipeline</button>}
    </PageHead>
    {state.phase === "loading" && <Skeleton rows={8} columns={2} label="Loading pipeline" />}
    <Failure state={state} notFound={<>Pipeline {runId} does not exist or is not visible to this identity.</>} />
    {problem && (problem.rejections.length ? <Rejections items={problem.rejections} title="Pipeline refused" /> : <div className="notice notice-denied">{problem.detail}</div>)}
    {run && <ol className="ci-steps" aria-label="Publish pipeline steps">
      {steps.map((step, index) => <li key={step.stage} className={`ci-step ci-step-${step.status}`}>
        <details open={step.status === "waiting" || step.status === "failed" || (step.stage === "evals" && !!run.report)}>
          <summary>
            <span className="ci-index">{index + 1}</span><StepStatus status={step.status} /><b>{step.title}</b>
            <span className="ci-summary">{step.summary}</span>
            {results[step.stage]?.at && <small className="muted">{when(results[step.stage].at)}</small>}
          </summary>
          <StepBody step={step} run={run} results={results}>
            {step.stage === "review" && <>
              {waiting && <div className="ci-callout">
                <p><b>{open.length ? `Waiting for you: review ${open.length} case${open.length === 1 ? "" : "s"}.` : "Every case is reviewed."}</b> Nothing publishes with unreviewed tests. Approve, edit or reject each one, then continue.</p>
                <div className="toolbar">
                  <button className="button" type="button" disabled={busy || !authoredPending.length} onClick={bulkApprove}>Approve {authoredPending.length || ""} hand-written</button>
                  <button className="button primary" type="button" disabled={busy || open.length > 0} onClick={() => run_(continuePublish(runId))}>{busy ? "Running evals…" : "Continue to evals"}</button>
                </div>
              </div>}
              <div className="ci-items">{cases.map(item => <ReviewCase key={`${item.case_id}-${item.case_hash ?? ""}-${item.decision ?? ""}`} runId={runId} item={item} onChange={reload} />)}</div>
            </>}
            {step.stage === "evals" && run.report && <>
              <p className="ci-metrics">Pass rate <b>{pct(run.report.pass_rate)}</b> (95% interval {pct(run.report.pass_rate_ci_low)} to {pct(run.report.pass_rate_ci_high)}) · safety <b>{pct(run.report.safety_pass_rate)}</b> · {usd(run.report.avg_cost)} per run · {run.report.runs_per_case ?? "?"} run{run.report.runs_per_case === 1 ? "" : "s"} per case · judge {run.report.judge_calibrated ? "calibrated" : "uncalibrated, not gating"}</p>
              <div className="ci-items">{((run.report.cases ?? []) as ReportCase[]).map(item => <ResultCase key={item.case_id} item={item} />)}</div>
            </>}
          </StepBody>
        </details>
      </li>)}
    </ol>}
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
  const blocking = problem?.rejections.find(r => r.code === "E_PIPELINE_ALREADY_RUNNING" && r.publish_run_id);

  async function start() {
    setBusy(true); setProblem(null);
    const outcome = await call<{ publish_run_id: number }>(startPublish(agent));
    setBusy(false);
    if (outcome.ok) router.push(`/publish?run=${outcome.data.publish_run_id}`);
    else setProblem({ detail: outcome.detail, rejections: outcome.rejections });
  }
  async function abandonAndStart(id: number) {
    setBusy(true);
    await call(abandonPublish(id));
    setBusy(false);
    start();
  }

  return <div className="split">
    <section className="panel">
      <h2>Start a pipeline</h2>
      <p className="panel-note">Steps 1 to 3 run straight away. Then the pipeline stops at the human review step until a person has approved, edited or rejected every test.</p>
      <label className="field"><span>Agent</span><select className="select" value={agent} onChange={event => { setAgent(event.target.value); setProblem(null); }}>{[...new Set([agent, ...names])].map(name => <option key={name}>{name}</option>)}</select></label>
      <button className="button primary" type="button" disabled={busy} onClick={start}>{busy ? "Starting…" : "Start publish pipeline"}</button>
      {blocking ? <div className="ci-callout">
        <p><b>{agent} already has a pipeline in progress</b> (pipeline {blocking.publish_run_id}, waiting at {blocking.stage}). Only one pipeline runs per agent at a time, so two can never race to publish.</p>
        <div className="toolbar">
          <Link className="button primary" href={`/publish?run=${blocking.publish_run_id}`}>Open pipeline {blocking.publish_run_id}</Link>
          <button className="button button-denied" type="button" disabled={busy} onClick={() => abandonAndStart(blocking.publish_run_id!)}>Abandon it and start again</button>
        </div>
      </div> : problem && (problem.rejections.length ? <Rejections items={problem.rejections} title="Pipeline refused" /> : <div className="notice notice-denied">{problem.detail}</div>)}
    </section>
    <section className="panel">
      <h2>Open a pipeline</h2>
      <form className="inline-form" onSubmit={event => { event.preventDefault(); if (runId) router.push(`/publish?run=${runId}`); }}>
        <input className="input" inputMode="numeric" placeholder="Pipeline id" value={runId} onChange={event => setRunId(event.target.value.replace(/\D/g, ""))} aria-label="Pipeline id" />
        <button className="button" type="submit">Open</button>
      </form>
    </section>
  </div>;
}

export default function PublishView() {
  const params = useSearchParams();
  const run = Number(params.get("run"));
  if (Number.isInteger(run) && run > 0) return <RunReview key={run} runId={run} />;
  return <>
    <PageHead crumb="Agents / Publish pipeline" title="Publish pipeline" subtitle="Validate → security review → generate tests → human review → run evals → gate → publish" />
    <StartForm />
  </>;
}
