"use client";

import Link from "next/link";
import { DEMO_USERS } from "../lib/api";
import { useDemoUser } from "../lib/live";
import { PageHead } from "./ui";

type Step = { title: string; body: string; href: string; action: string; who?: "approver" | "admin" };

const STEPS: Step[] = [
  { title: "See what your team is allowed to do",
    body: "Your team sits under the enterprise layer. Each layer can only tighten the one above it, so this is the outer edge of everything your agents can do: tools, models, budgets and guardrails.",
    href: "/hierarchy", action: "Open the hierarchy" },
  { title: "Create an agent",
    body: "Start from your team's base config, a blank agent that inherits the enterprise and team setup, or fork an agent your team already has.",
    href: "/agents?new=1", action: "New agent" },
  { title: "Edit and validate it",
    body: "An agent is one YAML file with no code and no secrets. The editor checks it as you type: ask for more than your team allows and it is refused, naming the field and the layer that blocked it.",
    href: "/agents", action: "Your agents" },
  { title: "Run it and read the trace",
    body: "Give it an input, like a repository and pull request, and watch every model call, tool call and sub-agent call. Blocked calls and redacted secrets are marked.",
    href: "/traces", action: "Run an agent" },
  { title: "Approve what needs a person",
    body: "Risky actions, like posting a comment, pause the run until a team approver says yes. No answer within the timeout counts as a no.",
    href: "/approvals", action: "Approvals", who: "approver" },
  { title: "Publish it",
    body: "Publishing runs a pipeline: validate, security review, generate tests, then a person reviews every test before evals run. It publishes only if the results meet the bar.",
    href: "/publish", action: "Publish pipeline" },
];

export default function GettingStarted() {
  const user = useDemoUser();
  const me = DEMO_USERS.find(item => item.id === user);
  const isApprover = me?.role === "Approver";
  return <>
    <PageHead crumb="Start" title="Getting started"
      subtitle={me ? <>Signed in as <b>{me.name}</b> · {me.role}{me.team ? <> in <b>{me.team}</b></> : null}. Six steps from nothing to a published agent.</> : "Six steps from nothing to a published agent."} />
    <ol className="start-steps">
      {STEPS.map((step, index) => <li key={step.title} className="start-step">
        <span className="start-index">{index + 1}</span>
        <div className="start-text">
          <h2>{step.title}</h2>
          <p>{step.body}</p>
          {step.who === "approver" && me && !isApprover && <p className="muted">You are not an approver, so you will see paused actions but not decide them.</p>}
        </div>
        <Link className={index === 1 ? "button primary" : "button"} href={step.href}>{step.action}</Link>
      </li>)}
    </ol>
    <p className="start-foot muted">ReAgent re-reads all three layers, enterprise, team and agent, on every run, so a policy change reaches every agent on its next run. <Link href="/privacy">What the platform logs</Link>.</p>
  </>;
}
