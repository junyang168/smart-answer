import type { Metadata } from "next";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, CheckCircle2, FileSearch, ShieldAlert } from "lucide-react";
import { fetchTopicEssayReviews } from "./data";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "文章审稿预览 | 王教授文库后台",
  robots: { index: false, follow: false },
};

export default async function TopicEssayReviewsPage() {
  const payload = await fetchTopicEssayReviews();
  return (
    <main>
      <Link href="/admin/wang/operations/articles" className="inline-flex items-center gap-2 text-sm font-semibold text-indigo-700 hover:underline">
        <ArrowLeft className="h-4 w-4" />返回写文章总表
      </Link>
      <header className="mt-4 overflow-hidden rounded-3xl bg-slate-950 px-6 py-7 text-white sm:px-9">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-sm font-bold tracking-wide text-amber-300">INTERNAL EDITORIAL REVIEW</p>
            <h1 className="mt-2 text-3xl font-black tracking-tight">文章审稿预览</h1>
            <p className="mt-3 text-base leading-7 text-slate-300">
              这里展示的是明确登记给教会同工阅读的 staging 稿件。它们不会进入公开文库，也不代表已经通过出版闸门。
            </p>
          </div>
          <div className="flex items-center gap-3 rounded-2xl bg-amber-300/10 px-4 py-3 text-sm text-amber-100">
            <ShieldAlert className="h-5 w-5" />登录后内部可见
          </div>
        </div>
      </header>

      {payload.warnings.length > 0 && (
        <div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 p-5 text-amber-950">
          <p className="flex items-center gap-2 font-bold"><AlertTriangle className="h-4 w-4" />有预览登记无法读取</p>
          <ul className="mt-2 space-y-1 text-sm">
            {payload.warnings.map((warning) => <li key={warning.manifest}>· {warning.manifest}：{warning.message}</li>)}
          </ul>
        </div>
      )}

      <section className="mt-6 grid gap-4 lg:grid-cols-2">
        {payload.reviews.map((review) => (
          <article key={review.review_id} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-bold tracking-[0.12em] text-amber-800">{review.passage || "主题文章"}</p>
                <h2 className="mt-2 text-xl font-black leading-8 text-slate-950">{review.title}</h2>
              </div>
              {review.integrity_status === "verified" ? (
                <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-800">
                  <CheckCircle2 className="h-3.5 w-3.5" />SHA 已核对
                </span>
              ) : (
                <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-rose-50 px-3 py-1 text-xs font-bold text-rose-800">
                  <AlertTriangle className="h-3.5 w-3.5" />稿件已改变
                </span>
              )}
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              {review.stage_checks.map((check) => (
                <span key={check.id} className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
                  check.state === "complete" || check.state === "passed"
                    ? "bg-emerald-50 text-emerald-800"
                    : check.state === "failed"
                      ? "bg-rose-50 text-rose-800"
                      : "bg-slate-100 text-slate-500"
                }`}>{check.label} · {check.state === "not_run" ? "未运行" : check.state === "failed" ? "未通过" : "完成"}</span>
              ))}
            </div>
            <p className="mt-5 break-all font-mono text-[11px] leading-5 text-slate-400">Manuscript SHA · {review.manuscript_sha256}</p>
            {review.integrity_status === "verified" ? (
              <Link href={review.href} className="mt-5 inline-flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-bold text-white hover:bg-indigo-800">
                <FileSearch className="h-4 w-4" />打开审稿页
              </Link>
            ) : (
              <p className="mt-5 text-sm font-semibold text-rose-700">请重新登记当前稿件后再继续审阅。</p>
            )}
          </article>
        ))}
        {payload.reviews.length === 0 && (
          <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-12 text-center text-slate-500 lg:col-span-2">
            目前没有登记给同工审阅的文章。
          </div>
        )}
      </section>
    </main>
  );
}
