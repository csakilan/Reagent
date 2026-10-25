import Link from "next/link";
import exampleSetup from "../../fixtures/example-setup.json";

const enterpriseRows: [string, string][] = [
  ["Admins", exampleSetup.enterprise.admins.join(", ")],
  ["Models", exampleSetup.enterprise.models.join(", ")],
  ["Connectors", exampleSetup.enterprise.connectors.join(" · ")],
  ["Triggers", exampleSetup.enterprise.triggers.join(", ")],
  ["Guardrails", exampleSetup.enterprise.guardrails.join(" · ")],
  ["Run limits", `$${exampleSetup.enterprise.limits.usd_per_run.toFixed(2)} / run · ${exampleSetup.enterprise.limits.max_steps} steps · ${exampleSetup.enterprise.limits.timeout_seconds}s timeout`],
  ["Trace retention", `${exampleSetup.enterprise.trace_retention_days} days · redacted payloads`],
];

export default function ExamplesPage() {
  return <div className="shell"><aside className="rail"><div className="brand">ReAgent<small>POLICY CONTROL PLANE</small></div><nav className="nav" aria-label="Main navigation"><Link href="/">Resolved view</Link><Link href="/traces">Trace viewer</Link><Link href="/publish">Publish pipeline</Link><Link className="active" href="/agents">Collection</Link><Link href="/approvals">Approvals</Link></nav><div className="user-switch">LOCAL FIXTURES<span>Example data only</span></div></aside><main className="examples-page">
    <header className="page-head"><div><div className="eyebrow">Configuration / Example setup</div><h1>Enterprise and team setup</h1><p className="subtitle">A sample policy hierarchy with two teams and three agents per team.</p></div><Link className="button" href="/agents">Back to collection</Link></header>
    <div className="example-banner">{exampleSetup.label}. These records are display fixtures; they do not create or change platform configuration.</div>
    <section className="example-section"><div className="example-title"><div><div className="eyebrow layer-enterprise-text">Enterprise layer</div><h2>{exampleSetup.enterprise.name}</h2></div><span className="example-origin">EXAMPLE POLICY</span></div><div className="resolved example-table"><div className="example-table-head"><span>Setting</span><span>Enterprise value</span></div>{enterpriseRows.map(([key, value]) => <div className="example-table-row" key={key}><span>{key}</span><span>{value}</span></div>)}</div></section>
    {exampleSetup.teams.map((team) => <section className="example-section" key={team.name}><div className="example-title"><div><div className="eyebrow layer-team-text">Team layer</div><h2>{team.name}</h2><p className="subtitle">{team.description}</p></div><span className="example-origin">TEAM EXAMPLE</span></div>
      <div className="resolved example-table"><div className="example-table-head"><span>Setting</span><span>{team.name} value</span></div>
        <div className="example-table-row"><span>Members · approvers</span><span>{team.members.join(", ")} · Approvers: {team.approvers.join(", ")}</span></div>
        <div className="example-table-row"><span>Allowed models</span><span>{team.models.join(", ")}</span></div>
        <div className="example-table-row"><span>Connector grants</span><span>{team.connectors.join(" · ")}</span></div>
        <div className="example-table-row"><span>Guardrails</span><span>{team.guardrails.join(" · ")}</span></div>
        <div className="example-table-row"><span>Team limits</span><span>${team.limits.usd_per_month} / month · {team.limits.max_concurrent_runs} concurrent runs</span></div>
      </div>
      <div className="example-agent-heading"><div><div className="eyebrow">Agent layer</div><h3>Agents in {team.name}</h3></div><span>3 EXAMPLE AGENTS</span></div>
      <div className="resolved example-table agent-example-table"><div className="example-agent-head"><span>Agent · version</span><span>Purpose</span><span>Model · budget</span><span>Tool allowlist</span></div>{team.agents.map((agent) => <article className="example-agent-row" key={agent.name}><div><strong>{agent.name}</strong><small>{agent.version} · {agent.source}</small></div><p>{agent.purpose}</p><span className="example-mono">{agent.model}<small>{agent.budget}</small></span><span className="example-mono">{agent.tools.map((tool) => <small key={tool}>{tool}</small>)}</span></article>)}</div>
    </section>)}
  </main></div>;
}
