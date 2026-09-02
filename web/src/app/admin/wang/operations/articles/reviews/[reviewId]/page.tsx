import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { AlertTriangle, ArrowLeft, CheckCircle2, CircleDashed, FileText, ShieldAlert } from "lucide-react";
import { slugifyHeadingAnchor } from "@/app/components/full-article/heading-anchor";
import { ReviewArticle } from "../ReviewArticle";
import { fetchTopicEssayReview } from "../data";
import { PublishControls } from "../PublishControls";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "内部文章审稿 | 王教授文库后台",
  robots: { index: false, follow: false },
};

function headings(markdown: string) {
  return Array.from(markdown.matchAll(/^##\s+(.+)$/gm), (match) => match[1].trim());
}

export default async function TopicEssayReviewPage(props: { params: Promise<{ reviewId: string }> }) {
  const { reviewId } = await props.params;
  const review = await fetchTopicEssayReview(reviewId);
  if (!review) notFound();
  const sectionHeadings = headings(review.markdown);
  const published = review.workflow_status === "workflow_published";
  const humanPublished = review.publication_decision?.decision === "approved";

  return (
    <main className="pb-20">
      <Link href="/admin/wang/operations/articles/reviews" className="inline-flex items-center gap-2 text-sm font-semibold text-indigo-700 hover:underline">
        <ArrowLeft className="h-4 w-4" />返回审稿预览
      </Link>

      <header className="mt-4 overflow-hidden rounded-3xl border border-amber-200 bg-[#f5eddb] px-6 py-8 sm:px-10 sm:py-10">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-4xl">
            <p className="flex items-center gap-2 text-sm font-black tracking-[0.14em] text-amber-900">
              <ShieldAlert className="h-4 w-4" />
              {humanPublished ? "审稿通过 · 已出版" : published ? "POC 内部审稿 · 自动流程已出版" : "POC 内部审稿 · 尚未出版"}
            </p>
            <p className="mt-6 text-sm font-bold text-stone-600">{review.passage}</p>
            <h1 className="mt-3 font-serif text-3xl font-bold leading-tight text-stone-950 sm:text-5xl">{review.title}</h1>
            <p className="mt-5 max-w-3xl text-sm leading-7 text-stone-700 sm:text-base">
              {humanPublished
                ? "这版已经出版，读者可以在王教授文库看见。这里仍保留内部来源对照，供教会同工继续审阅；如需下架，用右侧「从读者站撤下」。"
                : published
                ? "这版已经通过 grounding、独立编审、差异审核与程序审计，并由自动流程发布到王教授文库；这里仍保留内部来源对照，供教会同工继续审阅。"
                : "这是写作流程中的工作稿，只供教会同工继续审阅。页面不会出现在公开文库；闸门全部通过后，可以用右侧「正式出版」发布给读者。"}
            </p>
          </div>
          <div className="flex flex-col items-end gap-3">
            <PublishControls
              reviewId={reviewId}
              workflowStatus={review.workflow_status}
              integrityStatus={review.integrity_status}
              publicationDecision={review.publication_decision ?? null}
            />
          </div>
        </div>
      </header>

      <section aria-label="稿件状态" className="mt-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap gap-2">
          {review.stage_checks.map((check) => {
            const done = check.state === "complete" || check.state === "passed";
            const failed = check.state === "failed";
            const Icon = done ? CheckCircle2 : failed ? AlertTriangle : CircleDashed;
            return (
              <span key={check.id} className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-bold ${
                done ? "bg-emerald-50 text-emerald-800" : failed ? "bg-rose-50 text-rose-800" : "bg-slate-100 text-slate-500"
              }`}>
                <Icon className="h-3.5 w-3.5" />{check.label} · {done ? "完成" : failed ? "未通过" : "未运行"}
              </span>
            );
          })}
        </div>
        <div className={`mt-4 flex flex-wrap items-center gap-2 rounded-xl border px-4 py-3 text-sm ${
          review.source_projection_audit.passed
            ? "border-emerald-200 bg-emerald-50 text-emerald-900"
            : "border-rose-200 bg-rose-50 text-rose-900"
        }`}>
          {review.source_projection_audit.passed ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : (
            <AlertTriangle className="h-4 w-4" />
          )}
          <span className="font-bold">
            来源一致性{review.source_projection_audit.passed ? "已通过" : "未通过"}
          </span>
          <span>
            {review.source_projection_audit.paragraphs_with_sources}/{review.source_projection_audit.paragraphs_checked} 段有来源，
            {review.source_projection_audit.direct_quotes_checked} 处逐字引文已核对
          </span>
          {!review.source_projection_audit.passed ? (
            <span>· {review.source_projection_audit.findings.length} 项需要处理</span>
          ) : null}
        </div>
        <div className={`mt-3 flex flex-wrap items-center gap-2 rounded-xl border px-4 py-3 text-sm ${
          review.source_playback_audit.passed
            ? "border-sky-200 bg-sky-50 text-sky-950"
            : "border-rose-200 bg-rose-50 text-rose-900"
        }`}>
          {review.source_playback_audit.passed ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : (
            <AlertTriangle className="h-4 w-4" />
          )}
          <span className="font-bold">
            引文录音定位{review.source_playback_audit.passed ? "已通过" : "未通过"}
          </span>
          <span>
            {review.source_playback_audit.exact_clips} 段精确匹配，
            {review.source_playback_audit.estimated_clips} 段顺序对齐，
            {review.source_playback_audit.paragraph_fallback_clips} 段仍从大段开头播放
          </span>
          {review.source_playback_audit.findings.length > 0 ? (
            <span>· {review.source_playback_audit.findings.length} 项文字或时间提示</span>
          ) : null}
        </div>
      </section>

      <div className="mt-7 grid items-start gap-7 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className="rounded-2xl border border-slate-200 bg-white p-5 lg:sticky lg:top-6">
          <p className="flex items-center gap-2 text-xs font-black tracking-[0.14em] text-slate-500"><FileText className="h-4 w-4" />文章目录</p>
          <ol className="mt-4 space-y-1.5">
            {sectionHeadings.map((heading, index) => (
              <li key={heading}>
                <a href={`#${slugifyHeadingAnchor(heading)}`} className="block rounded-lg px-3 py-2 text-sm leading-6 text-slate-700 hover:bg-amber-50 hover:text-amber-950">
                  <span className="mr-2 text-slate-400">{index + 1}</span>{heading}
                </a>
              </li>
            ))}
          </ol>
        </aside>

        <article className="min-w-0 rounded-[2rem] bg-[#fffdf9] px-6 py-9 shadow-[0_18px_60px_rgba(70,55,35,0.08)] sm:px-10 sm:py-12 lg:px-14">
          <ReviewArticle markdown={review.markdown} sourceAnnotations={review.source_annotations} />
        </article>
      </div>
    </main>
  );
}
