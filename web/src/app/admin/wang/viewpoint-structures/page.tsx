import { Suspense } from "react";
import { StructureExplorer } from "./StructureExplorer";

export default function ViewpointStructuresPage() {
  return (
    <Suspense fallback={<p className="py-10 text-sm text-slate-500">载入中心结构…</p>}>
      <StructureExplorer />
    </Suspense>
  );
}
