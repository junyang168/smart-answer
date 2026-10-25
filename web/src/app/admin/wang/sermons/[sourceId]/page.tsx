import type { Metadata } from "next";
import { Suspense } from "react";
import { SermonView } from "./SermonView";

export const metadata: Metadata = {
  title: "讲道 | 王教授文库后台",
  robots: { index: false, follow: false },
};

export default async function SermonPage(props: { params: Promise<{ sourceId: string }> }) {
  const { sourceId } = await props.params;
  return (
    <Suspense fallback={<p className="py-10 text-sm text-slate-500">载入讲道…</p>}>
      <SermonView sourceId={decodeURIComponent(sourceId)} />
    </Suspense>
  );
}
