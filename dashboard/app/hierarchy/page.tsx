"use client";

import { useDemoUser, useLive } from "../../lib/live";
import { Failure, PageHead, Skeleton } from "../ui";
import ConfigGraph, { type Setup } from "./config-graph";
import { hierarchy } from "../../lib/api";

export default function HierarchyPage() {
  const user = useDemoUser();
  // No recorded fallback on purpose: the tree is filtered per caller on the server,
  // and a bundled copy would ship every team's configuration to every browser.
  const [state] = useLive<Setup>(user && `hierarchy:${user}`, () => hierarchy());
  return <>
    <PageHead crumb="Policy / Hierarchy" title="Configuration hierarchy"
      subtitle={state.phase === "ready" && !state.data.viewer.is_enterprise_admin
        ? <>What <strong>{state.data.viewer.team}</strong> can see: your team and its agents, beneath an enterprise layer whose rules apply but stay hidden.</>
        : "One enterprise layer, team layers beneath it, agents beneath those. Each layer can only tighten the one above."} />
    {state.phase === "loading" && <Skeleton rows={10} columns={3} label="Loading hierarchy" />}
    <Failure state={state} />
    {state.phase === "ready" && <ConfigGraph key={user ?? ""} setup={state.data} />}
  </>;
}
