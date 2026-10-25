"use client";

import Link from "next/link";
import { Fragment, useMemo, useState, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { listAgents, resolvedAgent, type AgentSummary } from "../lib/api";
import { recordedAgents, recordedResolved } from "../lib/fixtures";
import { brief, groupByLayer, sectionTitle, type LayerGroup, type Setting } from "../lib/layers";
import { useDemoUser, useLive } from "../lib/live";
import { flatten, layerName, layerOf, show, type Layer, type Leaf, type Resolved } from "../lib/resolved";
import { Failure, Icon, PageHead, Rejections, Skeleton, SourceTag, when } from "./ui";

const ROLE: Record<Layer, { title: (source: string, spec: Resolved) => string; rule: string; next?: string }> = {
  enterprise: { title: () => "Enterprise", rule: "Outer limits and safety rules for every team and agent. Set by enterprise admins.", next: "Team can only tighten these" },
  team: { title: source => `Team · ${source.replace(/^team:/, "")}`, rule: "Narrows the enterprise limits for this team's agents. Set by enterprise admins.", next: "The agent can only tighten these" },
  agent: { title: (_, spec) => `Agent · ${spec.agent}.agent`, rule: "What this agent asks for, inside the team's limits. Written by the team." },
};

type Obj = Record<string, unknown>;
const isObj = (value: unknown): value is Obj => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const Chips = ({ items }: { items: unknown[] }) => <span className="chips">{items.map((item, index) => <code className="chip" key={index}>{brief(item)}</code>)}</span>;

function LongText({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return <div className="long-text">
    <p className={open ? undefined : "clamped"}>{text}</p>
    <button type="button" className="text-button" onClick={() => setOpen(!open)}><Icon name={open ? "collapse" : "expand"} />{open ? "Show less" : "Show all"}</button>
  </div>;
}

/** One value, written for a person rather than as JSON. */
function Value({ settingKey, value }: { settingKey: string; value: unknown }) {
  if (typeof value === "string") return value.length > 140 || value.includes("\n") ? <LongText text={value} /> : <span>{value}</span>;
  if (Array.isArray(value)) return value.every(item => !isObj(item)) ? <Chips items={value} /> : <span className="value-list">{value.map((item, i) => <Value key={i} settingKey={settingKey} value={item} />)}</span>;
  if (!isObj(value)) return <span>{brief(value, settingKey)}</span>;
  if (typeof value.connector === "string") {
    const resources = isObj(value.resources) ? Object.entries(value.resources).map(([kind, list]) => `${kind}: ${brief(list)}`).join(" · ") : "";
    return <span className="value-tool"><b>{value.connector}</b>{Array.isArray(value.actions) && <Chips items={value.actions} />}{resources && <small>{resources}</small>}</span>;
  }
  if (typeof value.bucket === "string") return <span><code>{value.bucket}</code> <small className="muted">{[value.mode, value.type].filter(Boolean).join(" · ")}</small></span>;
  if (typeof value.type === "string" && !value.properties) return <span className="value-tool"><b>{value.type}</b>{Array.isArray(value.events) && <Chips items={value.events} />}</span>;
  if (isObj(value.properties)) return <span className="value-tool"><b>fields</b><Chips items={Object.keys(value.properties)} /></span>;
  if (typeof value.name === "string" && typeof value.version === "string") return <code>{value.name}@{value.version}</code>;
  if (typeof value.name === "string" && typeof value.kind === "string") return <span><code>{value.name}</code> <small className="muted">{value.kind}</small></span>;
  if (typeof value.when === "string") return <span><code>{value.when}</code> <small className="muted">notify {brief(value.notify)}</small></span>;
  return <span>{brief(value, settingKey)}</span>;
}

function SettingRow({ setting }: { setting: Setting }) {
  return <div className={`setting ${setting.kind === "limit" ? "is-limit" : ""}`}>
    <div className="setting-label">{setting.label}{setting.kind === "limit" && <span className="setting-kind" title="This layer's bound; a lower layer chose a stricter value inside it">limit</span>}</div>
    <div className="setting-value">
      {setting.hidden ? <span className="locked-value">Hidden from you · enterprise admins only</span>
        : (setting.values.length > 1 || setting.key.endsWith("[]")) && setting.values.every(item => !isObj(item) && !Array.isArray(item) && String(item).length < 60) ? <Chips items={setting.values} />
        : setting.values.length > 1 ? <span className="value-list">{setting.values.map((value, index) => <Value key={index} settingKey={setting.key} value={value} />)}</span>
        : <Value settingKey={setting.key} value={setting.values[0]} />}
    </div>
    <div className="setting-note">{setting.notes.join(" · ")}</div>
  </div>;
}

function LayerPanel({ group, spec }: { group: LayerGroup; spec: Resolved }) {
  const role = ROLE[group.layer];
  const sections = [...new Set(group.settings.map(s => s.section))];
  const allHidden = group.settings.length > 0 && group.hidden === group.settings.length;
  const limits = group.settings.filter(s => s.kind === "limit").length;
  return <>
    <section className="layer-panel" aria-label={role.title(group.source, spec)}>
      <header className="layer-panel-head">
        <div>
          <h2 className={`layer-tag layer-${group.layer}`}><i />{role.title(group.source, spec)}</h2>
          <p>{role.rule}</p>
        </div>
        <span className="layer-count">{group.settings.length} settings{limits ? ` · ${limits} tightened below` : ""}{group.hidden ? ` · ${group.hidden} hidden` : ""}</span>
      </header>
      {!group.settings.length && <p className="layer-empty">This layer adds nothing to {spec.agent}; it inherits everything above.</p>}
      {allHidden ? <div className="layer-hidden">
        <p><Icon name="pending" /> You are logged in as a member, so the API sends these settings without their values. You can still see what the enterprise controls:</p>
        <dl>{sections.map(section => <div key={section}><dt>{sectionTitle(section)}</dt><dd><Chips items={group.settings.filter(s => s.section === section).map(s => s.label)} /></dd></div>)}</dl>
      </div> : sections.map(section => <div className="setting-group" key={section}>
        <h3>{sectionTitle(section)}</h3>
        {group.settings.filter(s => s.section === section).map(s => <SettingRow key={`${s.key}-${s.kind}-${s.hidden}`} setting={s} />)}
      </div>)}
    </section>
    {role.next && <div className="layer-step" aria-hidden="true"><span />{role.next}</div>}
  </>;
}

function Lineage({ leaf }: { leaf: Leaf }) {
  const parent = leaf.narrowed_from;
  const parentValue = parent && (parent.value ?? parent.allow ?? parent.declared ?? (parent.deny !== undefined ? { deny: parent.deny } : undefined));
  return <div className="lineage">
    <ol>
      {parent && <li><span className={`layer-tag layer-${layerOf(parent.from_layer)}`}><i />{layerName(parent.from_layer)}</span><span className="lineage-verb">allowed</span>{parent.redacted ? <span className="locked-value">hidden from you</span> : <code>{show(parentValue)}</code>}</li>}
      <li><span className={`layer-tag layer-${layerOf(leaf.source)}`}><i />{layerName(leaf.source)}</span><span className="lineage-verb">{parent ? "narrowed to" : "set"}</span>{leaf.redacted ? <span className="locked-value">hidden from you</span> : <code>{show(leaf.value)}</code>}</li>
    </ol>
  </div>;
}

function AllValues({ spec }: { spec: Resolved }) {
  const rows = useMemo(() => flatten(spec.provenance), [spec]);
  const [open, setOpen] = useState<string | null>(null);
  return <section className="table resolved-table" aria-label="All resolved values">
    <div className="table-head"><span>Set by</span><span>Field</span><span>Effective value</span><span /></div>
    {rows.map((row, index) => {
      const first = index === 0 || rows[index - 1].section !== row.section;
      const isOpen = open === row.path;
      return <Fragment key={row.path}>
        {first && <div className="table-section">{sectionTitle(row.section)}</div>}
        <button type="button" className={`resolved-row ${isOpen ? "is-open" : ""}`} aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : row.path)}>
          <span className={`layer-tag layer-${layerOf(row.leaf.source)}`}><i />{layerName(row.leaf.source)}</span>
          <code className="field-path">{row.path}</code>
          <span className={`value ${row.leaf.redacted ? "locked-value" : ""}`}>{row.leaf.redacted ? "Hidden from you" : show(row.leaf.value)}</span>
          <Icon name={isOpen ? "collapse" : "expand"} />
        </button>
        {isOpen && <Lineage leaf={row.leaf} />}
      </Fragment>;
    })}
  </section>;
}

export default function ResolvedView() {
  const params = useSearchParams();
  const router = useRouter();
  const user = useDemoUser();
  const agent = params.get("agent") ?? "pr-reviewer";
  const view = params.get("view") === "values" ? "values" : "layers";
  const [agents] = useLive<{ agents: AgentSummary[] }>(user && `agents:${user}`, () => listAgents(), { fallback: recordedAgents });
  const [state] = useLive<Resolved>(user && `resolved:${user}:${agent}`, () => resolvedAgent(agent), { fallback: () => user ? recordedResolved(user, agent) : undefined });

  const spec = state.phase === "ready" ? state.data : null;
  const groups = useMemo(() => spec ? groupByLayer(spec.provenance) : [], [spec]);
  const names = agents.phase === "ready" ? agents.data.agents.map(item => item.name) : [agent];
  const go = (next: { agent?: string; view?: string }) => {
    const query = new URLSearchParams({ agent: next.agent ?? agent });
    if ((next.view ?? view) === "values") query.set("view", "values");
    router.replace(`/?${query}`);
  };
  const narrowed = groups.reduce((sum, g) => sum + g.settings.filter(s => s.kind === "limit").length, 0);
  const hidden = groups.reduce((sum, g) => sum + g.hidden, 0);
  const summary: ReactNode = spec && <>
    What <b>{spec.agent}</b> is allowed to do is built from {groups.filter(g => g.settings.length).length} layers, read top to bottom and merged again on every run.
    Each layer can only tighten the one above it{narrowed ? <>; here, <b>{narrowed}</b> limits were tightened by a lower layer</> : null}.
    {hidden ? <> {hidden} enterprise settings are hidden because you are logged in as a member.</> : null}
  </>;

  return <>
    <PageHead crumb="Policy / Resolved view" title={<span className="mono-title">{agent}</span>}
      subtitle={spec ? <>Version {spec.version} · {spec.team ?? "enterprise"} · resolved {when(spec.resolved_at)} for <b>{spec.caller.user}</b>{spec.caller.is_enterprise_admin ? " (enterprise admin)" : " (member)"}</> : "How this agent's policy is built, layer by layer"}>
      <SourceTag state={state} />
      <label className="field-inline"><span>Agent</span>
        <select className="select" value={agent} onChange={event => go({ agent: event.target.value })}>
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
      <div className="resolved-intro">
        <p>{summary}</p>
        <div className="segmented" role="tablist" aria-label="View">
          <button type="button" role="tab" aria-selected={view === "layers"} className={view === "layers" ? "is-on" : undefined} onClick={() => go({ view: "layers" })}>By layer</button>
          <button type="button" role="tab" aria-selected={view === "values"} className={view === "values" ? "is-on" : undefined} onClick={() => go({ view: "values" })}>All values</button>
        </div>
      </div>
      {view === "layers" ? <div className="layer-stack">{groups.map(group => <LayerPanel key={group.layer} group={group} spec={spec} />)}</div> : <AllValues spec={spec} />}
    </>}
  </>;
}
