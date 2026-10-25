import Link from "next/link";
import { FileCheck2, HeartPulse, ShieldCheck, Wrench } from "lucide-react";
import { SermonOverview } from "../SermonOverview";
import { operationStageSummary } from "../operations-copy";

// Engineering's pages, off the colleagues' menu (#453): the sermon pipeline
// table that used to be the home page, and the pages it summarises.
const areas = [
  { href: "/admin/wang/health", title: "健康度", description: "整个语料的抽取质量分布，有没有该管的事", icon: HeartPulse },
  { href: "/admin/wang/library-audit", title: "独立审计", description: "文库各层的独立审计与要处理的后续", icon: ShieldCheck },
  { href: "/admin/canonical-repository", title: "出版单元", description: "canonical units 与公开索引", icon: FileCheck2 },
];

export default function WangRunsPage() {
  return (
    <main>
      <header className="overflow-hidden rounded-3xl bg-slate-950 px-6 py-7 text-white sm:px-9">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-sm font-bold tracking-wide text-indigo-300">运行</p>
            <h1 className="mt-2 text-3xl font-black tracking-tight">讲道进知识库：抽取总表</h1>
            <p className="mt-3 text-base leading-7 text-slate-300">
              一行一篇来源，{operationStageSummary}。每一格的状态与质量都来自执行记录表。
              这里给工程排错用；等人决定的事在「待处理」。
            </p>
          </div>
          <div className="flex items-center gap-3 rounded-2xl bg-white/10 px-4 py-3 text-sm text-slate-200">
            <Wrench className="h-5 w-5 text-slate-300" />工程用
          </div>
        </div>
      </header>

      <section className="mt-6 grid gap-3 sm:grid-cols-3">
        {areas.map((area) => {
          const Icon = area.icon;
          return (
            <Link key={area.href} href={area.href}
              className="group rounded-2xl border border-slate-200 bg-white p-4 transition hover:border-indigo-300 hover:shadow-sm">
              <div className="flex items-center gap-2 text-slate-900">
                <Icon className="h-4 w-4 text-slate-400 group-hover:text-indigo-600" />
                <span className="font-bold">{area.title}</span>
              </div>
              <p className="mt-1 text-sm leading-6 text-slate-600">{area.description}</p>
            </Link>
          );
        })}
      </section>

      <div className="mt-6">
        <SermonOverview />
      </div>
    </main>
  );
}
