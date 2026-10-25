"use client";

import { useMemo, useRef, useState } from "react";
import type { PointerEvent, WheelEvent, KeyboardEvent, ReactNode } from "react";
import exampleSetup from "../../fixtures/example-setup.json";

type Setup = typeof exampleSetup;
type Team = Setup["teams"][number];
type Agent = Team["agents"][number];
type Selection = { kind: "enterprise" } | { kind: "team"; team: Team } | { kind: "agent"; team: Team; agent: Agent };
type Point = { x: number; y: number };
const MAX_AGENTS = Math.max(1, ...exampleSetup.teams.map(team => team.agents.length));
const TEAM_SPACING = Math.max(700, (MAX_AGENTS - 1) * 220 + 270);
const SIDE_MARGIN = Math.max(350, (MAX_AGENTS - 1) * 110 + 145);
const GRAPH_WIDTH = Math.max(1200, SIDE_MARGIN * 2 + TEAM_SPACING * Math.max(0, exampleSetup.teams.length - 1));
const HEIGHT = 800;
const enterprisePoint = { x: GRAPH_WIDTH / 2, y: 115 };
const teamPoint = (_team: Team, index: number, count: number): Point => ({ x: count === 1 ? GRAPH_WIDTH / 2 : SIDE_MARGIN + index * TEAM_SPACING, y: 330 });
const agentPoint = (team: Team, teamIndex: number, agentIndex: number, teamCount: number): Point => {
  const center = teamPoint(team, teamIndex, teamCount).x;
  return { x: center + (agentIndex - (team.agents.length - 1) / 2) * 220, y: 650 };
};
const connect = (a: Point, b: Point) => `M ${a.x} ${a.y} C ${a.x} ${(a.y + b.y) / 2}, ${b.x} ${(a.y + b.y) / 2}, ${b.x} ${b.y}`;
const text = (values: string[] | undefined) => values?.length ? values.join(" · ") : "Not specified in this example";

function DetailSection({ title, inherited, children }: { title: string; inherited?: boolean; children: ReactNode }) {
  return <section className="graph-detail-section"><h3>{title}<span>{inherited ? "INHERITED" : "CONFIGURED HERE"}</span></h3>{children}</section>;
}
function DetailRows({ rows }: { rows: [string, ReactNode][] }) {
  return <dl className="graph-detail-rows">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? "Not specified in this example"}</dd></div>)}</dl>;
}

export default function ConfigGraph() {
  const [selected, setSelected] = useState<Selection>({ kind: "enterprise" });
  const [camera, setCamera] = useState({ x: GRAPH_WIDTH / 2 - enterprisePoint.x * 0.78, y: HEIGHT / 2 - enterprisePoint.y * 0.78, zoom: 0.78 });
  const [dragging, setDragging] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const dragOrigin = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const selectedId = selected.kind === "enterprise" ? "enterprise" : selected.kind === "team" ? `team:${selected.team.name}` : `agent:${selected.team.name}:${selected.agent.name}`;
  const allPoints = useMemo(() => {
    const points: Point[] = [enterprisePoint];
    exampleSetup.teams.forEach((team, ti) => {
      points.push(teamPoint(team, ti, exampleSetup.teams.length));
      team.agents.forEach((_, ai) => points.push(agentPoint(team, ti, ai, exampleSetup.teams.length)));
    });
    return points;
  }, []);

  function viewPoint(event: { clientX: number; clientY: number }) {
    const svg = svgRef.current;
    const matrix = svg?.getScreenCTM();
    if (!svg || !matrix) return { x: 0, y: 0 };
    const point = svg.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
    const converted = point.matrixTransform(matrix.inverse());
    return { x: converted.x, y: converted.y };
  }
  function fit() {
    const minX = Math.min(...allPoints.map(p => p.x)) - 140;
    const maxX = Math.max(...allPoints.map(p => p.x)) + 140;
    const minY = Math.min(...allPoints.map(p => p.y)) - 100;
    const maxY = Math.max(...allPoints.map(p => p.y)) + 90;
    const zoom = Math.min(GRAPH_WIDTH / (maxX - minX), HEIGHT / (maxY - minY), 0.9);
    setCamera({ zoom, x: GRAPH_WIDTH / 2 - ((minX + maxX) / 2) * zoom, y: HEIGHT / 2 - ((minY + maxY) / 2) * zoom });
  }
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

  const selectedTitle = selected.kind === "enterprise" ? exampleSetup.enterprise.name : selected.kind === "team" ? selected.team.name : selected.agent.name;
  const selectedLayer = selected.kind === "enterprise" ? "Enterprise layer" : selected.kind === "team" ? "Team layer" : "Agent layer";
  return <div className="hierarchy-workspace">
    <section className="graph-panel" aria-label="Configuration hierarchy graph">
      <div className="graph-toolbar"><div><span className="crumb">Configuration hierarchy</span><div className="graph-count">1 enterprise <b>·</b> {exampleSetup.teams.length} teams <b>·</b> {exampleSetup.teams.reduce((n, team) => n + team.agents.length, 0)} agents</div></div><div className="camera-controls"><button type="button" aria-label="Zoom out" onClick={() => zoomAt(camera.zoom / 1.2)}>−</button><span>{Math.round(camera.zoom * 100)}%</span><button type="button" aria-label="Zoom in" onClick={() => zoomAt(camera.zoom * 1.2)}>+</button><button type="button" aria-label="Fit graph to view" onClick={fit}>Fit</button></div></div>
      <div className="graph-viewport"><svg ref={svgRef} viewBox={`0 0 ${GRAPH_WIDTH} ${HEIGHT}`} className={`hierarchy-svg ${dragging ? "is-dragging" : ""}`} role="group" aria-label="Interactive enterprise, team, and agent hierarchy" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} onWheel={onWheel}>
        <g className="camera-layer" style={{ transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.zoom})` }}>
          {exampleSetup.teams.map((team, ti) => {
            const tp = teamPoint(team, ti, exampleSetup.teams.length);
            const agentPoints = team.agents.map((_, ai) => agentPoint(team, ti, ai, exampleSetup.teams.length));
            const left = agentPoints.length ? Math.min(...agentPoints.map(point => point.x)) : tp.x;
            const right = agentPoints.length ? Math.max(...agentPoints.map(point => point.x)) : tp.x;
            const branch = `M ${tp.x} ${tp.y + 50} V 500 M ${left} 500 H ${right}`;
            return <g key={`edges-${team.name}`}>
              <path id={`enterprise-${team.name}`} className="graph-edge graph-edge-enterprise" d={connect({ x: enterprisePoint.x, y: enterprisePoint.y + 58 }, { x: tp.x, y: tp.y - 51 })} />
              <path className="graph-edge graph-edge-team" d={branch} />
              {team.agents.map((agent, ai) => { const ap = agentPoints[ai]; const drop = `M ${ap.x} 500 V ${ap.y - 42}`; return <path key={`edge-${agent.name}`} className="graph-edge graph-edge-team" d={drop} />; })}
            </g>;
          })}
          <g className={`graph-node enterprise-node ${selectedId === "enterprise" ? "is-selected" : ""}`} role="button" tabIndex={0} aria-label={`Select enterprise ${exampleSetup.enterprise.name}`} aria-pressed={selectedId === "enterprise"} onPointerDown={e => e.stopPropagation()} onClick={() => choose({ kind: "enterprise" }, enterprisePoint)} onKeyDown={e => nodeKey(e, { kind: "enterprise" }, enterprisePoint)}>
            <rect x={enterprisePoint.x - 150} y={enterprisePoint.y - 57} width="300" height="114" rx="4" /><text className="node-kicker" x={enterprisePoint.x} y={enterprisePoint.y - 22} textAnchor="middle">ENTERPRISE LAYER</text><text className="node-name enterprise-name" x={enterprisePoint.x} y={enterprisePoint.y + 13} textAnchor="middle">{exampleSetup.enterprise.name}</text><text className="node-meta" x={enterprisePoint.x} y={enterprisePoint.y + 36} textAnchor="middle">GOVERNS ALL TEAMS</text>
          </g>
          {exampleSetup.teams.map((team, ti) => {
            const tp = teamPoint(team, ti, exampleSetup.teams.length); const teamId = `team:${team.name}`; const teamSelection: Selection = { kind: "team", team };
            return <g key={teamId}>
              <g className={`graph-node team-node ${selectedId === teamId ? "is-selected" : ""}`} role="button" tabIndex={0} aria-label={`Select team ${team.name}`} aria-pressed={selectedId === teamId} onPointerDown={e => e.stopPropagation()} onClick={() => choose(teamSelection, tp)} onKeyDown={e => nodeKey(e, teamSelection, tp)}>
                <rect x={tp.x - 122} y={tp.y - 50} width="244" height="100" rx="4" /><text className="node-kicker" x={tp.x} y={tp.y - 17} textAnchor="middle">TEAM LAYER</text><text className="node-name" x={tp.x} y={tp.y + 10} textAnchor="middle">{team.name}</text><text className="node-meta" x={tp.x} y={tp.y + 31} textAnchor="middle">{team.agents.length} AGENTS · {team.members.length} MEMBERS</text>
              </g>
              {team.agents.map((agent, ai) => {
                const ap = agentPoint(team, ti, ai, exampleSetup.teams.length); const agentId = `agent:${team.name}:${agent.name}`; const agentSelection: Selection = { kind: "agent", team, agent };
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
    <aside className="graph-details" aria-live="polite"><header><div className="crumb">{selectedLayer}</div><h2>{selectedTitle}</h2><p>From spec/examples · the files this deployment was seeded with</p></header>
      {selected.kind === "enterprise" && <>
        <DetailSection title="Enterprise policy"><DetailRows rows={[["Administrators", text(exampleSetup.enterprise.admins)], ["System prompt", exampleSetup.enterprise.prompt], ["Allowed triggers", text(exampleSetup.enterprise.triggers)]]} /></DetailSection>
        <DetailSection title="Models and evaluation"><DetailRows rows={[["Model allowlist", text(exampleSetup.enterprise.models.allow)], ["Judge", `${exampleSetup.enterprise.evaluation.judge_model} · ${exampleSetup.enterprise.evaluation.votes} votes · ${(exampleSetup.enterprise.evaluation.min_agreement * 100).toFixed(0)}% minimum agreement`], ["Requirements", `${exampleSetup.enterprise.evaluation.min_cases} · tool coverage ${exampleSetup.enterprise.evaluation.tool_coverage ? "required" : "optional"}`]]} /></DetailSection>
        <DetailSection title="Security guardrails"><DetailRows rows={[["Guardrails", text(exampleSetup.enterprise.guardrails)], ["Observability", text(exampleSetup.enterprise.observability)], ["Trace retention", `${exampleSetup.enterprise.trace_retention_days} days`]]} /></DetailSection>
        <DetailSection title="Budgets and limits"><DetailRows rows={[["Monthly budget", `$${exampleSetup.enterprise.limits.usd_per_month}`], ["Per-run cap", `$${exampleSetup.enterprise.limits.usd_per_run.toFixed(2)}`], ["Execution", `${exampleSetup.enterprise.limits.max_steps} steps · ${exampleSetup.enterprise.limits.timeout_seconds}s · depth ${exampleSetup.enterprise.limits.max_call_depth}`]]} /></DetailSection>
        <DetailSection title="Connector permissions"><DetailRows rows={[["Registered grants", text(exampleSetup.enterprise.connectors)], ["Memory", text(exampleSetup.enterprise.memory)]]} /></DetailSection>
      </>}
      {selected.kind === "team" && <>
        <DetailSection title="Team layer"><DetailRows rows={[["Description", selected.team.description], ["Members", text(selected.team.members)], ["Approvers", text(selected.team.approvers)], ["Team prompt", selected.team.prompt]]} /></DetailSection>
        <DetailSection title="Inherited enterprise policy" inherited><DetailRows rows={[["Allowed models", text(selected.team.models.allow)], ["Secret and PII redaction", "Required · cannot be disabled"], ["Audit and trace payloads", "Audit on · payloads redacted · 30-day retention"], ["Per-run ceiling", `$${exampleSetup.enterprise.limits.usd_per_run.toFixed(2)} maximum inherited`]]} /></DetailSection>
        <DetailSection title="Team grants and limits"><DetailRows rows={[["Connector scope", text(selected.team.connectors)], ["Team guardrails", text(selected.team.guardrails)], ["Team budget", `$${selected.team.limits.usd_per_month} / month · ${selected.team.limits.max_concurrent_runs} concurrent runs`], ["Memory buckets", text(selected.team.memory)]]} /></DetailSection>
        <DetailSection title="Available agents"><div className="graph-agent-list">{selected.team.agents.map(agent => <button key={agent.name} onClick={() => choose({ kind: "agent", team: selected.team, agent }, agentPoint(selected.team, exampleSetup.teams.indexOf(selected.team), selected.team.agents.indexOf(agent), exampleSetup.teams.length))}>{agent.name}<span>{agent.model}</span></button>)}</div></DetailSection>
      </>}
      {selected.kind === "agent" && <>
        <DetailSection title="Agent configuration"><DetailRows rows={[["File", `${selected.agent.name}.agent · ${selected.agent.version}`], ["Example origin", selected.agent.source], ["Purpose", selected.agent.purpose], ["Task prompt", selected.agent.prompt]]} /></DetailSection>
        <DetailSection title="Inherited policies" inherited><DetailRows rows={[["Enterprise", "Secret/PII redaction · audit logging · redacted traces"], ["Team", `${selected.team.name} connector grants, channel/repository scopes, model allowlist and approval rules`], ["Effective model constraint", text(selected.team.models.allow)], ["Budgets", `${selected.team.limits.usd_per_month} monthly team budget · $${exampleSetup.enterprise.limits.usd_per_run.toFixed(2)} enterprise per-run ceiling`]]} /></DetailSection>
        <DetailSection title="Model, tools and scopes"><DetailRows rows={[["Model", selected.agent.model], ["Fallback", selected.agent.fallback], ["Tool allowlist", text(selected.agent.tools)], ["Resource scopes", text(selected.agent.resources)]]} /></DetailSection>
        <DetailSection title="Execution and memory"><DetailRows rows={[["Budget", selected.agent.budget], ["Triggers", text(selected.agent.triggers)], ["Memory", text(selected.agent.memory)]]} /></DetailSection>
        <DetailSection title="Evaluation, guardrails and observability"><DetailRows rows={[["Evaluation", text(selected.agent.evaluation)], ["Guardrails", text(selected.agent.guardrails)], ["Observability", text(selected.agent.observability)]]} /></DetailSection>
      </>}
      <div className="graph-detail-foot">Read-only. Team and enterprise layers are edited by enterprise admins, and every change applies on each agent’s next run.</div>
    </aside>
  </div>;
}
