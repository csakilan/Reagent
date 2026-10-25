import { Suspense } from "react";
import TraceView from "./trace-view";
import { Skeleton } from "../ui";

export default function TracePage() {
  return <Suspense fallback={<Skeleton rows={10} columns={5} />}><TraceView /></Suspense>;
}
