import type { Metadata } from "next";
import { SermonCatalog } from "./SermonCatalog";

export const metadata: Metadata = {
  title: "讲道 | 王教授文库后台",
  robots: { index: false, follow: false },
};

export default function SermonsPage() {
  return (
    <main className="space-y-4 pb-10">
      <header className="border-b border-slate-200 pb-4">
        <h1 className="text-3xl font-black tracking-tight text-slate-950">讲道</h1>
        <p className="mt-2 max-w-3xl text-sm leading-7 text-slate-600">
          按圣经书卷或讲道主题找一篇讲道或母本。打开后左边是原文，右边是这一篇里教授的主张和它们之间的推理关系。
        </p>
      </header>
      <SermonCatalog />
    </main>
  );
}
