"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import ResolvedLayers from "../resolved-layers";
import type { PointerEvent, WheelEvent, KeyboardEvent, ReactNode } from "react";

/** GET /v0/hierarchy: built per caller on the server. A member receives only their
 *  own team and a locked enterprise node; nothing else reaches the browser. */
type Agent = { name: string; version: string; model?: string | null; fallback?: string | null; tools: string[];
  resources: string[]; budget: string; purpose: string; prompt: string; memory: string[]; evaluation: string[];
  triggers: string[]; guardrails: string[]; observability: string[]; source: string };
type Team = { name: string; description: string; source: string; members: string[]; approvers: string[]; prompt: string;
  models: { allow?: string[] }; connectors: string[]; guardrails: string[]; limits: Record<string, number>;
  memory: string[]; agents: Agent[] };
type Enterprise = { name: string; locked: boolean; description: string; admins?: string[]; prompt?: string;
  models?: { allow?: string[] }; connectors?: string[]; triggers?: string[]; guardrails?: string[];
  observability?: string[]; limits?: Record<string, number>; memory?: string[]; trace_retention_days?: number;
  evaluation?: { judge_model?: string; votes?: number; min_agreement?: number; min_cases?: string; tool_coverage?: boolean } };
export type Setup = { label: string; viewer: { user: string; team: string | null; is_enterprise_admin: boolean };
  enterprise: Enterprise; teams: Team[] };
type Selection = { kind: "enterprise" } | { kind: "team"; team: Team } | { kind: "agent"; team: Team; agent: Agent };
type Point = { x: number; y: number };
const HEIGHT = 800;

// Each team takes only the width its own agents need, and a team's agents wrap into
// rows of AGENT_COLS, so one big group (the enterprise's agents) cannot stretch the
// whole tree. The viewBox is a fixed window; the camera fits the tree into it.
const AGENT_COLS = 3, AGENT_STEP = 230, ROW_STEP = 140, TEAM_GAP = 70, BUS_Y = 500, AGENT_Y = 580;
function makeLayout(setup: Setup) {
  const GRAPH_WIDTH = 1200;
  const widthOf = (team: Team) => Math.max(270, Math.min(Math.max(team.agents.length, 1), AGENT_COLS) * AGENT_STEP);
  const centers: number[] = [];
  let cursor = 0;
  for (const team of setup.teams) { const w = widthOf(team); centers.push(cursor + w / 2); cursor += w + TEAM_GAP; }
  const span = Math.max(0, cursor - TEAM_GAP);
  const offset = GRAPH_WIDTH / 2 - span / 2;
  const enterprisePoint = { x: GRAPH_WIDTH / 2, y: 115 };
  const teamPoint = (_team: Team, index: number, _count: number): Point => ({ x: offset + centers[index], y: 330 });
  const agentPoint = (team: Team, teamIndex: number, agentIndex: number, teamCount: number): Point => {
    const cols = Math.min(team.agents.length, AGENT_COLS);
    const first = teamPoint(team, teamIndex, teamCount).x - ((cols - 1) / 2) * AGENT_STEP;
    return { x: first + (agentIndex % AGENT_COLS) * AGENT_STEP, y: AGENT_Y + Math.floor(agentIndex / AGENT_COLS) * ROW_STEP };
  };
  return { GRAPH_WIDTH, enterprisePoint, teamPoint, agentPoint };
}
/** The camera that shows every node, centred, never zoomed past 0.9. */
function fitted(points: Point[], width: number) {
  const minX = Math.min(...points.map(p => p.x)) - 140, maxX = Math.max(...points.map(p => p.x)) + 140;
  const minY = Math.min(...points.map(p => p.y)) - 100, maxY = Math.max(...points.map(p => p.y)) + 90;
  const zoom = Math.min(width / (maxX - minX), HEIGHT / (maxY - minY), 0.9);
  return { zoom, x: width / 2 - ((minX + maxX) / 2) * zoom, y: HEIGHT / 2 - ((minY + maxY) / 2) * zoom };
}
const connect = (a: Point, b: Point) => `M ${a.x} ${a.y} C ${a.x} ${(a.y + b.y) / 2}, ${b.x} ${(a.y + b.y) / 2}, ${b.x} ${b.y}`;
const text = (values: string[] | undefined) => values?.length ? values.join(" · ") : "Not set";
const HIDDEN = "Set by the enterprise. Hidden from members.";

function DetailSection({ title, inherited, children }: { title: string; inherited?: boolean; children: ReactNode }) {
  return <section className="graph-detail-section"><h3>{title}<span>{inherited ? "INHERITED" : "CONFIGURED HERE"}</span></h3>{children}</section>;
}
function DetailRows({ rows }: { rows: [string, ReactNode][] }) {
  return <dl className="graph-detail-rows">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? "Not set"}</dd></div>)}</dl>;
}

/** Admins receive the enterprise's own agents; show them as a group beside the teams. */
const ENTERPRISE_GROUP = "enterprise-agents";
function withEnterpriseAgents(setup: Setup): Setup {
  const own = (setup.enterprise as Enterprise & { agents?: Agent[] }).agents ?? [];
  if (!own.length) return setup;
  const group: Team = { name: ENTERPRISE_GROUP, description: "Agents that run at the enterprise level: meta-agents and services.",
    source: "enterprise", members: [], approvers: [], prompt: "", models: {}, connectors: [], guardrails: [], limits: {}, memory: [], agents: own };
  return { ...setup, teams: [...setup.teams, group] };
}

export default function ConfigGraph({ setup: given, initialAgent }: { setup: Setup; initialAgent?: string | null }) {
  const setup = useMemo(() => withEnterpriseAgents(given), [given]);
  const { GRAPH_WIDTH, enterprisePoint, teamPoint, agentPoint } = useMemo(() => makeLayout(setup), [setup]);
  const E = setup.enterprise;
  const [selected, setSelected] = useState<Selection>(() => {
    for (const team of setup.teams) {
      const agent = team.agents.find(a => a.name === initialAgent);
      if (agent) return { kind: "agent", team, agent };
    }
    return { kind: "enterprise" };
  });
  const [dragging, setDragging] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const dragOrigin = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const selectedId = selected.kind === "enterprise" ? "enterprise" : selected.kind === "team" ? `team:${selected.team.name}` : `agent:${selected.team.name}:${selected.agent.name}`;
  const allPoints = useMemo(() => {
    const points: Point[] = [enterprisePoint];
    setup.teams.forEach((team, ti) => {
      points.push(teamPoint(team, ti, setup.teams.length));
      team.agents.forEach((_, ai) => points.push(agentPoint(team, ti, ai, setup.teams.length)));
    });
    return points;
  }, [setup, enterprisePoint, teamPoint, agentPoint]);
  const [camera, setCamera] = useState(() => fitted(allPoints, GRAPH_WIDTH));

  function viewPoint(event: { clientX: number; clientY: number }) {
    const svg = svgRef.current;
    const matrix = svg?.getScreenCTM();
    if (!svg || !matrix) return { x: 0, y: 0 };
    const point = svg.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
    const converted = point.matrixTransform(matrix.inverse());
    return { x: converted.x, y: converted.y };
  }
  function fit() { setCamera(fitted(allPoints, GRAPH_WIDTH)); }
  function focus(point: Point) {
    const zoom = Math.max(0.7, Math.min(1.12, camera.zoom));
    setCamera({ zoom, x: GRAPH_WIDTH / 2 - point.x * zoom, y: HEIGHT / 2 - point.y * zoom });
  }
  function zoomAt(nextZoom: number, anchor = { x: GRAPH_WIDTH / 2, y: HEIGHT / 2 }) {
    const zoom = Math.max(0.42, Math.min(1.65, nextZoom));
    const scale = zoom / camera.zoom;
    setCamera({ zoom, x: anchor.x - (anchor.x - camera.x) * scale, y: anchor.y - (anchor.y - camera.y) * scale });
  }
  function onPointerDown(event: PointerEvent<SVGSVGElement>) {
    if (event.target instanceof Element && event.target.closest(".graph-node")) return;
    if (!svgRef.current) return;
    const p = viewPoint(event); dragOrigin.current = { x: p.x, y: p.y, tx: camera.x, ty: camera.y };
    svgRef.current.setPointerCapture(event.pointerId); setDragging(true);
  }
  function onPointerMove(event: PointerEvent<SVGSVGElement>) {
    const origin = dragOrigin.current;
    if (!origin) return;
    const p = viewPoint(event); setCamera(c => ({ ...c, x: origin.tx + p.x - origin.x, y: origin.ty + p.y - origin.y }));
  }
  function onPointerUp() { dragOrigin.current = null; setDragging(false); }
  function onWheel(event: WheelEvent<SVGSVGElement>) {
    event.preventDefault(); const anchor = viewPoint(event); zoomAt(camera.zoom * (event.deltaY < 0 ? 1.1 : 0.9), anchor);
  }
  function choose(selection: Selection, point: Point) { setSelected(selection); focus(point); }
  function nodeKey(event: KeyboardEvent<SVGGElement>, selection: Selection, point: Point) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(selection, point); } }

  const selectedTitle = selected.kind === "enterprise" ? E.name : selected.kind === "team" ? selected.team.name : selected.agent.name;
  const selectedLayer = selected.kind === "enterprise" ? "Enterprise layer" : selected.kind === "team" ? "Team layer" : "Agent layer";
  return <><div className="hierarchy-workspace">
    <section className="graph-panel" aria-label="Configuration hierarchy graph">
      <div className="graph-toolbar"><div><span className="crumb">Configuration hierarchy</span><div className="graph-count">1 enterprise <b>·</b> {setup.teams.filter(t => t.source !== "enterprise").length} teams <b>·</b> {setup.teams.reduce((n, team) => n + team.agents.length, 0)} agents</div></div><div className="camera-controls"><button type="button" aria-label="Zoom out" onClick={() => zoomAt(camera.zoom / 1.2)}>−</button><span>{Math.round(camera.zoom * 100)}%</span><button type="button" aria-label="Zoom in" onClick={() => zoomAt(camera.zoom * 1.2)}>+</button><button type="button" aria-label="Fit graph to view" onClick={fit}>Fit</button></div></div>
      <div className="graph-viewport"><svg ref={svgRef} viewBox={`0 0 ${GRAPH_WIDTH} ${HEIGHT}`} className={`hierarchy-svg ${dragging ? "is-dragging" : ""}`} role="group" aria-label="Interactive enterprise, team, and agent hierarchy" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} onWheel={onWheel}>
        <g className="camera-layer" style={{ transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.zoom})` }}>
          {setup.teams.map((team, ti) => {
            const tp = teamPoint(team, ti, setup.teams.length);
            const agentPoints = team.agents.map((_, ai) => agentPoint(team, ti, ai, setup.teams.length));
            const left = agentPoints.length ? Math.min(...agentPoints.map(point => point.x)) : tp.x;
            const rows = Math.ceil(agentPoints.length / AGENT_COLS);
            const bus = (r: number) => BUS_Y + r * ROW_STEP;
            const spine = left + AGENT_STEP / 2;
            const buses = Array.from({ length: rows }, (_, r) => {
              const xs = agentPoints.filter((_, ai) => Math.floor(ai / AGENT_COLS) === r).map(point => point.x);
              return `M ${Math.min(...xs, r ? spine : Infinity)} ${bus(r)} H ${Math.max(...xs)}`;
            }).join(" ");
            const branch = `M ${tp.x} ${tp.y + 50} V ${BUS_Y} ${agentPoints.length ? buses : ""}${rows > 1 ? ` M ${spine} ${BUS_Y} V ${bus(rows - 1)}` : ""}`;
            return <g key={`edges-${team.name}`}>
              <path id={`enterprise-${team.name}`} className="graph-edge graph-edge-enterprise" d={connect({ x: enterprisePoint.x, y: enterprisePoint.y + 58 }, { x: tp.x, y: tp.y - 51 })} />
              <path className="graph-edge graph-edge-team" d={branch} />
              {team.agents.map((agent, ai) => { const ap = agentPoints[ai]; const drop = `M ${ap.x} ${ap.y - (AGENT_Y - BUS_Y)} V ${ap.y - 42}`; return <path key={`edge-${agent.name}`} className="graph-edge graph-edge-team" d={drop} />; })}
            </g>;
          })}
          <g className={`graph-node enterprise-node ${selectedId === "enterprise" ? "is-selected" : ""}`} role="button" tabIndex={0} aria-label={`Select enterprise ${E.name}`} aria-pressed={selectedId === "enterprise"} onPointerDown={e => e.stopPropagation()} onClick={() => choose({ kind: "enterprise" }, enterprisePoint)} onKeyDown={e => nodeKey(e, { kind: "enterprise" }, enterprisePoint)}>
            <rect x={enterprisePoint.x - 150} y={enterprisePoint.y - 57} width="300" height="114" rx="4" /><text className="node-kicker" x={enterprisePoint.x} y={enterprisePoint.y - 22} textAnchor="middle">ENTERPRISE LAYER</text><text className="node-name enterprise-name" x={enterprisePoint.x} y={enterprisePoint.y + 13} textAnchor="middle">{E.name}</text><text className="node-meta" x={enterprisePoint.x} y={enterprisePoint.y + 36} textAnchor="middle">GOVERNS ALL TEAMS</text>
          </g>
          {setup.teams.map((team, ti) => {
            const tp = teamPoint(team, ti, setup.teams.length); const teamId = `team:${team.name}`; const teamSelection: Selection = { kind: "team", team };
            return <g key={teamId}>
              <g className={`graph-node team-node ${selectedId === teamId ? "is-selected" : ""}`} role="button" tabIndex={0} aria-label={`Select team ${team.name}`} aria-pressed={selectedId === teamId} onPointerDown={e => e.stopPropagation()} onClick={() => choose(teamSelection, tp)} onKeyDown={e => nodeKey(e, teamSelection, tp)}>
                <rect x={tp.x - 122} y={tp.y - 50} width="244" height="100" rx="4" /><text className="node-kicker" x={tp.x} y={tp.y - 17} textAnchor="middle">{team.source === "enterprise" ? "ENTERPRISE AGENTS" : "TEAM LAYER"}</text><text className="node-name" x={tp.x} y={tp.y + 10} textAnchor="middle">{team.name}</text><text className="node-meta" x={tp.x} y={tp.y + 31} textAnchor="middle">{team.source === "enterprise" ? `${team.agents.length} AGENTS · NO TEAM LAYER` : `${team.agents.length} AGENTS · ${team.members.length} MEMBERS`}</text>
              </g>
              {team.agents.map((agent, ai) => {
                const ap = agentPoint(team, ti, ai, setup.teams.length); const agentId = `agent:${team.name}:${agent.name}`; const agentSelection: Selection = { kind: "agent", team, agent };
                return <g className={`graph-node agent-node ${selectedId === agentId ? "is-selected" : ""}`} key={agentId} role="button" tabIndex={0} aria-label={`Select agent ${agent.name}`} aria-pressed={selectedId === agentId} onPointerDown={e => e.stopPropagation()} onClick={() => choose(agentSelection, ap)} onKeyDown={e => nodeKey(e, agentSelection, ap)}>
                  <rect x={ap.x - 105} y={ap.y - 42} width="210" height="84" rx="4" /><text className="node-kicker" x={ap.x} y={ap.y - 12} textAnchor="middle">AGENT · {agent.version}</text><text className="node-name agent-name" x={ap.x} y={ap.y + 13} textAnchor="middle">{agent.name}</text><text className="node-meta" x={ap.x} y={ap.y + 30} textAnchor="middle">{agent.model}</text>
                </g>;
              })}
            </g>;
          })}
        </g>
      </svg><div className="graph-hint">Drag canvas to pan <span>·</span> Scroll to zoom <span>·</span> Select a node to inspect</div></div>
      <div className="graph-legend"><span><i className="legend-mark legend-enterprise" />Enterprise provenance</span><span><i className="legend-mark legend-team" />Team provenance</span><span><i className="legend-mark legend-agent" />Agent configuration</span></div>
    </section>
    <aside className="graph-details" aria-live="polite"><header><div className="crumb">{selectedLayer}</div><h2>{selectedTitle}</h2><p>Live from the API, filtered for {setup.viewer.user}{setup.viewer.is_enterprise_admin ? " (enterprise admin)" : ` (${setup.viewer.team})`}</p></header>
      {selected.kind === "enterprise" && E.locked && <>
        <DetailSection title="Enterprise policy" inherited><p className="graph-locked">{E.description}</p>
          <DetailRows rows={[["Why it is hidden", "Members never see the enterprise layer (SPEC.md §2). The server does not send it."],
            ["What still applies", "Its limits, guardrails and deny rules bind every team and agent below it. Your team's layer and your agents can only tighten them."]]} /></DetailSection>
      </>}
      {selected.kind === "enterprise" && !E.locked && <>
        <DetailSection title="Enterprise policy"><DetailRows rows={[["Administrators", text(E.admins)], ["System prompt", E.prompt], ["Allowed triggers", text(E.triggers)]]} /></DetailSection>
        <DetailSection title="Models and evaluation"><DetailRows rows={[["Model allowlist", text(E.models?.allow)], ["Judge", `${E.evaluation?.judge_model} · ${E.evaluation?.votes} votes · ${((E.evaluation?.min_agreement ?? 0) * 100).toFixed(0)}% minimum agreement`], ["Requirements", `${E.evaluation?.min_cases} · tool coverage ${E.evaluation?.tool_coverage ? "required" : "optional"}`]]} /></DetailSection>
        <DetailSection title="Security guardrails"><DetailRows rows={[["Guardrails", text(E.guardrails)], ["Observability", text(E.observability)], ["Trace retention", `${E.trace_retention_days} days`]]} /></DetailSection>
        <DetailSection title="Budgets and limits"><DetailRows rows={[["Monthly budget", `$${E.limits?.usd_per_month}`], ["Per-run cap", `$${E.limits?.usd_per_run?.toFixed(2)}`], ["Execution", `${E.limits?.max_steps} steps · ${E.limits?.timeout_seconds}s · depth ${E.limits?.max_call_depth}`]]} /></DetailSection>
        <DetailSection title="Connector permissions"><DetailRows rows={[["Registered grants", text(E.connectors)], ["Memory", text(E.memory)]]} /></DetailSection>
      </>}
      {selected.kind === "team" && <>
        <DetailSection title="Team layer"><DetailRows rows={[["Description", selected.team.description], ["Members", text(selected.team.members)], ["Approvers", text(selected.team.approvers)], ["Team prompt", selected.team.prompt]]} /></DetailSection>
        <DetailSection title="Inherited enterprise policy" inherited><DetailRows rows={[["Allowed models", text(selected.team.models.allow)], ["Secret and PII redaction", "Required · cannot be disabled"], ["Audit and trace payloads", "Audit on · payloads redacted · 30-day retention"], ["Per-run ceiling", E.limits ? `$${E.limits.usd_per_run.toFixed(2)} maximum inherited` : HIDDEN]]} /></DetailSection>
        <DetailSection title="Team grants and limits"><DetailRows rows={[["Connector scope", text(selected.team.connectors)], ["Team guardrails", text(selected.team.guardrails)], ["Team budget", `$${selected.team.limits.usd_per_month} / month · ${selected.team.limits.max_concurrent_runs} concurrent runs`], ["Memory buckets", text(selected.team.memory)]]} /></DetailSection>
        <DetailSection title="Available agents"><div className="graph-agent-list">{selected.team.agents.map(agent => <button key={agent.name} onClick={() => choose({ kind: "agent", team: selected.team, agent }, agentPoint(selected.team, setup.teams.indexOf(selected.team), selected.team.agents.indexOf(agent), setup.teams.length))}>{agent.name}<span>{agent.model}</span></button>)}</div></DetailSection>
      </>}
      {selected.kind === "agent" && <>
        <DetailSection title="Agent"><DetailRows rows={[["File", `${selected.agent.name}.agent · ${selected.agent.version}`], ["Purpose", selected.agent.purpose],
          ["Model", selected.agent.model], ["Tools", text(selected.agent.tools)], ["Budget", selected.agent.budget]]} /></DetailSection>
        <p className="graph-locked">Its inheritance view, agent first, then {selected.team.source === "enterprise" ? "" : `${selected.team.name}, then `}the enterprise, is below the graph.</p>
        <div className="toolbar"><Link className="button" href={`/agents/${selected.agent.name}/edit`}>Edit</Link><Link className="button" href={`/traces?agent=${selected.agent.name}`}>Run</Link></div>
      </>}
      <div className="graph-detail-foot">Read-only. Team and enterprise layers are edited by enterprise admins, and every change applies on each agent’s next run.</div>
    </aside>
  </div>
  {selected.kind === "agent" && <ResolvedLayers key={selected.agent.name} agent={selected.agent.name} user={setup.viewer.user} />}
  </>;
}
