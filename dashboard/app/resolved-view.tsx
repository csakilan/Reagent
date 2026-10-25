"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import memberFixture from "../fixtures/resolved/pr-reviewer.member.json";
import adminFixture from "../fixtures/resolved/pr-reviewer.admin.json";

type Leaf = { value: unknown; source: string; redacted?: boolean; narrowed_from?: unknown };
type Resolved = { agent: string; version: string; level: string; team: string; resolved_at: string; caller: { user: string; team: string; is_enterprise_admin: boolean }; provenance: Record<string, unknown>; rejections: Array<{ code: string; field: string; blocked_by: string; message: string }> };
type Row = { path: string; leaf: Leaf };

const fixtures: Record<string, Resolved> = { member: memberFixture as Resolved, admin: adminFixture as Resolved };
const layerClass = (source: string) => source === "enterprise" ? "enterprise" : source.startsWith("team:") ? "team" : "agent";
const sourceName = (source: string) => source === "agent" ? "Agent" : source.startsWith("team:") ? `Team · ${source.slice(5)}` : "Enterprise";

function flatten(value: unknown, path = ""): Row[] {
  if (Array.isArray(value)) return value.flatMap((item, index) => flatten(item, `${path}[${index}]`));
  if (value && typeof value === "object") {
    if ("source" in value && "value" in value && typeof value.source === "string") return [{ path, leaf: value as Leaf }];
    return Object.entries(value).flatMap(([key, child]) => flatten(child, path ? `${path}.${key}` : key));
  }
  return [];
}

function display(value: unknown) {
  if (typeof value === "string") return JSON.stringify(value);
  const serialized = JSON.stringify(value);
  return serialized === undefined ? "undefined" : serialized;
}

export default function ResolvedView() {
  const [identity, setIdentity] = useState("member");
  const [selected, setSelected] = useState<string | null>(null);
  const [showChain, setShowChain] = useState<string | null>(null);
  const spec = fixtures[identity];
  const rows = useMemo(() => flatten(spec.provenance), [spec]);
  const rejections = spec.rejections;

  return <div className="shell">
    <aside className="rail">
      <div className="brand">ReAgent<small>POLICY CONTROL PLANE</small></div>
      <nav className="nav" aria-label="Main navigation">
        <Link className="active" href="/">Resolved view</Link><Link href="/traces">Trace viewer</Link><Link href="/publish">Publish pipeline</Link><Link href="/agents">Collection</Link><Link href="/approvals">Approvals</Link>
      </nav>
      <label className="user-switch">DEMO IDENTITY<select aria-label="Demo identity" value={identity} onChange={(event) => { setIdentity(event.target.value); setSelected(null); setShowChain(null); }}><option value="member">Member · live + fixture</option><option value="admin">Admin · fixture preview</option></select></label>
    </aside>
    <main>
      <header className="page-head"><div><div className="eyebrow">Policy / Resolution</div><h1>{spec.agent}</h1><p className="subtitle">Effective policy with field-level lineage</p></div><div className="toolbar"><button className="button" type="button" onClick={() => setIdentity(identity === "member" ? "admin" : "member")}>Compare identity</button><span className="example-origin">EXAMPLE · LOCAL ONLY</span></div></header>
      <div className="example-banner">EXAMPLE DATA · LOCAL PREVIEW ONLY · Switch identity to compare the member and administrator fixtures.</div>
      <div className="meta-row"><span>VERSION <strong>{spec.version}</strong></span><span>TEAM <strong>{spec.team}</strong></span><span>RESOLVED <strong>{spec.resolved_at}</strong></span></div>
      {rejections.length > 0 && <section className="rejections" aria-label="Policy rejections"><div className="rejection-head"><span>Rejected policy changes</span></div>{rejections.map((item, index) => <div className="rejection" key={`${item.field}-${index}`}><div><strong>{item.field}</strong> · blocked by <strong>{item.blocked_by}</strong></div><div>{item.message}</div></div>)}</section>}
      <div className="layer-key" aria-label="Provenance layers"><span className="key-item"><i className="key-dot" style={{ background: "var(--enterprise)" }} />Enterprise</span><span className="key-item"><i className="key-dot" style={{ background: "var(--team)" }} />Team</span><span className="key-item"><i className="key-dot" style={{ background: "var(--agent)" }} />Agent</span></div>
      <section className="resolved" aria-label="Resolved policy">
        <div className="resolved-head"><span>Source layer</span><span>Resolved document · {rows.length} policy values</span></div>
        {rows.map(({ path, leaf }) => <div key={path}>
          <div className="policy-row"><div className={`gutter ${layerClass(leaf.source)}`}><button type="button" aria-label={`Show lineage for ${path}`} onClick={() => { setSelected(path); setShowChain(showChain === path ? null : path); }} /><span>{sourceName(leaf.source)}</span></div><div className={`yaml ${leaf.redacted ? "locked" : ""}`}>{path}: {leaf.redacted ? "[locked · value hidden]" : display(leaf.value)}</div></div>
          {showChain === path && selected === path && <div className="chain"><div className="chain-title">POLICY LINEAGE · {path}</div><div className="chain-item"><span>Source</span><span>{sourceName(leaf.source)}</span></div>{leaf.narrowed_from !== undefined && <div className="chain-item"><span>Narrowed from</span><code>{display(leaf.narrowed_from)}</code></div>}<div className="chain-item"><span>Effective value</span><span>{leaf.redacted ? "Enterprise value hidden for this identity" : display(leaf.value)}</span></div></div>}
        </div>)}
      </section>
    </main>
  </div>;
}
