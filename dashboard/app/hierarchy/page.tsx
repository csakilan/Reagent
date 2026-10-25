import Link from "next/link";
import ConfigGraph from "./config-graph";
import { PageHead } from "../ui";

export default function HierarchyPage() {
  return <>
    <PageHead crumb="Policy / Hierarchy" title="Configuration hierarchy" subtitle="One enterprise layer, team layers beneath it, agents beneath those. Each layer can only tighten the one above.">
      <Link className="button" href="/">Resolved view</Link>
    </PageHead>
    <ConfigGraph />
  </>;
}
