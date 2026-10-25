"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { DEMO_USERS, listAgents, type AgentSummary } from "../lib/api";
import { recordedAgents } from "../lib/fixtures";
import { useDemoUser, useLive } from "../lib/live";
import NewAgentDrawer from "./new-agent-drawer";
import { Failure, PageHead, Skeleton, SourceTag, pct, usd } from "./ui";

/** The home page: the agents your team can see, run and fork, and where new ones start. */
export default function Collection() {
  const user = useDemoUser();
  const team = DEMO_USERS.find(item => item.id === user)?.team ?? null;
  const [query, setQuery] = useState("");
  const [term, setTerm] = useState("");
  // /agents?new=1 (from Getting started) opens the New agent drawer straight away.
  const params = useSearchParams();
  const [drawer, setDrawer] = useState<{ fork?: string } | null>(() => params.get("new") ? {} : null);
  useEffect(() => { const timer = setTimeout(() => setTerm(query.trim()), 250); return () => clearTimeout(timer); }, [query]);

  const [state, reload] = useLive<{ agents: AgentSummary[] }>(user && `agents:${user}:${term}`, () => listAgents(term || undefined), { fallback: recordedAgents });
  // The server filters by team and query. Recorded data is only narrowed by the search text.
  const all = state.phase === "ready" ? state.data.agents : [];
  const agents = state.phase !== "ready" ? [] : state.source === "live" || !term ? all
    : all.filter(item => `${item.name} ${item.description} ${item.desired_outcome ?? ""} ${(item.tags ?? []).join(" ")}`.toLowerCase().includes(term.toLowerCase()));

  return <>
    <PageHead crumb="Agents" title="Your agents" subtitle={team
      ? <>Agents in <strong>{team}</strong> you can run, edit and fork. Every agent inherits the enterprise and team setup.</>
      : <>Every team&apos;s agents. You are an enterprise admin.</>}>
      <SourceTag state={state} />
      <button className="button primary" type="button" onClick={() => setDrawer({})}>New agent</button>
    </PageHead>
    <div className="search-bar">
      <input className="input" type="search" placeholder="Search by outcome, tag or name" value={query} onChange={event => setQuery(event.target.value)} aria-label="Search agents" />
      {state.phase === "ready" && <span className="muted">{agents.length} agent{agents.length === 1 ? "" : "s"}</span>}
    </div>
    {state.phase === "loading" && <Skeleton rows={6} columns={6} label="Loading agents" />}
    <Failure state={state} />
    {state.phase === "ready" && <section className="table agent-table" aria-label="Agents">
      <div className="table-head"><span>Agent</span><span>Team</span><span>Version</span><span>Eval pass</span><span>Avg cost</span><span>Runs 30d</span><span /></div>
      {agents.map(agent => <div className="table-row" key={agent.name}>
        <div className="agent-cell">
          <Link className="agent-name" href={`/agents/${agent.name}/edit`}>{agent.name}</Link>
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
          <Link className="button" href={`/hierarchy?agent=${agent.name}`}>Policy</Link>
          <button className="button" type="button" onClick={() => setDrawer({ fork: agent.name })}>Fork</button>
        </span>
      </div>)}
      {!agents.length && <div className="empty">{term ? <>No agents match “{term}”.</> : <>No agents yet. Start one from your team&apos;s base config.</>}</div>}
    </section>}
    {drawer && <NewAgentDrawer team={team} agents={all} initialFork={drawer.fork}
      onClose={() => setDrawer(null)} onCreated={reload} />}
  </>;
}
