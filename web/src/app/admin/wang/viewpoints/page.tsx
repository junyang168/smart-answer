import type { Metadata } from "next";
import { Suspense } from "react";
import { ViewpointGraph } from "./ViewpointGraph";

export const metadata: Metadata = {
  title: "观点图 | 王教授文库后台",
  robots: { index: false, follow: false },
};

export default function ViewpointsPage() {
  return <Suspense fallback={<p className="py-10 text-sm text-slate-500">载入观点图…</p>}><ViewpointGraph /></Suspense>;
}
