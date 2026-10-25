export type Layer = "enterprise" | "team" | "agent";
export type NarrowedFrom = { from_layer?: string; value?: unknown; allow?: unknown; deny?: unknown; redacted?: boolean; declared?: string } & Record<string, unknown>;
export type Leaf = { value: unknown; source: string; redacted?: boolean; narrowed_from?: NarrowedFrom };
export type Resolved = {
  agent: string;
  version: string;
  level: string;
  team: string | null;
  resolved_at: string;
  layers?: string[];
  caller: { user: string; team: string | null; is_enterprise_admin: boolean };
  provenance: Record<string, unknown>;
  rejections: Array<{ code: string; field: string; blocked_by: string; message: string }>;
};
export type Row = { section: string; path: string; leaf: Leaf };

export const layerOf = (source: string | undefined): Layer =>
  source === "enterprise" ? "enterprise" : source?.startsWith("team") ? "team" : "agent";

export const layerName = (source: string | undefined) =>
  !source ? "Unknown" : source === "agent" ? "Agent" : source.startsWith("team:") ? `Team · ${source.slice(5)}` : source === "enterprise" ? "Enterprise" : source;

const isLeaf = (value: object): value is Leaf => "source" in value && "value" in value && typeof (value as Leaf).source === "string";

/** Every `{value, source}` leaf in the provenance tree, in document order. */
export function flatten(value: unknown, path = "", section = ""): Row[] {
  if (Array.isArray(value)) return value.flatMap((item, index) => flatten(item, `${path}[${index}]`, section));
  if (value && typeof value === "object") {
    if (isLeaf(value)) return [{ section: section || path, path, leaf: value }];
    return Object.entries(value).flatMap(([key, child]) => flatten(child, path ? `${path}.${key}` : key, section || key));
  }
  return [];
}

export function show(value: unknown): string {
  if (value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
