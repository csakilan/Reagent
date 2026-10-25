import { Suspense } from "react";
import Collection from "../collection";
import { Skeleton } from "../ui";

export default function CollectionPage() {
  return <Suspense fallback={<Skeleton rows={6} columns={6} label="Loading agents" />}><Collection /></Suspense>;
}
