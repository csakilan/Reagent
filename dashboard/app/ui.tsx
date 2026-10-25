import type { ReactNode } from "react";
import type { Rejection } from "../lib/api";
import type { Live } from "../lib/live";

// The five hand-drawn icons. No icon library, by design.
const paths = {
  expand: "M4.5 6.2c1.2 1.3 2.3 2.5 3.5 3.7 1.2-1.2 2.3-2.4 3.5-3.6",
  collapse: "M4.6 9.8c1.1-1.2 2.3-2.4 3.4-3.6 1.2 1.2 2.3 2.4 3.5 3.5",
  pass: "M3.6 8.4c1.1.9 2 1.9 2.9 2.9 1.9-2.6 3.8-4.9 5.9-7",
  fail: "M4.3 4.4c2.5 2.3 4.9 4.8 7.3 7.2M11.7 4.2c-2.4 2.6-4.9 5-7.4 7.5",
  pending: "M8 3.1c2.8-.1 4.9 2 4.9 4.9 0 2.8-2.2 5-4.9 4.9-2.8 0-4.9-2.2-4.9-4.9M8 5.3v2.9l1.9 1.2",
} as const;
export type IconName = keyof typeof paths;

export function Icon({ name, label }: { name: IconName; label?: string }) {
  return <svg className={`icon icon-${name}`} viewBox="0 0 16 16" role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
    <path d={paths[name]} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

export function Verdict({ kind, children }: { kind: "pass" | "fail" | "pending"; children: ReactNode }) {
  return <span className={`verdict verdict-${kind}`}><Icon name={kind} />{children}</span>;
}

export function PageHead({ crumb, title, subtitle, children }: { crumb: string; title: ReactNode; subtitle?: ReactNode; children?: ReactNode }) {
  return <header className="page-head">
    <div className="page-title"><div className="crumb">{crumb}</div><h1>{title}</h1>{subtitle && <p className="subtitle">{subtitle}</p>}</div>
    {children && <div className="page-actions">{children}</div>}
  </header>;
}

export function SourceTag<T>({ state }: { state: Live<T> }) {
  if (state.phase !== "ready") return null;
  return state.source === "live"
    ? <span className="source-tag" title="Fetched from the runtime API just now">Live</span>
    : <span className="source-tag source-fixture" title="The runtime API could not answer, so a response recorded from it is shown">Recorded · {state.note}</span>;
}

export function Skeleton({ rows = 6, columns = 3, label = "Loading" }: { rows?: number; columns?: number; label?: string }) {
  return <div className="skeleton-table" role="status" aria-label={label}>
    {Array.from({ length: rows }, (_, row) => <div className="skeleton-row" key={row} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
      {Array.from({ length: columns }, (_, column) => <span key={column} style={{ width: `${45 + ((row * 7 + column * 13) % 50)}%` }} />)}
    </div>)}
  </div>;
}

export function Rejections({ items, title = "Rejected" }: { items: Rejection[]; title?: string }) {
  if (!items.length) return null;
  return <section className="rejections" aria-label={title}>
    <div className="rejections-head"><Icon name="fail" />{title}<span>{items.length}</span></div>
    {items.map((item, index) => <div className="rejection" key={`${item.field}-${index}`}>
      <div className="rejection-meta"><code>{item.code}</code><span>field <code>{item.field}</code></span><span>blocked by <b>{item.blocked_by}</b></span></div>
      <p>{item.message}</p>
    </div>)}
  </section>;
}

export function Failure<T>({ state, notFound }: { state: Live<T>; notFound?: ReactNode }) {
  if (state.phase !== "error") return null;
  if (state.rejections.length) return <Rejections items={state.rejections} title={`Refused · HTTP ${state.status}`} />;
  if (state.status === 404 && notFound) return <div className="notice">{notFound}</div>;
  return <div className="notice notice-denied"><b>{state.status ? `HTTP ${state.status}` : "No connection"}</b> {state.detail}</div>;
}

const REDACTION = /(\[REDACTED(?::[a-z0-9_]+)?\])/gi;

/** Pretty JSON with every redaction marker made visible. */
export function Payload({ value, compact }: { value: unknown; compact?: boolean }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (!text || text === "{}" || text === "null") return null;
  return <pre className={`payload ${compact ? "payload-compact" : ""}`}>{text.split(REDACTION).map((part, index) =>
    index % 2 ? <mark className="redacted" key={index}>{part}</mark> : part)}</pre>;
}

export function KeyValues({ rows }: { rows: [string, ReactNode][] }) {
  return <dl className="kv">{rows.filter(([, value]) => value !== undefined && value !== null && value !== "").map(([label, value]) =>
    <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

export const usd = (value: number | null | undefined) => value === null || value === undefined ? "—" : `$${value < 0.01 ? value.toFixed(4) : value.toFixed(2)}`;
export const pct = (value: number | null | undefined) => value === null || value === undefined ? "—" : `${Math.round(value * 1000) / 10}%`;
export const when = (iso: string | null | undefined) => iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export function relative(iso: string | null | undefined, now = Date.now()) {
  if (!iso) return "—";
  const minutes = Math.round((new Date(iso).getTime() - now) / 60000);
  const span = Math.abs(minutes) < 60 ? `${Math.abs(minutes)}m` : `${Math.round(Math.abs(minutes) / 6) / 10}h`;
  return minutes >= 0 ? `in ${span}` : `${span} ago`;
}
