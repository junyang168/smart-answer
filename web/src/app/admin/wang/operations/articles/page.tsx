import Link from "next/link";
import { FileSearch, ShieldCheck } from "lucide-react";
import { ArticleTable } from "./ArticleTable";

export default function ArticleOverviewPage() {
  return (
    <main>
      <header className="overflow-hidden rounded-3xl bg-slate-950 px-6 py-7 text-white sm:px-9">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-sm font-bold tracking-wide text-indigo-300">Wang Knowledge Platform</p>
            <h1 className="mt-2 text-3xl font-black tracking-tight">寫文章：總表</h1>
            <p className="mt-3 text-base leading-7 text-slate-300">
              一行一個編排計劃。講道進知識庫是另一條線，在總覽。兩者是多對多：一篇文章站在好幾篇講道上，一篇講道也可以進好幾篇文章。
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3">
            <Link href="/admin/wang/operations/articles/reviews" className="inline-flex items-center gap-2 rounded-xl bg-amber-300 px-4 py-2.5 text-sm font-black text-slate-950 hover:bg-amber-200">
              <FileSearch className="h-4 w-4" />审稿预览
            </Link>
            <div className="flex items-center gap-3 rounded-2xl bg-white/10 px-4 py-3 text-sm text-slate-200">
              <ShieldCheck className="h-5 w-5 text-emerald-300" />只讀
            </div>
          </div>
        </div>
      </header>
      <div className="mt-6">
        <ArticleTable />
      </div>
    </main>
  );
}
