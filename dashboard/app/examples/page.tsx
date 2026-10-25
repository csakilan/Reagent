import Link from "next/link";
import ConfigGraph from "./config-graph";
import exampleSetup from "../../fixtures/example-setup.json";

export default function ExamplesPage() {
  return <div className="shell"><aside className="rail"><div className="brand">ReAgent<small>POLICY CONTROL PLANE</small></div><nav className="nav" aria-label="Main navigation"><Link href="/">Resolved view</Link><Link href="/traces">Trace viewer</Link><Link href="/publish">Publish pipeline</Link><Link className="active" href="/agents">Collection</Link><Link href="/approvals">Approvals</Link></nav><div className="user-switch">LOCAL FIXTURES<span>Example data only</span></div></aside><main className="examples-page">
    <header className="page-head"><div><div className="eyebrow">Configuration / Collection</div><h1>Configuration hierarchy</h1><p className="subtitle">Explore inherited enterprise, team and agent policy.</p></div><span className="example-origin">{exampleSetup.label}</span></header>
    <div className="example-banner">Display-only configuration fixtures. No platform settings are created or changed.</div>
    <ConfigGraph />
  </main></div>;
}
