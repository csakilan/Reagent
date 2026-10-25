"use client";

import { useEffect, useState } from "react";
import { adminLayers, call, connectorRequests, decideRequest, registerItem, saveLayer, type AdminLayers, type ConnectorRequest, type LayerReport, type Rejection } from "../../lib/api";
import { useDemoUser, useLive } from "../../lib/live";
import { Failure, PageHead, Rejections, Skeleton, Verdict, when } from "../ui";

type Problem = { detail: string; rejections: Rejection[] } | null;

function Requests() {
  const user = useDemoUser();
  const [state, reload] = useLive<{ requests: ConnectorRequest[] }>(user && `requests:${user}`, () => connectorRequests());
  const [busy, setBusy] = useState(false);
  async function decide(id: number, status: "fulfilled" | "rejected") {
    setBusy(true); await call(decideRequest(id, status)); setBusy(false); reload();
  }
  const open = state.phase === "ready" ? state.data.requests.filter(r => r.status === "open") : [];
  return <section className="panel">
    <h2>Connector requests {state.phase === "ready" && <span className="muted">{open.length} open</span>}</h2>
    <p className="panel-note">Teams ask here for a connector or action they were not granted. Grant it by editing the team layer below, then mark the request fulfilled.</p>
    {state.phase === "loading" && <Skeleton rows={3} columns={4} label="Loading requests" />}
    <Failure state={state} />
    {state.phase === "ready" && (state.data.requests.length ? <div className="table compact-table">
      <div className="table-head"><span>Team</span><span>Request</span><span>Status</span><span /></div>
      {state.data.requests.slice().reverse().map(r => <div className="table-row" key={r.id}>
        <span><b>{r.team}</b><small>{r.requested_by} · {when(r.created_at)}</small></span>
        <span>{r.description}</span>
        <span>{r.status === "open" ? <Verdict kind="pending">open</Verdict> : r.status === "fulfilled" ? <Verdict kind="pass">fulfilled</Verdict> : <Verdict kind="fail">rejected</Verdict>}</span>
        <span className="row-actions">{r.status === "open" && <>
          <button className="button" type="button" disabled={busy} onClick={() => decide(r.id, "fulfilled")}>Mark fulfilled</button>
          <button className="button button-denied" type="button" disabled={busy} onClick={() => decide(r.id, "rejected")}>Reject</button></>}</span>
      </div>)}
    </div> : <p className="muted">No requests yet.</p>)}
  </section>;
}

function LayerEditor({ layers, onSaved }: { layers: AdminLayers; onSaved: () => void }) {
  const targets = ["enterprise", ...Object.keys(layers.teams)];
  const [target, setTarget] = useState(targets[1] ?? "enterprise");
  const source = target === "enterprise" ? layers.enterprise : layers.teams[target];
  const [text, setText] = useState(source);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<LayerReport | null>(null);
  const [problem, setProblem] = useState<Problem>(null);
  useEffect(() => { setText(source); setReport(null); setProblem(null); }, [target, source]);

  async function submit(dryRun: boolean) {
    setBusy(true); setProblem(null);
    const outcome = await call<LayerReport>(saveLayer(target, text, dryRun));
    setBusy(false);
    if (!outcome.ok) { setReport(null); setProblem({ detail: outcome.detail, rejections: outcome.rejections }); return; }
    setReport(outcome.data);
    if (outcome.data.saved) onSaved();
  }
  const changed = report?.impact.filter(i => i.changed) ?? [];
  return <section className="panel">
    <h2>Edit a layer</h2>
    <p className="panel-note">Changes are checked against the layers above, and apply to every agent below on its next run. Check the impact first: it lists every agent that starts or stops working.</p>
    <label className="field"><span>Layer</span>
      <select className="select" value={target} onChange={event => setTarget(event.target.value)}>
        {targets.map(t => <option key={t} value={t}>{t === "enterprise" ? "Enterprise" : `Team · ${t}`}</option>)}</select></label>
    <textarea className="code-input" rows={18} value={text} onChange={event => setText(event.target.value)} spellCheck={false} aria-label={`${target} layer YAML`} />
    <div className="toolbar">
      <button className="button" type="button" disabled={busy} onClick={() => submit(true)}>{busy ? "Checking…" : "Check impact"}</button>
      <button className="button primary" type="button" disabled={busy || text === source} onClick={() => submit(false)}>Save</button>
      {text !== source && <button className="button" type="button" onClick={() => setText(source)}>Discard changes</button>}
    </div>
    {problem && (problem.rejections.length ? <Rejections items={problem.rejections} title="This layer is not valid" /> : <div className="notice notice-denied">{problem.detail}</div>)}
    {report && <div className="impact">
      <p>{report.saved ? <Verdict kind="pass">saved</Verdict> : <Verdict kind="pending">not saved: impact only</Verdict>}{" "}
        {changed.length ? <b>{changed.length} agent{changed.length === 1 ? "" : "s"} change status.</b> : "No agent changes status."}</p>
      <div className="table compact-table">
        <div className="table-head"><span>Agent</span><span>Before</span><span>After</span><span>Why</span></div>
        {report.impact.map(i => <div className={`table-row ${i.changed ? "is-changed" : ""}`} key={i.agent}>
          <code>{i.agent}</code>
          <span>{i.before === "resolves" ? <Verdict kind="pass">works</Verdict> : <Verdict kind="fail">refused</Verdict>}</span>
          <span>{i.after === "resolves" ? <Verdict kind="pass">works</Verdict> : <Verdict kind="fail">refused</Verdict>}</span>
          <span className="muted">{i.rejections[0]?.message ?? ""}</span>
        </div>)}
      </div>
    </div>}
  </section>;
}

function Registry({ layers, onSaved }: { layers: AdminLayers; onSaved: () => void }) {
  const [kind, setKind] = useState<"connector" | "model">("model");
  const [text, setText] = useState("apiVersion: reagent/v0\nkind: Model\nmetadata:\n  name: my-model\n  version: 1.0.0\nspec:\n  provider: openrouter\n  model: openai/gpt-oss-20b\n  supports_tools: true\n");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const [done, setDone] = useState<string | null>(null);
  async function submit() {
    setBusy(true); setProblem(null); setDone(null);
    const outcome = await call<LayerReport>(registerItem(kind, text));
    setBusy(false);
    if (!outcome.ok) { setProblem({ detail: outcome.detail, rejections: outcome.rejections }); return; }
    setDone(`Registered. Grant it to a team in that team's layer.`); onSaved();
  }
  return <section className="panel">
    <h2>Register a connector or model</h2>
    <p className="panel-note">Registered once, as broadly as possible; teams get subsets. Today: {layers.connectors.length} connectors ({layers.connectors.join(", ")}) and {layers.models.length} models ({layers.models.join(", ")}). A new connector&apos;s actions also need wire names in contracts/connector_actions.json before agents can call them.</p>
    <label className="field"><span>Kind</span><select className="select" value={kind} onChange={event => setKind(event.target.value as "connector" | "model")}><option value="model">Model</option><option value="connector">Connector</option></select></label>
    <textarea className="code-input" rows={10} value={text} onChange={event => setText(event.target.value)} spellCheck={false} aria-label="Registry document YAML" />
    <div className="toolbar"><button className="button primary" type="button" disabled={busy} onClick={submit}>{busy ? "Registering…" : "Register"}</button></div>
    {done && <p><Verdict kind="pass">done</Verdict> {done}</p>}
    {problem && (problem.rejections.length ? <Rejections items={problem.rejections} title="Not registered" /> : <div className="notice notice-denied">{problem.detail}</div>)}
  </section>;
}

export default function AdminPage() {
  const user = useDemoUser();
  const [state, reload] = useLive<AdminLayers>(user && `layers:${user}`, () => adminLayers());
  return <>
    <PageHead crumb="Admin" title="Enterprise admin" subtitle="Requests, team and enterprise layers, and the registry. Enterprise admins only: the server refuses everyone else." />
    {state.phase === "loading" && <Skeleton rows={8} columns={2} label="Loading layers" />}
    {state.phase === "error" && state.status === 403
      ? <div className="notice notice-denied">This screen is for enterprise admins. Log in as Frank to see it. Members can request a connector from <a href="/agents">Your agents</a>.</div>
      : <Failure state={state} />}
    {state.phase === "ready" && <div className="admin-stack">
      <Requests />
      <LayerEditor layers={state.data} onSaved={reload} />
      <Registry layers={state.data} onSaved={reload} />
    </div>}
  </>;
}
