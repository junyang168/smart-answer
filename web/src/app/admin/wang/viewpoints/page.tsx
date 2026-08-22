import { Suspense } from "react";
import { ViewpointExplorer } from "./ViewpointExplorer";

export default function ViewpointsPage() {
  return <Suspense fallback={<p className="py-10 text-sm text-slate-500">载入观点主数据…</p>}><ViewpointExplorer /></Suspense>;
}
