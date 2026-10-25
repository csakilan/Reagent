"use client";

import Link from "next/link";
import { examplePublishRun } from "../../fixtures/tab-examples";
import type { components } from "../../lib/api-schema";

type Pipeline = components["schemas"]["PublishRun"];
const stages = ["Validate", "Security review", "Generate tests", "Human review", "Run evals", "Gate", "Publish"];
const stageKeys = ["validate", "security", "generate", "review", "evals", "gate", "publish"];
const summary = (value: unknown) => value === undefined ? "Not provided" : JSON.stringify(value, null, 2);

export default function PublishPage() {
  const run: Pipeline = examplePublishRun;
  const stageIndex = stageKeys.indexOf(run.stage);
  return <div className="shell"><aside className="rail"><div className="brand">ReAgent<small>POLICY CONTROL PLANE</small></div><nav className="nav"><Link href="/">Resolved view</Link><Link href="/traces">Trace viewer</Link><Link className="active" href="/publish">Publish pipeline</Link><Link href="/agents">Collection</Link><Link href="/approvals">Approvals</Link></nav></aside><main>
    <header className="page-head"><div><div className="eyebrow">Agent lifecycle / Publish</div><h1>Publish pipeline</h1><p className="subtitle">Example publish run paused at its human review gate.</p></div><span className="example-origin">RUN {run.id} · LOCAL ONLY</span></header>
    <div className="example-banner">EXAMPLE DATA · LOCAL PREVIEW ONLY · Review controls are disabled. No publish or approval action is submitted.</div>
    <div className="pipeline" aria-label="Seven stage publish pipeline">{stages.map((label, index) => <div key={label} className={`stage ${index < stageIndex || run.status === "succeeded" ? "stage-done" : index === stageIndex ? "stage-current" : ""} ${index === 3 ? "stage-human" : ""}`}><span>{String(index + 1).padStart(2, "0")}</span><b>{label}</b>{index === 3 && <small>HUMAN STOP</small>}</div>)}</div><div className="meta-row"><span>AGENT <strong>{run.agent}</strong></span><span>STATUS <strong>{run.status}</strong></span><span>STAGE <strong>{run.stage}</strong></span><span>RUN <strong>{run.id}</strong></span></div>
    <div className="case-heading"><div><div className="eyebrow">Human review · example fixture</div><h2>Generated and inherited cases</h2></div><div className="toolbar"><button className="button" disabled>Bulk approve authored</button><button className="button primary" disabled>Continue to evals</button></div></div>
    <section className="resolved case-table"><div className="case-grid case-header"><span>Case identity</span><span>Input · mocks</span><span>Checks · rubric · answer</span><span>Review</span></div>{(run.cases ?? []).map((item, index) => { const content = (item.case ?? {}) as unknown as Record<string, unknown>; const caseId = item.case_id ?? `case-${index + 1}`; return <article className="case-grid" key={caseId}><div><code>{caseId}</code><p>{item.kind ?? "case"} · {item.origin ?? "unknown"}</p><span className={`verdict ${item.decision === "approved" ? "pass" : item.decision === "rejected" ? "fail" : "pending"}`}>{item.decision ?? "pending review"}</span></div><div><b>Input</b><pre>{summary(content.input)}</pre><b>Mocked sandbox</b><pre>{summary(content.mocks)}</pre></div><div><b>Code checks</b><pre>{summary(content.checks)}</pre><b>Judge rubric</b><pre>{summary(content.rubric)}</pre><b>Answer key</b><pre>{summary(content.expected)}</pre></div><div className="case-actions"><button className="button" disabled>Dry run</button><button className="button" disabled>Review</button></div></article>; })}</section>
  </main></div>;
}
