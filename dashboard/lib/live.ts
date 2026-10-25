"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { call, getDemoUser, type FetchResult, type Rejection } from "./api";

export type Live<T> =
  | { phase: "loading" }
  | { phase: "ready"; data: T; source: "live" | "fixture"; note?: string }
  | { phase: "error"; status: number; detail: string; rejections: Rejection[] };

type Options<T> = {
  /** Recorded response to show when the API is down, or does not serve this route yet. */
  fallback?: () => T | undefined;
};

/**
 * Fetches on mount and whenever `key` changes. Falls back to a recorded fixture only when
 * the API cannot answer at all; a real refusal (403, 404, 422) is shown as the refusal.
 */
export function useLive<T>(key: string | null, load: () => Promise<FetchResult>, options: Options<T> = {}) {
  const [state, setState] = useState<Live<T>>({ phase: "loading" });
  const [tick, setTick] = useState(0);
  const latest = useRef({ load, options });
  latest.current = { load, options };

  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    setState(current => (current.phase === "ready" && tick > 0 ? current : { phase: "loading" }));
    (async () => {
      const outcome = await call<T>(latest.current.load());
      if (cancelled) return;
      if (outcome.ok) { setState({ phase: "ready", data: outcome.data, source: "live" }); return; }
      const recorded = outcome.unreachable || outcome.missingRoute ? latest.current.options.fallback?.() : undefined;
      if (recorded !== undefined) {
        setState({ phase: "ready", data: recorded, source: "fixture", note: outcome.unreachable ? "API unreachable" : "Route not served yet" });
        return;
      }
      setState({ phase: "error", status: outcome.status, detail: outcome.detail, rejections: outcome.rejections });
    })();
    return () => { cancelled = true; };
  }, [key, tick]);

  const reload = useCallback(() => setTick(value => value + 1), []);
  return [state, reload] as const;
}

/** Reads the demo identity after hydration, so server and client render the same first frame. */
export function useDemoUser() {
  const [user, setUser] = useState<string | null>(null);
  useEffect(() => setUser(getDemoUser()), []);
  return user;
}
