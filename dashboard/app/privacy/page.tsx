import Link from "next/link";
import { PageHead } from "../ui";

const logged: [string, string][] = [
  ["Run record", "Run and parent run ids, agent and version, team, trigger, acting user, model, token counts, cost, latency, step count, status."],
  ["Trace events", "Model calls, tool calls with the action and resource touched, allow or deny outcome, approvals and their decisions, retries, errors, redaction hits and policy violations."],
  ["Approvals", "The requested action, its risk class, redacted arguments, who decided, when, and whether it expired."],
  ["Publish pipelines", "Stage results, generated and reviewed eval cases, reviewer decisions and rejection reasons, eval reports."],
];

const never: [string, string][] = [
  ["Secrets", "API keys, tokens and credentials matched by the redaction patterns are replaced with a [REDACTED] marker before anything is written."],
  ["Personal data", "Values matched by the enterprise PII rules are stripped from model output, tool results, traces and logs."],
  ["Full tool results", "A tool call stores a short, redacted summary, never the full response."],
];

export default function PrivacyPage() {
  return <div className="prose-page">
    <PageHead crumb="Governance / Privacy" title="Privacy and trace retention" subtitle="ReAgent is self-hosted. The organisation running this instance owns its database, model endpoint and retention settings." />
    <section>
      <h2>What the platform logs</h2>
      <dl className="kv kv-wide">{logged.map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}</dl>
    </section>
    <section>
      <h2>What redaction strips</h2>
      <p>Redaction wraps model output, tool results, traces and logs, and it runs before storage, so the trace API has nothing left to redact. Enterprise redaction is a locked safety flag: no team or agent layer can switch it off.</p>
      <dl className="kv kv-wide">{never.map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}</dl>
    </section>
    <section>
      <h2>Retention</h2>
      <p>Run traces are retained for <b>30 days</b>. The operator of this instance controls the database and is responsible for its backups and for any longer retention it configures.</p>
    </section>
    <section>
      <h2>Who can see what</h2>
      <p>Members see only their own team&apos;s agents, runs and approvals. Enterprise-layer values are returned as locked rows with their source tag and no value. The server applies every filter; the dashboard never hides data on its own. See <Link href="/hierarchy">the policy hierarchy</Link>.</p>
    </section>
  </div>;
}
