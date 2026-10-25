/**
 * Recorded fallbacks, deliberately empty.
 *
 * These used to import recorded API responses (fixtures/recorded/*.json) so screens
 * could render with the API down. Imported here, that data was compiled into the
 * JavaScript every browser downloads: an admin's resolved view with the enterprise
 * prompt, and every team's agent specs, readable by any member from dev tools.
 * Visibility is enforced on the server (SPEC.md §2), so nothing the server would
 * filter may ship in the client. With the API down, screens show the error instead.
 *
 * The signatures stay so callers need no change; every one returns nothing.
 */
import type { AgentSummary, Approval, PublishRun, RunResult, Trace } from "./api";
import type { Resolved } from "./resolved";

export const recordedResolved = (_user: string, _agent: string): Resolved | undefined => undefined;
export const recordedRun = (_id: number): RunResult | undefined => undefined;
export const recordedTrace = (_id: number): Trace | undefined => undefined;
export const recordedApprovals = (): { approvals: Approval[] } => ({ approvals: [] });
export const recordedPublishRun = (_id: number): PublishRun | undefined => undefined;
export const recordedDryRun = (): undefined => undefined;
export const recordedAgents = (): { agents: AgentSummary[] } | undefined => undefined;
export const recordedSpec = (_name: string): string | undefined => undefined;
export const RECORDED_RUN_ID = 0;
export const RECORDED_PUBLISH_RUN_ID = 0;
