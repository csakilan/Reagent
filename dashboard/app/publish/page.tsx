import { Suspense } from "react";
import PublishView from "./publish-view";
import { Skeleton } from "../ui";

export default function PublishPage() {
  return <Suspense fallback={<Skeleton rows={8} columns={4} />}><PublishView /></Suspense>;
}
