import { Suspense } from "react";
import ResolvedView from "./resolved-view";
import { Skeleton } from "./ui";

export default function Home() {
  return <Suspense fallback={<Skeleton rows={12} />}><ResolvedView /></Suspense>;
}
