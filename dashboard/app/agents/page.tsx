"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { DRAFT_KEY, call, forkAgent, listAgents, type AgentDetail, type AgentSummary, type Rejection } from "../../lib/api";
import { recordedAgents } from "../../lib/fixtures";
import { useDemoUser, useLive } from "../../lib/live";
import { Failure, PageHead, Rejections, Skeleton, SourceTag, Verdict, pct, usd } from "../ui";

function ForkForm({ agent, onDone }: { agent: AgentSummary; onDone: () => void }) {
  const [name, setName] = useState(`${agent.name}-fork`);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ detail: string; rejections: Rejection[] } | null>(null);
  const [created, setCreated] = useState<AgentDetail | null>(null);
  const valid = /^[a-z0-9-]+$/.test(name);

  async function submit() {
    setBusy(true); setProblem(null);
    const outcome = await call<AgentDetail>(forkAgent(agent.name, name));
    setBusy(false);
    if (!outcome.ok) { setProblem({ detail: outcome.detail, rejections: outcome.rejections }); return; }
    // The detail route may not be served yet, so hand the forked file to the editor directly.
    try { if (outcome.data.spec_yaml) window.sessionStorage.setItem(DRAFT_KEY(outcome.data.name), outcome.data.spec_yaml); } catch { /* editor falls back to the API */ }
    setCreated(outcome.data);
    onDone();
  }

  if (created) return <div className="row-extra"><Verdict kind="pass">Forked</Verdict> <code>{created.name}</code> {created.version} as a draft in {created.team ?? "your team"}, recording <code>forked_from: {agent.name}@{agent.version}</code>. <Link href={`/agents/${created.name}/edit`}>Open in editor</Link> · <Link href={`/?agent=${created.name}`}>Resolved view</Link></div>;
  return <form className="row-extra inline-form" onSubmit={event => { event.preventDefault(); if (valid) submit(); }}>
    <label className="field-inline"><span>Fork <code>{agent.name}@{agent.version}</code> into your team as</span>
      <input className="input mono" value={name} onChange={event => setName(event.target.value.toLowerCase())} aria-invalid={!valid} autoFocus /></label>
    <button className="button primary" type="submit" disabled={busy || !valid}>{busy ? "Forking…" : "Fork"}</button>
    {!valid && <span className="denied-text">Lowercase letters, digits and hyphens only.</span>}
    {problem && (problem.rejections.length ? <Rejections items={problem.rejections} title="Fork refused" /> : <span className="denied-text">{problem.detail}</span>)}
  </form>;
}

export default function CollectionPage() {
  const user = useDemoUser();
  const [query, setQuery] = useState("");
  const [term, setTerm] = useState("");
  const [forking, setForking] = useState<string | null>(null);
  useEffect(() => { const timer = setTimeout(() => setTerm(query.trim()), 250); return () => clearTimeout(timer); }, [query]);

  const [state, reload] = useLive<{ agents: AgentSummary[] }>(user && `agents:${user}:${term}`, () => listAgents(term || undefined), { fallback: recordedAgents });
  // The server filters by team and query. Recorded data is only narrowed by the search text.
  const agents = state.phase !== "ready" ? [] : state.source === "live" || !term ? state.data.agents
    : state.data.agents.filter(item => `${item.name} ${item.description} ${item.desired_outcome ?? ""} ${(item.tags ?? []).join(" ")}`.toLowerCase().includes(term.toLowerCase()));

  return <>
    <PageHead crumb="Agents / Collection" title="Collection" subtitle="Agents your team can see, run and fork. Filtered by team on the server.">
      <SourceTag state={state} />
      <Link className="button" href="/hierarchy">Hierarchy</Link>
    </PageHead>
    <div className="search-bar">
      <input className="input" type="search" placeholder="Search by outcome, tag or name" value={query} onChange={event => setQuery(event.target.value)} aria-label="Search agents" />
      {state.phase === "ready" && <span className="muted">{agents.length} agent{agents.length === 1 ? "" : "s"}</span>}
    </div>
    {state.phase === "loading" && <Skeleton rows={6} columns={6} label="Loading agents" />}
    <Failure state={state} />
    {state.phase === "ready" && <section className="table agent-table" aria-label="Agents">
      <div className="table-head"><span>Agent</span><span>Team</span><span>Version</span><span>Eval pass</span><span>Avg cost</span><span>Runs 30d</span><span /></div>
      {agents.map(agent => <Fragment key={agent.name}>
        <div className="table-row">
          <div className="agent-cell">
            <Link className="agent-name" href={`/?agent=${agent.name}`}>{agent.name}</Link>
            <p>{agent.description}</p>
            {agent.desired_outcome && <p className="muted">Outcome · {agent.desired_outcome}</p>}
            <div className="tags">{(agent.tags ?? []).map(tag => <span className="tag" key={tag}>{tag}</span>)}{agent.forked_from && <span className="tag">forked from {agent.forked_from}</span>}{agent.update_available && <span className="tag">update available</span>}</div>
          </div>
          <span>{agent.team ?? "enterprise"}</span>
          <span className="mono">{agent.version}<small>{agent.status ?? ""}</small></span>
          <span className="num">{pct(agent.pass_rate)}</span>
          <span className="num">{usd(agent.avg_cost_usd)}</span>
          <span className="num">{agent.runs_30d ?? "—"}</span>
          <span className="row-actions">
            <Link className="button" href={`/agents/${agent.name}/edit`}>Edit</Link>
            <button className="button" type="button" aria-expanded={forking === agent.name} onClick={() => setForking(forking === agent.name ? null : agent.name)}>Fork</button>
          </span>
        </div>
        {forking === agent.name && <ForkForm agent={agent} onDone={reload} />}
      </Fragment>)}
      {!agents.length && <div className="empty">No agents match “{term}”.</div>}
    </section>}
  </>;
}
