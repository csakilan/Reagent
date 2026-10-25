import createClient from "openapi-fetch";
import type { paths } from "./api-schema";

const baseUrl = process.env.NEXT_PUBLIC_REAGENT_API_URL ?? "http://localhost:8000";

// Keep this client at module scope: Next dev hot reload must not allocate one per request.
export const api = createClient<paths>({ baseUrl });
const readyHandoffs = new Set((process.env.NEXT_PUBLIC_READY_HANDOFFS ?? "").split(",").map(value => value.trim()));
export function isHandoffReady(id: string) { return readyHandoffs.has(id); }
let demoUser = "alice";
export function setDemoUser(user: string) {
  demoUser = user;
  if (typeof window !== "undefined") {
    window.sessionStorage.setItem("reagent-demo-user", user);
    window.location.reload();
  }
}
export function getDemoUser() {
  if (typeof window !== "undefined") demoUser = window.sessionStorage.getItem("reagent-demo-user") ?? demoUser;
  return demoUser;
}

export function resolvedAgent(name: string, user: string) {
  return api.GET("/v0/agents/{name}/resolved", {
    params: { path: { name } },
    headers: { "X-ReAgent-User": user },
  });
}

export function traceForRun(id: number, user: string) {
  return api.GET("/v0/runs/{id}/trace", {
    params: { path: { id } },
    headers: { "X-ReAgent-User": user },
  });
}

export function validateAgent(name: string, spec: string) {
  return api.POST("/v0/agents/{name}/validate", { params: { path: { name } }, body: { spec }, headers: { "X-ReAgent-User": demoUser } });
}

export function agentDetail(name: string) {
  return api.GET("/v0/agents/{name}", { params: { path: { name } }, headers: { "X-ReAgent-User": demoUser } });
}

export function publishRun(id: number) {
  return api.GET("/v0/publish-runs/{id}", { params: { path: { id } }, headers: { "X-ReAgent-User": demoUser } });
}

export function reviewCase(id: number, caseId: string, decision: "approve" | "edit" | "reject", reason?: string, editedCase?: Record<string, unknown>) {
  return api.POST("/v0/publish-runs/{id}/cases/{case}/review", {
    params: { path: { id, case: caseId } },
    body: { decision, reason, edited_case: editedCase as Record<string, never> | undefined },
    headers: { "X-ReAgent-User": demoUser },
  });
}

export function dryRunCase(id: number, caseId: string) {
  return api.POST("/v0/publish-runs/{id}/cases/{case}/dry-run", { params: { path: { id, case: caseId } }, headers: { "X-ReAgent-User": demoUser } });
}

export function continuePublish(id: number) {
  return api.POST("/v0/publish-runs/{id}/continue", { params: { path: { id } }, headers: { "X-ReAgent-User": demoUser } });
}

export function searchAgents(q?: string) {
  return api.GET("/v0/agents", { params: { query: q ? { q } : {} }, headers: { "X-ReAgent-User": demoUser } });
}

export function forkAgent(name: string, new_name: string) {
  return api.POST("/v0/agents/{name}/fork", { params: { path: { name } }, body: { new_name }, headers: { "X-ReAgent-User": demoUser } });
}

export function approvals(status: "pending" | "decided" | "all" = "pending") {
  return api.GET("/v0/approvals", { params: { query: { status } }, headers: { "X-ReAgent-User": demoUser } });
}

export function decideApproval(id: number, decision: "approved" | "rejected") {
  return api.POST("/v0/approvals/{id}", { params: { path: { id } }, body: { decision }, headers: { "X-ReAgent-User": demoUser } });
}
