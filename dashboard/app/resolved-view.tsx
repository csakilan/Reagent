"use client";

import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { listAgents, resolvedAgent, type AgentSummary } from "../lib/api";
import { recordedAgents, recordedResolved } from "../lib/fixtures";
import { useDemoUser, useLive } from "../lib/live";
import { flatten, layerName, layerOf, show, type Layer, type Leaf, type Resolved } from "../lib/resolved";
import { Failure, Icon, PageHead, Rejections, Skeleton, SourceTag, when } from "./ui";

const LAYERS: { id: Layer; label: string; rule: string }[] = [
  { id: "enterprise", label: "Enterprise", rule: "Sets the outer limits" },
  { id: "team", label: "Team", rule: "Narrows within enterprise" },
  { id: "agent", label: "Agent", rule: "Asks within team" },
];

function Lineage({ leaf }: { leaf: Leaf }) {
  const parent = leaf.narrowed_from;
  const parentValue = parent && (parent.value ?? parent.allow ?? parent.declared ?? (parent.deny !== undefined ? { deny: parent.deny } : undefined));
  return <div className="lineage">
    <div className="lineage-title">Lineage · lower layers may only tighten</div>
    <ol>
      {parent && <li>
        <span className={`layer-tag layer-${layerOf(parent.from_layer)}`}><i />{layerName(parent.from_layer)}</span>
        <span className="lineage-verb">allowed</span>
        {parent.redacted ? <span className="locked-value">value hidden from this identity</span> : <code>{show(parentValue)}</code>}
      </li>}
      <li>
        <span className={`layer-tag layer-${layerOf(leaf.source)}`}><i />{layerName(leaf.source)}</span>
        <span className="lineage-verb">{parent ? "narrowed to" : "set"}</span>
        {leaf.redacted ? <span className="locked-value">value hidden from this identity</span> : <code>{show(leaf.value)}</code>}
      </li>
    </ol>
  </div>;
}

export default function ResolvedView() {
  const params = useSearchParams();
  const router = useRouter();
  const user = useDemoUser();
  const agent = params.get("agent") ?? "pr-reviewer";
  const [agents] = useLive<{ agents: AgentSummary[] }>(user && `agents:${user}`, () => listAgents(), { fallback: recordedAgents });
  const [state] = useLive<Resolved>(user && `resolved:${user}:${agent}`, () => resolvedAgent(agent), { fallback: () => user ? recordedResolved(user, agent) : undefined });
  const [filter, setFilter] = useState<Layer | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const spec = state.phase === "ready" ? state.data : null;
  const rows = useMemo(() => spec ? flatten(spec.provenance) : [], [spec]);
  const counts = useMemo(() => Object.fromEntries(LAYERS.map(({ id }) => {
    const mine = rows.filter(row => layerOf(row.leaf.source) === id);
    return [id, { total: mine.length, locked: mine.filter(row => row.leaf.redacted).length }];
  })) as Record<Layer, { total: number; locked: number }>, [rows]);
  const visible = filter ? rows.filter(row => layerOf(row.leaf.source) === filter) : rows;
  const names = agents.phase === "ready" ? agents.data.agents.map(item => item.name) : [agent];
  const locked = rows.filter(row => row.leaf.redacted).length;

  return <>
    <PageHead crumb="Policy / Resolved view" title={<span className="mono-title">{agent}</span>}
      subtitle={spec ? <>Version {spec.version} · {spec.level} agent{spec.team ? ` · ${spec.team}` : ""} · resolved {when(spec.resolved_at)} for <b>{spec.caller.user}</b>{spec.caller.is_enterprise_admin ? " (enterprise admin)" : ""}</> : "Every effective value, tagged with the layer it came from"}>
      <SourceTag state={state} />
      <label className="field-inline"><span>Agent</span>
        <select className="select" value={agent} onChange={event => { setOpen(null); router.replace(`/?agent=${encodeURIComponent(event.target.value)}`); }}>
          {[...new Set([agent, ...names])].map(name => <option key={name} value={name}>{name}</option>)}
        </select>
      </label>
      <Link className="button" href={`/agents/${agent}/edit`}>Edit</Link>
      <Link className="button" href={`/traces?agent=${agent}`}>Run</Link>
    </PageHead>

    {state.phase === "loading" && <Skeleton rows={12} columns={3} label="Resolving policy" />}
    <Failure state={state} notFound={<><b>{agent}</b> is not visible to <b>{user}</b>. Members see only their own team&apos;s agents; the API returns the same 404 whether an agent is hidden or missing.</>} />

    {spec && <>
      <Rejections items={spec.rejections} title="This agent does not resolve" />
      <div className="layer-strip" role="group" aria-label="Filter by source layer">
        {LAYERS.map(layer => <button type="button" key={layer.id} aria-pressed={filter === layer.id} className={`layer-cell layer-${layer.id} ${filter === layer.id ? "is-on" : ""}`} onClick={() => setFilter(filter === layer.id ? null : layer.id)}>
          <span className="layer-tag"><i />{layer.label}</span>
          <strong>{counts[layer.id].total}</strong>
          <small>{counts[layer.id].locked ? `${counts[layer.id].locked} hidden from you` : layer.rule}</small>
        </button>)}
        <div className="layer-cell layer-total"><span className="layer-tag">Resolved values</span><strong>{rows.length}</strong><small>{filter ? <button type="button" className="text-button" onClick={() => setFilter(null)}>Show all layers</button> : "Re-merged on every run"}</small></div>
      </div>
      {locked > 0 && <p className="hint"><Icon name="pending" /> {locked} enterprise values are redacted by the API for this identity. Each one keeps its layer tag so you can still see who owns it.</p>}

      <section className="table resolved-table" aria-label="Resolved policy">
        <div className="table-head"><span>Source</span><span>Field</span><span>Effective value</span></div>
        {visible.map((row, index) => {
          const first = index === 0 || visible[index - 1].section !== row.section;
          const field = row.path.slice(row.section.length).replace(/^\./, "") || row.section;
          const isOpen = open === row.path;
          return <Fragment key={row.path}>
            {first && <div className="table-section">{row.section}</div>}
            <button type="button" className={`resolved-row ${isOpen ? "is-open" : ""}`} aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : row.path)}>
              <span className={`layer-tag layer-${layerOf(row.leaf.source)}`}><i />{layerName(row.leaf.source)}</span>
              <code className="field-path">{field}{row.leaf.narrowed_from && <em title="Narrowed from a parent layer">narrowed</em>}</code>
              <span className={`value ${row.leaf.redacted ? "locked-value" : ""}`}>{row.leaf.redacted ? "Hidden · enterprise value" : show(row.leaf.value)}</span>
              <Icon name={isOpen ? "collapse" : "expand"} />
            </button>
            {isOpen && <Lineage leaf={row.leaf} />}
          </Fragment>;
        })}
      </section>
    </>}
  </>;
}
