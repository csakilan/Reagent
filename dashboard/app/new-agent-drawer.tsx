"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { DRAFT_KEY, call, forkAgent, listAgents, resolvedAgent, teamBase, teamGrants, type AgentDetail, type AgentSummary, type Rejection, type TeamGrants } from "../lib/api";
import { useLive } from "../lib/live";
import { Failure, Rejections, Skeleton, Verdict, pct, usd } from "./ui";

/** GET /v0/agents?q= adds why each hit matched (runtime/api/agent_search.py). */
type Match = AgentSummary & { match?: { score: number; reason: string; matched: string[] } };

/** The team's agents closest to what the new one should achieve, ranked on the server by outcome. */
function Suggestions({ team, term, chosen, onFork }: {
  team: string; term: string; chosen: string | null; onFork: (name: string) => void;
}) {
  const [state] = useLive<{ agents: Match[] }>(term && team ? `suggest:${team}:${term}` : null, () => listAgents(term));
  if (!term || !team) return null;
  if (state.phase === "loading") return <Skeleton rows={3} columns={1} label="Finding agents with a similar outcome" />;
  if (state.phase === "error") return null;   // suggestions are a shortcut; the form still works
  const hits = state.data.agents.filter(a => a.team === team).slice(0, 3);
  return <section className="drawer-suggest" aria-label="Agents with a similar outcome">
    <h4>Closest by outcome in {team}</h4>
    {!hits.length ? <p className="muted">No agent in {team} achieves something similar. Start from the base config.</p>
      : <ul className="suggest-list">{hits.map(a => <li key={a.name} className={chosen === a.name ? "selected" : undefined}>
        <div className="suggest-head"><code>{a.name}</code><span className="muted">{a.version}</span>
          {a.pass_rate != null && <span className="muted num">{pct(a.pass_rate)} eval pass</span>}
          <button className="button" type="button" onClick={() => onFork(a.name)} disabled={chosen === a.name}>
            {chosen === a.name ? "Forking this" : "Fork this"}</button></div>
        {a.desired_outcome && <p>{a.desired_outcome}</p>}
        {a.match && <small className="muted">{a.match.reason}</small>}
      </li>)}</ul>}
  </section>;
}
import { layerName, layerOf, type Leaf } from "../lib/resolved";

/** The fork route also returns how the new draft resolves; AgentDetail does not list it. */
type Created = AgentDetail & { rejections?: Rejection[] };

type Resolved = {
  effective: {
    model: { id: string };
    limits: { usd_per_run: number; max_steps: number; timeout_seconds: number; max_call_depth: number };
    tool_permissions: { permission: string; resources?: Record<string, string[]>; requires_approval: boolean }[];
    guardrails: { redaction?: { secrets?: boolean; pii?: boolean }; audit_log?: boolean; approval_timeout_minutes?: number | null };
  };
  provenance: {
    prompt?: Leaf[];
    limits?: Record<string, Leaf>;
    budget?: Record<string, Leaf>;
    guardrails?: { approval?: { require_for_risk?: Leaf } };
  };
};

function Layer({ source }: { source?: string }) {
  return <span className={`layer-tag layer-${layerOf(source)}`}><i />{layerName(source)}</span>;
}

/** Where a per-run limit was set, read from the provenance tree (budget = the agent's own). */
function limitSource(doc: Resolved, key: string) {
  return (doc.provenance.budget?.[key] ?? doc.provenance.limits?.[key])?.source;
}

function Preview({ source, team }: { source: string; team: string }) {
  const [resolved] = useLive<Resolved>(`preview:${source}`, () => resolvedAgent(source));
  const [grants] = useLive<TeamGrants>(`grants:${team}`, () => teamGrants(team));
  if (resolved.phase === "loading" || grants.phase === "loading") return <Skeleton rows={6} columns={2} label="Loading what this agent inherits" />;
  if (resolved.phase === "error") return <Failure state={resolved} />;
  if (grants.phase === "error") return <Failure state={grants} />;
  const doc = resolved.data, g = grants.data, eff = doc.effective;
  const fromBase = source.startsWith("team:");
  const risks = doc.provenance.guardrails?.approval?.require_for_risk;
  return <div className="drawer-preview">
    <h3>What this agent starts with</h3>

    <section>
      <h4>Prompt</h4>
      <ul className="drawer-list">{(doc.provenance.prompt ?? []).map((part, index) =>
        <li key={index}><Layer source={part.source} />
          {part.redacted ? <span className="muted">Set by the enterprise, hidden from members</span>
            : <span className="drawer-text">{String(part.value ?? "")}</span>}</li>)}
      </ul>
    </section>

    <section>
      <h4>Model</h4>
      <p><code>{eff.model.id}</code> <span className="muted">by default. Your team allows</span> {g.models.map(m => <code key={m} className="drawer-chip">{m}</code>)}</p>
    </section>

    <section>
      <h4>Tools</h4>
      {fromBase || !eff.tool_permissions.length
        ? <p className="muted">None yet: an agent gets no tool it does not list. Your team can grant these:</p>
        : <ul className="drawer-list">{eff.tool_permissions.map(t => <li key={t.permission}><code>{t.permission}</code>
            {t.resources && Object.entries(t.resources).map(([k, v]) => <span key={k} className="muted">{k}: {v.join(", ")}</span>)}
            {t.requires_approval && <span className="muted">needs approval</span>}</li>)}</ul>}
      {(fromBase || !eff.tool_permissions.length) && <ul className="drawer-list">{g.connectors.map(c => <li key={c.connector}>
        <Layer source={`team:${team}`} /><code>{c.connector}</code>
        <span>{c.actions.join(", ")}</span>
        {Object.entries(c.resources).map(([k, v]) => <span key={k} className="muted">{k}: {v.join(", ")}</span>)}</li>)}</ul>}
    </section>

    <section>
      <h4>Limits per run</h4>
      <ul className="drawer-list">
        <li><Layer source={limitSource(doc, "usd_per_run")} /><span>{usd(eff.limits.usd_per_run)} per run</span></li>
        <li><Layer source={limitSource(doc, "max_steps")} /><span>{eff.limits.max_steps} steps</span></li>
        <li><Layer source={limitSource(doc, "timeout_seconds")} /><span>{eff.limits.timeout_seconds}s timeout</span></li>
        <li><Layer source={limitSource(doc, "max_call_depth")} /><span>call depth {eff.limits.max_call_depth}</span></li>
      </ul>
      <p className="muted">A lower layer may tighten any of these, never loosen them.</p>
    </section>

    <section>
      <h4>Guardrails</h4>
      <ul className="drawer-list">
        <li><Layer source="enterprise" /><span>secret redaction {eff.guardrails.redaction?.secrets ? "on" : "off"}, PII redaction {eff.guardrails.redaction?.pii ? "on" : "off"}, audit log {eff.guardrails.audit_log ? "on" : "off"}</span></li>
        {risks && <li><Layer source={risks.source} /><span>{risks.redacted ? "approval rules set by the enterprise" : `approval required for ${(risks.value as string[]).join(" and ")} actions`}</span></li>}
        {g.denylist.length > 0 && <li><Layer source="enterprise" /><span>never allowed: {g.denylist.join(", ")}</span></li>}
      </ul>
    </section>
  </div>;
}

export default function NewAgentDrawer({ team, agents, initialFork, onClose, onCreated }: {
  team: string | null; agents: AgentSummary[]; initialFork?: string; onClose: () => void; onCreated: () => void;
}) {
  const teams = Array.from(new Set(agents.map(a => a.team).filter((t): t is string => !!t))).sort();
  const startTeam = agents.find(a => a.name === initialFork)?.team ?? team ?? teams[0] ?? "";
  const [targetTeam, setTargetTeam] = useState<string>(startTeam);
  const [mode, setMode] = useState<"base" | "fork">(initialFork ? "fork" : "base");
  const forkable = agents.filter(a => a.team === targetTeam);
  const [forkFrom, setForkFrom] = useState<string>(initialFork ?? "");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ detail: string; rejections: Rejection[] } | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const [goal, setGoal] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => { const timer = setTimeout(() => setTerm(goal.trim().length >= 3 ? goal.trim() : ""), 400); return () => clearTimeout(timer); }, [goal]);

  // Opened from /agents?new=1 the drawer mounts before the user and agents load,
  // so the team is unknown at first; take it as soon as it is.
  const fallbackTeam = team ?? teams[0] ?? "";
  useEffect(() => { if (!targetTeam && fallbackTeam) setTargetTeam(fallbackTeam); }, [targetTeam, fallbackTeam]);
  // Keep the fork source inside the chosen team.
  useEffect(() => {
    if (!forkable.some(a => a.name === forkFrom)) setForkFrom(forkable[0]?.name ?? "");
  }, [targetTeam]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const source = mode === "base" ? teamBase(targetTeam) : forkFrom;
  const valid = /^[a-z0-9-]+$/.test(name) && !!source;

  async function create() {
    setBusy(true); setProblem(null);
    const outcome = await call<Created>(forkAgent(source, name));
    setBusy(false);
    if (!outcome.ok) { setProblem({ detail: outcome.detail, rejections: outcome.rejections }); return; }
    try { if (outcome.data.spec_yaml) window.sessionStorage.setItem(DRAFT_KEY(outcome.data.name), outcome.data.spec_yaml); } catch { /* the editor falls back to the API */ }
    setCreated(outcome.data);
    onCreated();
  }

  return <>
    <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
    <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="new-agent-title">
      <header className="drawer-head">
        <h2 id="new-agent-title">New agent</h2>
        <button className="button" type="button" onClick={onClose} aria-label="Close">Close</button>
      </header>

      {created ? <div className="drawer-body">
        <p><Verdict kind="pass">Draft created</Verdict></p>
        <p><code>{created.name}</code> {created.version} is a draft in {created.team}.
          {created.forked_from ? <> It records <code>forked_from: {created.forked_from}</code>.</> : <> It starts from the {created.team} base config.</>}</p>
        {created.rejections && created.rejections.length > 0 && <Rejections items={created.rejections} title="Fix before publishing" />}
        <div className="drawer-actions">
          <Link className="button primary" href={`/agents/${created.name}/edit`}>Open in editor</Link>
          <button className="button" type="button" onClick={onClose}>Done</button>
        </div>
      </div> : <form className="drawer-body" onSubmit={event => { event.preventDefault(); if (valid) create(); }}>
        {team === null && teams.length > 0 && <label className="field"><span>Team</span>
          <select className="select" value={targetTeam} onChange={event => setTargetTeam(event.target.value)}>
            {teams.map(t => <option key={t} value={t}>{t}</option>)}</select></label>}

        <label className="field"><span>What should it achieve?</span>
          <textarea className="input textarea" rows={2} value={goal} placeholder="Every PR touching Terraform gets flagged if it opens a bucket to the public"
            onChange={event => setGoal(event.target.value)} /></label>
        <Suggestions team={targetTeam} term={term} chosen={mode === "fork" ? forkFrom : null}
          onFork={agent => { setMode("fork"); setForkFrom(agent); }} />

        <fieldset className="drawer-choice">
          <legend>Start from</legend>
          <label className={mode === "base" ? "choice selected" : "choice"}>
            <input type="radio" name="start" checked={mode === "base"} onChange={() => setMode("base")} />
            <span><strong>{targetTeam} base config</strong>
              <small>A blank agent that inherits the enterprise and team setup. No tools until you add them.</small></span>
          </label>
          <label className={mode === "fork" ? "choice selected" : "choice"}>
            <input type="radio" name="start" checked={mode === "fork"} onChange={() => setMode("fork")} disabled={!forkable.length} />
            <span><strong>Fork an existing agent</strong>
              <small>{forkable.length ? "Copies its tools, prompt and tests, and records where it came from." : "No agents in this team to fork yet."}</small>
              {mode === "fork" && <select className="select" value={forkFrom} onChange={event => setForkFrom(event.target.value)}>
                {forkable.map(a => <option key={a.name} value={a.name}>{a.name}@{a.version}</option>)}</select>}</span>
          </label>
        </fieldset>

        <label className="field"><span>Name</span>
          <input className="input mono" value={name} placeholder="my-agent" autoFocus
            onChange={event => setName(event.target.value.toLowerCase())} aria-invalid={!!name && !/^[a-z0-9-]+$/.test(name)} /></label>
        {name && !/^[a-z0-9-]+$/.test(name) && <p className="denied-text">Lowercase letters, digits and hyphens only.</p>}
        {problem && (problem.rejections.length ? <Rejections items={problem.rejections} title="Not created" /> : <p className="denied-text">{problem.detail}</p>)}

        <div className="drawer-actions">
          <button className="button primary" type="submit" disabled={busy || !valid}>{busy ? "Creating…" : "Create draft"}</button>
          <button className="button" type="button" onClick={onClose}>Cancel</button>
        </div>

        {source && targetTeam && <Preview key={source} source={source} team={targetTeam} />}
      </form>}
    </aside>
  </>;
}
