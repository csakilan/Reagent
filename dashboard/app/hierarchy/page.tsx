"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { hierarchy } from "../../lib/api";
import { useDemoUser, useLive } from "../../lib/live";
import { Failure, PageHead, Skeleton } from "../ui";
import ConfigGraph, { type Setup } from "./config-graph";

function Hierarchy() {
  const user = useDemoUser();
  const agent = useSearchParams().get("agent");
  // No recorded fallback on purpose: the tree is filtered per caller on the server,
  // and a bundled copy would ship every team's configuration to every browser.
  const [state] = useLive<Setup>(user && `hierarchy:${user}`, () => hierarchy());
  return <>
    <PageHead crumb="Policy / Hierarchy" title="Policy hierarchy"
      subtitle={state.phase === "ready" && !state.data.viewer.is_enterprise_admin
        ? <>What <strong>{state.data.viewer.team}</strong> can see: your team and its agents, under an enterprise layer whose rules apply but stay hidden. Select an agent to see its full policy.</>
        : "Enterprise, then teams, then agents. Each layer can only tighten the one above. Select an agent to see its full policy."} />
    {state.phase === "loading" && <Skeleton rows={10} columns={3} label="Loading hierarchy" />}
    <Failure state={state} />
    {state.phase === "ready" && <ConfigGraph key={`${user}:${agent ?? ""}`} setup={state.data} initialAgent={agent} />}
  </>;
}

export default function HierarchyPage() {
  return <Suspense fallback={<Skeleton rows={10} columns={3} label="Loading hierarchy" />}><Hierarchy /></Suspense>;
}
