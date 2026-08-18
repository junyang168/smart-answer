import Link from "next/link";
import { Activity, ArrowUpRight, BookOpenText, Database, FileCheck2, Network, ShieldCheck } from "lucide-react";

const areas = [
  { href: "/admin/wang/matthew-progress", title: "馬太福音文章進度", description: "從篇章計畫一直看到 repository 與 production，明確顯示阻塞和部署滯後。", icon: Activity, accent: "bg-indigo-100 text-indigo-700" },
  { href: "/admin/thought-review/candidates", title: "釋經與專題候選", description: "按經卷查看已有材料、篇章編排和編輯稿，不把候選誤當成文章。", icon: BookOpenText, accent: "bg-sky-100 text-sky-700" },
  { href: "/admin/thought-review", title: "共享知識審核", description: "處理主張、證據、跨講綜合、問答診斷與篇章編排審核。", icon: Database, accent: "bg-emerald-100 text-emerald-700" },
  { href: "/admin/wang/argument-layer", title: "論證層檢視", description: "一個來源一張圖：教授從問題、經文走到結論的每一步，以及哪些材料還沒進入論證。只看，不改。", icon: Network, accent: "bg-slate-100 text-slate-700" },
  { href: "/admin/canonical-repository", title: "Canonical 出版單元", description: "維護舊式 canonical units、來源引用與公開索引。", icon: FileCheck2, accent: "bg-amber-100 text-amber-800" },
];

export default function WangAdminHome() {
  return (
    <main>
      <header className="overflow-hidden rounded-3xl bg-slate-950 px-6 py-9 text-white sm:px-9">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-sm font-bold tracking-wide text-indigo-300">Wang Knowledge Platform</p>
            <h1 className="mt-2 text-3xl font-black tracking-tight sm:text-4xl">釋經與思想文庫後台</h1>
            <p className="mt-4 text-base leading-7 text-slate-300">先判斷內容在哪一層，再進入對應工作台。文章進度、知識審核與 canonical publication 是三件不同的事。</p>
          </div>
          <div className="flex items-center gap-3 rounded-2xl bg-white/10 px-4 py-3 text-sm text-slate-200">
            <ShieldCheck className="h-5 w-5 text-emerald-300" />內部編輯與審核
          </div>
        </div>
      </header>
      <section className="mt-7 grid gap-4 md:grid-cols-2">
        {areas.map((area) => {
          const Icon = area.icon;
          return (
            <Link key={area.href} href={area.href} className="group rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-md">
              <div className="flex items-start justify-between gap-4">
                <span className={`flex h-11 w-11 items-center justify-center rounded-xl ${area.accent}`}><Icon className="h-5 w-5" /></span>
                <ArrowUpRight className="h-5 w-5 text-slate-300 group-hover:text-indigo-600" />
              </div>
              <h2 className="mt-5 text-xl font-black text-slate-950">{area.title}</h2>
              <p className="mt-2 leading-7 text-slate-600">{area.description}</p>
            </Link>
          );
        })}
      </section>
    </main>
  );
}
