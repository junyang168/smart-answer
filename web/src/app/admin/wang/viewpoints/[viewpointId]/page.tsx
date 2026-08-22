import { Suspense } from "react";
import { ViewpointDetail } from "./ViewpointDetail";

export default function ViewpointDetailPage() {
  return <Suspense fallback={<p className="py-10 text-sm text-slate-500">载入观点详情…</p>}><ViewpointDetail /></Suspense>;
}
