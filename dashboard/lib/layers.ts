// Regroups the resolved provenance tree by the layer that set each value, so the view
// reads top-down the way the policy is built: enterprise, then team, then agent.
import { flatten, layerName, layerOf, type Layer, type Leaf } from "./resolved";

export const LAYER_ORDER: Layer[] = ["enterprise", "team", "agent"];

const SECTIONS: Record<string, string> = {
  use_case: "Purpose", prompt: "Instructions", tools: "Tools", model: "Model", budget: "Budget", limits: "Limits",
  triggers: "Triggers", guardrails: "Guardrails", memory: "Memory", sub_agents: "Sub-agents", observability: "Observability",
  failure: "Failure handling", evaluation: "Evaluation", ownership: "Ownership", locked: "Locked fields", input: "Input", output: "Output",
};

const LABELS: Record<string, string> = {
  "use_case.description": "Description", "use_case.when_to_use": "When to use", "use_case.desired_outcome": "Desired outcome",
  "prompt[]": "Instructions", "tools[]": "Tools", "model.id": "Model", "model.fallback": "Fallback model", "model.params": "Model parameters",
  "budget.usd_per_month": "Monthly budget", "budget.usd_per_run": "Per-run budget", "budget.max_steps": "Max steps", "budget.timeout_seconds": "Timeout",
  "limits.max_call_depth": "Max sub-agent depth", "limits.max_concurrent_runs": "Max concurrent runs", "triggers[]": "Triggers",
  "guardrails.redaction.secrets": "Redact secrets", "guardrails.redaction.pii": "Redact personal data", "guardrails.audit_log": "Audit log",
  "guardrails.trace_payloads": "Trace payloads", "guardrails.approval.require_for_risk": "Approval needed for risk", "guardrails.approval.timeout_minutes": "Approval timeout",
  "guardrails.approval.actions": "Actions needing approval", "guardrails.deny[]": "Denied actions", "memory[]": "Memory buckets", "sub_agents[]": "Sub-agents",
  "observability.metrics": "Custom metrics", "observability.alerts": "Alerts", "observability.log_retention_days": "Log retention", "observability.trace_payloads": "Trace payloads",
  "failure.retries": "Retries", "failure.retry_on": "Retry on", "failure.on_failure": "On failure", "evaluation.runs_per_case": "Runs per case",
  "evaluation.thresholds": "Pass thresholds", "evaluation.production": "Production sampling", "evaluation.cases[].case": "Eval cases",
  "evaluation.default_cases[].case": "Required eval cases", "ownership.owners": "Owners", "ownership.maintainers": "Maintainers",
  "locked[]": "Locked fields", input: "Input schema", output: "Output schema",
};

export type Setting = {
  key: string;            // path with list indices removed, e.g. guardrails.deny[]
  section: string;
  label: string;
  kind: "set" | "limit";  // limit = this layer's bound that a lower layer narrowed
  hidden: boolean;
  values: unknown[];
  paths: string[];
  notes: string[];
};
export type LayerGroup = { layer: Layer; source: string; settings: Setting[]; hidden: number };

const groupKey = (path: string) => path.replace(/\[\d+\]/g, "[]");
const humanize = (key: string) => {
  const last = key.replace(/\[\]/g, "").split(".").pop() ?? key;
  const words = last.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};
export const sectionTitle = (section: string) => SECTIONS[section] ?? humanize(section);
export const settingLabel = (key: string) => LABELS[key] ?? humanize(key);

/** Short text for a value, used inside sentences. */
export function brief(value: unknown, key = ""): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (key.includes("usd")) return `$${value.toFixed(2)}`;
    if (key.endsWith("seconds")) return `${value}s`;
    if (key.endsWith("minutes")) return `${value} min`;
    if (key.endsWith("days")) return `${value} days`;
    return String(value);
  }
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "string") return value.length > 60 ? `${value.slice(0, 57)}…` : value;
  if (Array.isArray(value)) return value.map(item => brief(item, key)).join(", ");
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 1) return brief(entries[0][1], entries[0][0]);
  return entries.map(([k, v]) => `${k} ${brief(v, k)}`).join(" · ");
}

/** The parent's bound, without the bookkeeping keys. */
function parentBound(parent: NonNullable<Leaf["narrowed_from"]>) {
  const rest: Record<string, unknown> = { ...parent };
  delete rest.from_layer; delete rest.redacted; delete rest.declared;
  const keys = Object.keys(rest);
  return keys.length === 1 && keys[0] === "value" ? rest.value : keys.length ? rest : undefined;
}

export function groupByLayer(provenance: Record<string, unknown>): LayerGroup[] {
  const rows = flatten(provenance);
  type Raw = Omit<Setting, "values" | "paths" | "notes" | "label"> & { layer: Layer; source: string; value: unknown; path: string; note?: string };
  const raw: Raw[] = [];

  for (const { section, path, leaf } of rows) {
    const key = groupKey(path);
    const parent = leaf.narrowed_from;
    let note: string | undefined;
    if (parent?.declared) note = `Resolved from ${parent.declared}`;
    else if (parent?.redacted) note = `Inside a limit ${layerName(parent.from_layer)} keeps hidden from you`;
    else if (parent) note = `Tightened from ${layerName(parent.from_layer)}'s ${brief(parentBound(parent), key)}`;
    raw.push({ layer: layerOf(leaf.source), source: leaf.source, key, section, kind: "set", hidden: Boolean(leaf.redacted), value: leaf.value, path, note });

    if (parent?.from_layer && !parent.declared) {
      // Describe the child only in the terms the parent bounded, e.g. a tool's actions.
      const bound = parentBound(parent);
      const childValue = bound && typeof bound === "object" && !Array.isArray(bound) && leaf.value && typeof leaf.value === "object" && !Array.isArray(leaf.value)
        ? Object.fromEntries(Object.keys(bound).map(k => [k, (leaf.value as Record<string, unknown>)[k]]))
        : leaf.value;
      const child = leaf.redacted ? "a hidden value" : brief(childValue, key);
      raw.push({
        layer: layerOf(parent.from_layer), source: parent.from_layer, key, section, kind: "limit", hidden: Boolean(parent.redacted),
        value: parentBound(parent), path, note: `${layerName(leaf.source)} tightened this to ${child}`,
      });
    }
  }

  // A value with no parent bound, whose key also appears higher up, was merged rather than narrowed.
  for (const item of raw) {
    if (item.kind !== "set" || item.note) continue;
    const above = LAYER_ORDER.slice(0, LAYER_ORDER.indexOf(item.layer)).reverse().find(layer => raw.some(other => other.layer === layer && other.key === item.key && other.kind === "set"));
    if (!above) continue;
    const name = layerName(raw.find(other => other.layer === above && other.key === item.key)?.source);
    item.note = item.key === "prompt[]" ? `Appended after ${name}'s instructions` : item.key.endsWith("deny[]") ? `Added to ${name}'s denylist` : `Combined with ${name}'s`;
  }

  return LAYER_ORDER.map(layer => {
    const mine = raw.filter(item => item.layer === layer);
    const settings: Setting[] = [];
    for (const item of mine) {
      const existing = settings.find(s => s.key === item.key && s.kind === item.kind && s.hidden === item.hidden);
      if (existing) {
        existing.values.push(item.value); existing.paths.push(item.path);
        if (item.note && !existing.notes.includes(item.note)) existing.notes.push(item.note);
      } else {
        settings.push({ key: item.key, section: item.section, label: settingLabel(item.key), kind: item.kind, hidden: item.hidden, values: [item.value], paths: [item.path], notes: item.note ? [item.note] : [] });
      }
    }
    const source = mine.find(item => item.kind === "set")?.source ?? mine[0]?.source ?? layer;
    return { layer, source, settings, hidden: settings.filter(s => s.hidden).length };
  });
}
