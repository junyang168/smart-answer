import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, BookMarked, FileSearch } from "lucide-react";
import { backendJson } from "../../backend";
import { ArticleTable } from "./ArticleTable";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "文章 | 王教授文库后台",
  robots: { index: false, follow: false },
};

type ArticleRowSummary = {
  review_id: string;
  title: string;
  passage: string | null;
  stage_label: string;
  waiting: string | null;
  published: boolean;
  review_href: string | null;
  run_href: string | null;
  updated_at: string | null;
};

function when(at: string | null): string {
  if (!at) return "";
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" });
}

export default async function ArticleOverviewPage() {
  const payload = await backendJson<{ articles: ArticleRowSummary[] }>("/admin/wang/essays");
  const articles = payload?.articles ?? [];
  return (
    <main>
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 pb-5">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-slate-950">文章</h1>
          <p className="mt-2 max-w-3xl text-base leading-7 text-slate-600">
            每篇文章做到哪一步、停在哪里等谁。点标题看进度，点「审稿页」读全文、看出处和审稿过程。
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-sm font-semibold">
          <Link href="/admin/wang/operations/articles/reviews" className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-slate-700 hover:border-indigo-300">
            <FileSearch className="h-4 w-4" />全部审稿页
          </Link>
          <Link href="/admin/wang/matthew-progress" className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-slate-700 hover:border-indigo-300">
            <BookMarked className="h-4 w-4" />马太按章
          </Link>
        </div>
      </header>

      <section className="mt-6 overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs font-bold text-slate-500">
            <tr>
              <th className="px-4 py-3">文章</th>
              <th className="px-4 py-3">经文</th>
              <th className="px-4 py-3">现在</th>
              <th className="px-4 py-3">等什么</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {articles.length === 0 ? (
              <tr><td colSpan={5} className="px-4 py-6 text-slate-500">还没有新流程的文章。</td></tr>
            ) : articles.map((article) => (
              <tr key={article.review_id} className="align-top">
                <td className="px-4 py-3">
                  {article.run_href ? (
                    <Link href={article.run_href} className="font-bold text-slate-950 hover:text-indigo-700">{article.title}</Link>
                  ) : (
                    <span className="font-bold text-slate-950">{article.title}</span>
                  )}
                  <span className="mt-0.5 block text-xs text-slate-400">{when(article.updated_at)}</span>
                </td>
                <td className="px-4 py-3 text-slate-600">{article.passage}</td>
                <td className="px-4 py-3">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${article.published ? "bg-emerald-50 text-emerald-800" : article.waiting ? "bg-amber-50 text-amber-900" : "bg-slate-100 text-slate-600"}`}>
                    {article.stage_label}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-600">{article.waiting ?? "—"}</td>
                <td className="px-4 py-3 text-right">
                  {article.review_href ? (
                    <Link href={article.review_href} className="inline-flex items-center gap-1 font-semibold text-indigo-700 hover:underline">审稿页<ArrowRight className="h-3.5 w-3.5" /></Link>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <details className="mt-8 rounded-2xl border border-slate-200 bg-white">
        <summary className="cursor-pointer px-5 py-4 text-base font-black text-slate-900">
          旧流程的马太文章
          <span className="ml-2 text-sm font-normal text-slate-500">九月以前按篇章计划写成的稿件</span>
        </summary>
        <div className="border-t border-slate-200 p-4">
          <ArticleTable />
        </div>
      </details>
    </main>
  );
}
