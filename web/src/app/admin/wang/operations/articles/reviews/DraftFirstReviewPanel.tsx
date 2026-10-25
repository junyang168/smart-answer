import { AlertTriangle, CheckCircle2, ListChecks, MessagesSquare } from "lucide-react";
import type { DraftFirstChecklistItem, DraftFirstFinding, DraftFirstReview } from "./types";

// The topic-essay quality profile's dimensions (WQ-theological-topic-essay-v1),
// grouped as the reviewer prompt groups them.
const DIMENSIONS: Record<string, { label: string; weight: number }> = {
  source_and_exegesis: { label: "忠实 · 有原文依据", weight: 15 },
  positive_thesis_and_structural_fidelity: { label: "忠实 · 立场与结构", weight: 15 },
  argument_route_integrity: { label: "忠实 · 论证路线", weight: 15 },
  theological_tension_and_attribution: { label: "忠实 · 张力与归属", weight: 10 },
  reader_memory_center: { label: "结构 · 读者记得住", weight: 5 },
  general_reader_readability: { label: "读者 · 好读", weight: 10 },
  concision_without_compression: { label: "读者 · 言简意赅", weight: 5 },
  pastoral_theological_landing: { label: "读者 · 落地", weight: 5 },
  editorial_voice_restraint: { label: "口吻 · 编者克制", weight: 10 },
  approved_written_style: { label: "口吻 · 书面文风", weight: 10 },
};

function dimensionLabel(id: string): string {
  return DIMENSIONS[id]?.label ?? id;
}

function Finding({ finding }: { finding: DraftFirstFinding }) {
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs font-bold">
        <span className={`rounded-full px-2 py-0.5 ${finding.gate === "owner" ? "bg-violet-100 text-violet-800" : "bg-slate-100 text-slate-700"}`}>{finding.gate_label}</span>
        {finding.response ? (
          <span className={`rounded-full px-2 py-0.5 ${finding.response.kind === "fixed" ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>作者：{finding.response.label}</span>
        ) : null}
        {finding.ruling ? (
          <span className="rounded-full bg-sky-50 px-2 py-0.5 text-sky-800">审稿：{finding.ruling.label}</span>
        ) : null}
      </div>
      {finding.anchor ? (
        <blockquote className="mt-3 border-l-2 border-slate-300 pl-3 font-serif text-sm leading-7 text-slate-800">{finding.anchor}</blockquote>
      ) : null}
      <p className="mt-2 text-sm leading-7 text-slate-800">{finding.summary}</p>
      {finding.required_change ? (
        <p className="mt-2 text-sm leading-7 text-slate-600"><span className="font-bold text-slate-700">要怎么改：</span>{finding.required_change}</p>
      ) : null}
      {finding.source_quote ? (
        <p className="mt-2 text-sm leading-7 text-amber-950"><span className="font-bold">原文：</span>{finding.source_quote}</p>
      ) : null}
      {finding.response?.note ? (
        <p className="mt-2 text-sm leading-7 text-emerald-900"><span className="font-bold">作者：</span>{finding.response.note}</p>
      ) : null}
      {finding.ruling?.note ? (
        <p className="mt-2 text-sm leading-7 text-sky-900"><span className="font-bold">审稿裁定：</span>{finding.ruling.note}</p>
      ) : null}
    </li>
  );
}

function ChecklistRow({ item }: { item: DraftFirstChecklistItem }) {
  const problem = item.reviewer_acceptable === false;
  return (
    <li className={`rounded-xl border p-4 ${problem ? "border-rose-200 bg-rose-50/50" : "border-slate-200 bg-white"}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs font-bold">
        <span className={`rounded-full px-2 py-0.5 ${item.status === "landed" ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>{item.status_label ?? "作者未答"}</span>
        <span className={`rounded-full px-2 py-0.5 ${problem ? "bg-rose-100 text-rose-800" : "bg-slate-100 text-slate-600"}`}>审稿{problem ? "不同意" : "同意"}</span>
      </div>
      <p className="mt-2 text-sm font-semibold leading-7 text-slate-900">{item.text}</p>
      {item.anchor ? (
        <blockquote className="mt-2 border-l-2 border-slate-300 pl-3 font-serif text-sm leading-7 text-slate-700">{item.anchor}</blockquote>
      ) : null}
      {item.author_reason ? <p className="mt-2 text-sm leading-6 text-slate-600"><span className="font-bold">作者：</span>{item.author_reason}</p> : null}
      {item.reviewer_reason ? <p className="mt-1 text-sm leading-6 text-slate-600"><span className="font-bold">审稿：</span>{item.reviewer_reason}</p> : null}
    </li>
  );
}

export function DraftFirstReviewPanel({ review }: { review: DraftFirstReview }) {
  const rounds = [...review.rounds].reverse();
  const absent = review.checklist.filter((item) => item.status !== "landed");
  const disagreed = review.checklist.filter((item) => item.reviewer_acceptable === false);
  const ordered = [...disagreed, ...absent.filter((item) => item.reviewer_acceptable !== false),
    ...review.checklist.filter((item) => item.status === "landed" && item.reviewer_acceptable !== false)];
  return (
    <section className="space-y-5">
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="flex items-center gap-2 text-lg font-black text-slate-950"><MessagesSquare className="h-5 w-5 text-indigo-600" />审稿过程</h2>
        <p className="mt-1 text-sm text-slate-500">共 {review.rounds.length} 轮，最新的在上面。每个问题下面是作者怎么回应、审稿怎么裁定。</p>
        <div className="mt-4 space-y-3">
          {rounds.map((round, index) => (
            <details key={round.round} open={index === 0} className="rounded-xl border border-slate-200 bg-slate-50/60">
              <summary className="flex cursor-pointer flex-wrap items-center gap-3 px-4 py-3 text-sm font-bold text-slate-900">
                {round.passed ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                第 {round.round + 1} 轮 · {round.passed ? "通过" : `${round.findings.length} 个问题`}
                {round.edit_count > 0 ? <span className="font-normal text-slate-500">作者改了 {round.edit_count} 处</span> : null}
              </summary>
              <div className="border-t border-slate-200 p-4">
                {round.summary ? <p className="text-sm leading-7 text-slate-700">{round.summary}</p> : null}
                {round.dimensions_below_minimum.length > 0 ? (
                  <p className="mt-2 text-sm text-amber-900">未达标的维度：{round.dimensions_below_minimum.map(dimensionLabel).join("、")}</p>
                ) : null}
                {round.findings.length > 0 ? (
                  <ul className="mt-3 space-y-3">{round.findings.map((finding) => <Finding key={finding.finding_id} finding={finding} />)}</ul>
                ) : null}
              </div>
            </details>
          ))}
        </div>
      </div>

      {review.dimension_scores.length > 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <h2 className="text-lg font-black text-slate-950">最后一轮的评分</h2>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {review.dimension_scores.map((score) => {
              const weight = DIMENSIONS[score.dimension_id]?.weight;
              return (
                <li key={score.dimension_id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                  <span className="text-slate-700">{dimensionLabel(score.dimension_id)}</span>
                  <span className="font-bold text-slate-950">{score.score}{weight ? ` / ${weight}` : ""}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      <details className="rounded-2xl border border-slate-200 bg-white">
        <summary className="flex cursor-pointer flex-wrap items-center gap-3 p-5 text-lg font-black text-slate-950">
          <ListChecks className="h-5 w-5 text-indigo-600" />核对表
          <span className="text-sm font-normal text-slate-500">
            立场清单 {review.checklist.length} 项：写到 {review.checklist.length - absent.length}，没写 {absent.length}，审稿不同意 {disagreed.length}
          </span>
        </summary>
        <ul className="space-y-3 border-t border-slate-200 p-5">
          {ordered.map((item) => <ChecklistRow key={item.item_id} item={item} />)}
        </ul>
      </details>
    </section>
  );
}
