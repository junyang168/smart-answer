import Link from "next/link";
import { FileCheck2, HeartPulse, Highlighter, Network, ShieldCheck } from "lucide-react";
import { SermonOverview } from "./SermonOverview";

// The three existing views stay where they are and are not rebuilt; they move
// off the home page because the nav already calls this tab 總覽 and it had no
// overview on it -- only four links to the places an overview would summarise.
const areas = [
  { href: "/admin/wang/health", title: "健康視圖", description: "有沒有該管的事：整個語料的品質分佈", icon: HeartPulse },
  { href: "/admin/wang/matthew-progress", title: "馬太進度", description: "文章從篇章計畫到 production 的每一步", icon: Network },
  { href: "/admin/wang/argument-layer", title: "論證層", description: "一個來源一張圖：從問題到結論", icon: Network },
  { href: "/admin/wang/source-coverage", title: "來源覆蓋", description: "原文逐句對照 claim 層", icon: Highlighter },
  { href: "/admin/canonical-repository", title: "出版單元", description: "canonical units 與公開索引", icon: FileCheck2 },
];

export default function WangAdminHome() {
  return (
    <main>
      <header className="overflow-hidden rounded-3xl bg-slate-950 px-6 py-7 text-white sm:px-9">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-sm font-bold tracking-wide text-indigo-300">Wang Knowledge Platform</p>
            <h1 className="mt-2 text-3xl font-black tracking-tight">講道進知識庫：總表</h1>
            <p className="mt-3 text-base leading-7 text-slate-300">
              一行一篇來源，五個階段：抽取 → 複審 → 仲裁 → 合併 → 入庫。每一格的狀態與品質都來自執行記錄表。
              寫文章是另一條線，在馬太進度。
            </p>
          </div>
          <div className="flex items-center gap-3 rounded-2xl bg-white/10 px-4 py-3 text-sm text-slate-200">
            <ShieldCheck className="h-5 w-5 text-emerald-300" />內部編輯與審核
          </div>
        </div>
      </header>

      <div className="mt-6">
        <SermonOverview />
      </div>

      <section className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
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
    </main>
  );
}
