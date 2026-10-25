"use client";

import Link from "next/link";
import { useState } from "react";
import { call, metaAgents, runMetaAgent, type MetaAgent } from "../../lib/api";
import { useDemoUser, useLive } from "../../lib/live";
import { Failure, PageHead, Payload, Rejections, Skeleton, Verdict, usd } from "../ui";

/** A sensible first input for each meta-agent, editable before running. */
const EXAMPLE: Record<string, Record<string, unknown>> = {
  "agent-builder": { request: "An agent that triages new GitHub issues in our repos and labels them." },
  "security-reviewer": { agent: "pr-reviewer" },
  "cost-optimizer": { agent: "pr-reviewer" },
  "evaluator": { mode: "write_cases", agent: "pr-reviewer" },
  "failure-triage": { agent: "pr-reviewer", window_hours: 24 },
};

type Result = { run_id?: number; status?: string; output?: unknown; error?: string | null; cost_usd?: number; rejections?: [] };

function Row({ agent }: { agent: MetaAgent }) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState(JSON.stringify(EXAMPLE[agent.name] ?? {}, null, 2));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  async function run() {
    let parsed: Record<string, unknown>;
    try { parsed = JSON.parse(input); } catch { setProblem("Input is not valid JSON."); return; }
    setBusy(true); setProblem(null); setResult(null);
    const outcome = await call<Result>(runMetaAgent(agent.name, parsed));
    setBusy(false);
    if (outcome.ok) setResult(outcome.data); else setProblem(outcome.detail);
  }
  return <details className="ci-item" open={open} onToggle={event => setOpen((event.target as HTMLDetailsElement).open)}>
    <summary><code>{agent.name}</code><span className="ci-summary">{agent.description}</span></summary>
    <div className="ci-detail">
      <label className="field"><span>Input</span>
        <textarea className="code-input" rows={4} value={input} onChange={event => setInput(event.target.value)} spellCheck={false} /></label>
      <div className="toolbar"><button className="button primary" type="button" disabled={busy} onClick={run}>{busy ? "Running…" : `Run ${agent.name}`}</button></div>
      {problem && <div className="notice notice-denied">{problem}</div>}
      {result && <div className="dry-run">
        <div className="dry-run-head"><b>Result</b>
          <Verdict kind={result.status === "succeeded" ? "pass" : result.status === "awaiting_approval" ? "pending" : "fail"}>{(result.status ?? "").replace("_", " ")}</Verdict>
          <span>{usd(result.cost_usd)}</span>
          {result.run_id ? <Link href={`/traces?run=${result.run_id}`}>Open trace</Link> : null}</div>
        {result.rejections && result.rejections.length > 0 && <Rejections items={result.rejections} title="Refused" />}
        {result.error && <p className="denied-text">{result.error}</p>}
        {result.output !== null && result.output !== undefined && <Payload value={result.output} />}
      </div>}
    </div>
  </details>;
}

export default function MetaAgentsPage() {
  const user = useDemoUser();
  const [state] = useLive<{ meta_agents: MetaAgent[] }>(user && `meta:${user}`, () => metaAgents());
  return <>
    <PageHead crumb="Agents / Meta-agents" title="Meta-agents"
      subtitle="Built-in agents every user can run. Each run works on your own team only, and their configurations stay hidden from members." />
    {state.phase === "loading" && <Skeleton rows={5} columns={2} label="Loading meta-agents" />}
    <Failure state={state} />
    {state.phase === "ready" && <div className="ci-items">{state.data.meta_agents.map(a => <Row key={a.name} agent={a} />)}</div>}
    <p className="start-foot muted">They reach the platform through the platform connector: search the collection, read your team&apos;s grants, validate and propose drafts, run evals, and request connectors. They propose; they never publish.</p>
  </>;
}
