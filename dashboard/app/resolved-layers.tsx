"use client";

import { Fragment, useMemo, useState } from "react";
import { resolvedAgent } from "../lib/api";
import { brief, groupByLayer, sectionTitle, type LayerGroup, type Setting } from "../lib/layers";
import { useDemoUser, useLive } from "../lib/live";
import { flatten, layerName, layerOf, show, type Layer, type Leaf, type Resolved } from "../lib/resolved";
import { Failure, Icon, Rejections, Skeleton } from "./ui";

/** Read top to bottom: the agent's own spec, then the layers it must fit inside. */
const ROLE: Record<Layer, { title: (source: string, spec: Resolved) => string; rule: string; next?: string }> = {
  agent: { title: (_, spec) => `Agent · ${spec.agent}`, rule: "What this agent asks for. Written by the team.", next: "can only ask for what its team allows" },
  team: { title: source => `Team · ${source.replace(/^team:/, "")}`, rule: "Narrows the enterprise limits for this team's agents. Set by enterprise admins.", next: "can only tighten what the enterprise allows" },
  enterprise: { title: () => "Enterprise", rule: "Outer limits and safety rules for every team and agent. Set by enterprise admins." },
};
const ORDER: Layer[] = ["agent", "team", "enterprise"];

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
        <span className="layer-count">{group.settings.length} settings{limits ? ` · ${limits} tightened` : ""}{group.hidden ? ` · ${group.hidden} hidden` : ""}</span>
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

/** An agent's resolved policy, grouped by the layer each value came from:
 *  the agent's own spec first, then its team, then the enterprise. Enterprise
 *  values arrive from the server already redacted for members (FROZEN D3). */
export default function ResolvedLayers({ agent, user }: { agent: string; user: string | null }) {
  const [view, setView] = useState<"layers" | "values">("layers");
  const [state] = useLive<Resolved>(user && `resolved:${user}:${agent}`, () => resolvedAgent(agent));
  const spec = state.phase === "ready" ? state.data : null;
  const groups = useMemo(() => spec ? groupByLayer(spec.provenance).sort((a, b) => ORDER.indexOf(a.layer) - ORDER.indexOf(b.layer)) : [], [spec]);
  const narrowed = groups.reduce((sum, g) => sum + g.settings.filter(s => s.kind === "limit").length, 0);
  const hidden = groups.reduce((sum, g) => sum + g.hidden, 0);
  return <section className="resolved-in-graph" aria-label={`Inheritance view for ${agent}`}>
    {state.phase === "loading" && <Skeleton rows={8} columns={3} label="Loading the inheritance view" />}
    <Failure state={state} notFound={<><b>{agent}</b> is not visible to <b>{user}</b>.</>} />
    <h2 className="inheritance-title">Inheritance view · {agent}</h2>
    {spec && <>
      <Rejections items={spec.rejections} title="This agent does not resolve" />
      <div className="resolved-intro">
        <p>What <b>{spec.agent}</b> can do, merged from {groups.filter(g => g.settings.length).length} layers on every run: its own spec, then the layers above it, each of which can only tighten what it inherits{narrowed ? <>. Here <b>{narrowed}</b> limits were tightened</> : null}.
          {hidden ? <> {hidden} enterprise settings are hidden because you are a member.</> : null}</p>
        <div className="segmented" role="tablist" aria-label="View">
          <button type="button" role="tab" aria-selected={view === "layers"} className={view === "layers" ? "is-on" : undefined} onClick={() => setView("layers")}>By layer</button>
          <button type="button" role="tab" aria-selected={view === "values"} className={view === "values" ? "is-on" : undefined} onClick={() => setView("values")}>All values</button>
        </div>
      </div>
      {view === "layers" ? <div className="layer-stack">{groups.map(group => <LayerPanel key={group.layer} group={group} spec={spec} />)}</div> : <AllValues spec={spec} />}
    </>}
  </section>;
}
