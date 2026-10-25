import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, CircleDot, PauseCircle } from "lucide-react";
import { backendJson } from "../../../../backend";
import { DraftFirstReviewPanel } from "../../reviews/DraftFirstReviewPanel";
import type { DraftFirstReview } from "../../reviews/types";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "文章进度 | 王教授文库后台",
  robots: { index: false, follow: false },
};

type CoverageRow = {
  kind: string;
  text: string;
  why_relevant: string | null;
  suggestion: string | null;
  suggestion_reason: string | null;
  decision: string | null;
  structure_role: string | null;
  decision_reason: string | null;
};

type EssayRun = {
  review_id: string;
  title: string;
  reader_question: string | null;
  passages: string[];
  stage: string;
  stage_label: string;
  stages_done: string[];
  stopped: { stage: string; summary: string; at: string | null } | null;
  decision_basis: string | null;
  coverage: CoverageRow[];
  cvp_gap: { id: string; text: string }[];
  calibration: string | null;
  review: DraftFirstReview | null;
};

const STAGES: [string, string][] = [
  ["scope", "定问题"], ["coverage", "覆盖搜索"], ["decisions", "选材"], ["cvp_gap", "补观点"],
  ["calibration", "审稿校准"], ["packet", "编材料包"], ["author", "写稿"], ["review", "审稿"],
  ["binding", "绑定出处"], ["register", "登记"], ["final_review", "终审"],
];

const DECISIONS: Record<string, string> = { include: "纳入", adjacent: "相邻" };
const ROLES: Record<string, string> = {
  central_claim: "中心论点", supporting_conclusion: "支持结论", positive_identification: "正面认定",
  qualification: "限定", tension_side: "张力一方",
};

export default async function EssayRunPage(props: { params: Promise<{ reviewId: string }> }) {
  const { reviewId } = await props.params;
  const run = await backendJson<EssayRun>(`/admin/wang/essays/${encodeURIComponent(reviewId)}`);
  if (!run) notFound();
  const registered = run.stages_done.includes("register");
  return (
    <main className="pb-20">
      <Link href="/admin/wang/operations/articles" className="inline-flex items-center gap-2 text-sm font-semibold text-indigo-700 hover:underline">
        <ArrowLeft className="h-4 w-4" />返回文章
      </Link>

      <header className="mt-4 rounded-3xl border border-slate-200 bg-white p-6 sm:p-8">
        <p className="text-sm font-bold text-slate-500">{run.passages.join("；")}</p>
        <h1 className="mt-2 font-serif text-3xl font-bold text-slate-950">{run.title}</h1>
        {run.reader_question ? <p className="mt-4 text-base leading-8 text-slate-800"><span className="font-bold">读者问题：</span>{run.reader_question}</p> : null}
        {run.decision_basis ? <p className="mt-2 text-sm leading-7 text-slate-600"><span className="font-bold">讲什么、不讲什么：</span>{run.decision_basis}</p> : null}
        {registered ? (
          <Link href={`/admin/wang/operations/articles/reviews/${encodeURIComponent(run.review_id)}`} className="mt-4 inline-flex items-center gap-1 text-sm font-semibold text-indigo-700 hover:underline">
            打开审稿页<ArrowRight className="h-3.5 w-3.5" />
          </Link>
        ) : null}
      </header>

      <ol className="mt-5 flex flex-wrap gap-2" aria-label="步骤">
        {STAGES.map(([id, label]) => {
          // Where the run stopped wins over an older "done": a redo or a
          // changed input can stop a run at a stage it once finished.
          const current = run.stage === id;
          const done = !current && run.stages_done.includes(id);
          const Icon = done ? Check : current ? (run.stopped ? PauseCircle : CircleDot) : CircleDot;
          return (
            <li key={id} className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-bold ${
              done ? "bg-emerald-50 text-emerald-800" : current ? "bg-amber-100 text-amber-900" : "bg-slate-100 text-slate-400"
            }`}>
              <Icon className="h-3.5 w-3.5" />{label}
            </li>
          );
        })}
      </ol>

      {run.stopped ? (
        <section className="mt-5 rounded-2xl border border-amber-300 bg-amber-50 p-5">
          <p className="flex items-center gap-2 font-black text-amber-950"><PauseCircle className="h-5 w-5" />停在「{run.stage_label}」</p>
          <p className="mt-2 text-sm leading-7 text-amber-950">{run.stopped.summary}</p>
          <p className="mt-1 text-xs text-amber-800">决定在与 Claude 的对话里做（/topic-essay），这里只显示。</p>
          {run.calibration ? <p className="mt-2 text-sm text-amber-950">{run.calibration}</p> : null}
          {run.cvp_gap.length > 0 ? (
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-amber-950">
              {run.cvp_gap.map((claim) => <li key={claim.id}>{claim.text}</li>)}
            </ul>
          ) : null}
        </section>
      ) : null}

      {run.coverage.length > 0 ? (
        // Open only while the run waits on these decisions; afterwards it is a record.
        <details open={run.stage === "decisions"} className="mt-6 overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <summary className="cursor-pointer p-5">
            <span className="text-lg font-black text-slate-950">选材</span>
            <span className="ml-3 text-sm text-slate-500">
              覆盖搜索找到 {run.coverage.length} 个候选：纳入 {run.coverage.filter((row) => row.decision === "include").length}，
              相邻 {run.coverage.filter((row) => row.decision === "adjacent").length}，
              还没决定 {run.coverage.filter((row) => !row.decision).length}
            </span>
          </summary>
          <ul className="divide-y divide-slate-100 border-t border-slate-200">
            {run.coverage.map((row, index) => (
              <li key={index} className="grid gap-2 p-5 sm:grid-cols-[minmax(0,1fr)_14rem]">
                <div>
                  <p className="text-sm font-semibold leading-7 text-slate-900">{row.text}</p>
                  {row.why_relevant ? <p className="mt-1 text-sm leading-6 text-slate-600">为什么相关：{row.why_relevant}</p> : null}
                  {row.suggestion_reason ? <p className="mt-1 text-sm leading-6 text-slate-500">建议{DECISIONS[row.suggestion ?? ""] ?? row.suggestion}：{row.suggestion_reason}</p> : null}
                </div>
                <div className="text-sm">
                  {row.decision ? (
                    <>
                      <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${row.decision === "include" ? "bg-emerald-50 text-emerald-800" : "bg-slate-100 text-slate-700"}`}>
                        {DECISIONS[row.decision] ?? row.decision}{row.structure_role ? ` · ${ROLES[row.structure_role] ?? row.structure_role}` : ""}
                      </span>
                      {row.decision_reason ? <p className="mt-2 leading-6 text-slate-600">{row.decision_reason}</p> : null}
                    </>
                  ) : <span className="text-xs font-bold text-amber-800">还没决定</span>}
                </div>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {run.review ? (
        <div className="mt-8">
          <DraftFirstReviewPanel review={run.review} />
        </div>
      ) : null}
    </main>
  );
}
