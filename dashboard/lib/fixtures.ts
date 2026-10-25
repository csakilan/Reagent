// Responses recorded from the real runtime API (fake model, fake connectors), used only
// when that API cannot answer. Every screen labels which source it is showing.
import resolved from "../fixtures/recorded/resolved.json";
import runs from "../fixtures/recorded/runs.json";
import approvals from "../fixtures/recorded/approvals.json";
import publishRun from "../fixtures/recorded/publish_run.json";
import dryRun from "../fixtures/recorded/dry_run.json";
import agents from "../fixtures/recorded/agents.json";
import agentSpecs from "../fixtures/recorded/agent_specs.json";
import type { AgentSummary, Approval, PublishRun, RunResult, Trace } from "./api";
import type { Resolved } from "./resolved";

export const recordedResolved = (user: string, agent: string) => (resolved as unknown as Record<string, Resolved>)[`${user}/${agent}`];
export const recordedRun = (id: number) => (runs.runs as unknown as Record<string, RunResult>)[String(id)];
export const recordedTrace = (id: number) => (runs.traces as unknown as Record<string, Trace>)[String(id)];
export const recordedApprovals = () => approvals as unknown as { approvals: Approval[] };
export const recordedPublishRun = (id: number) => (id === (publishRun as { id: number }).id ? (publishRun as unknown as PublishRun) : undefined);
export const recordedDryRun = () => dryRun as unknown as { output?: Record<string, unknown>; tool_calls?: string[]; passed?: boolean; cost_usd?: number };
export const recordedAgents = () => agents as unknown as { agents: AgentSummary[] };
export const recordedSpec = (name: string) => (agentSpecs as Record<string, string>)[name];
export const RECORDED_RUN_ID = 1;
export const RECORDED_PUBLISH_RUN_ID = (publishRun as { id: number }).id;
