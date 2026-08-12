"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { BookOpen, ChevronLeft, ChevronRight, FileText, Layers3, Loader2 } from "lucide-react";

type Candidate = {
  candidate_id: string;
  axis: "scripture" | "topic";
  title: string;
  description: string;
  candidate_state: "composition_plan_ready" | "research_leads";
  candidate_state_label: string;
  canonical_topics: { topic_id: string; label: string }[];
  claims: { claim_id: string; title: string }[];
  claim_count: number;
  decisions: { decision_id: string; title: string; review: { status: string } }[];
  decision_count: number;
};

type Payload = {
  title: string;
  description: string;
  scripture_candidates: Candidate[];
  topic_candidates: Candidate[];
};

const statusLabels: Record<string, string> = {
  candidate: "待審核",
  approved: "已批准",
  changes_requested: "需要修改",
  rejected: "不採用",
};

export default function ThoughtReviewCandidatesPage() {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [axis, setAxis] = useState<"scripture" | "topic">("scripture");
  const [target, setTarget] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("axis") === "topic") setAxis("topic");
    setTarget(params.get("target") ?? "");
    fetch("/api/admin/thought-review/candidates", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json()).detail ?? "無法載入候選內容");
        return response.json();
      })
      .then(setPayload)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "無法載入候選內容"));
  }, []);

  useEffect(() => {
    if (!payload || !target) return;
    document.getElementById(`candidate-${target}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [payload, target, axis]);

  const items = useMemo(
    () => (axis === "scripture" ? payload?.scripture_candidates : payload?.topic_candidates) ?? [],
    [axis, payload],
  );

  function selectAxis(nextAxis: "scripture" | "topic") {
    setAxis(nextAxis);
    setTarget("");
    window.history.replaceState(null, "", `/admin/thought-review/candidates?axis=${nextAxis}`);
  }

  if (!payload && !error) {
    return <div className="flex min-h-[60vh] items-center justify-center gap-3 text-slate-600"><Loader2 className="h-6 w-6 animate-spin" />正在整理候選內容…</div>;
  }
  if (error) return <div className="mx-auto mt-12 max-w-2xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-800">{error}</div>;

  return (
    <main className="min-h-screen bg-slate-50 pb-12">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <Link href="/admin/thought-review" className="inline-flex items-center gap-1 text-sm font-semibold text-indigo-700">
          <ChevronLeft className="h-4 w-4" />返回思想審核
        </Link>
        <header className="mt-4 rounded-3xl bg-slate-900 p-7 text-white shadow-sm">
          <p className="text-sm font-semibold text-indigo-300">共享知識的產品出口</p>
          <h1 className="mt-2 text-3xl font-bold">{payload?.title}</h1>
          <p className="mt-3 max-w-3xl leading-7 text-slate-300">{payload?.description}</p>
        </header>

        <div className="mt-6 grid grid-cols-2 gap-3 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm">
          <button onClick={() => selectAxis("scripture")} className={`flex items-center justify-center gap-2 rounded-xl px-4 py-3 font-bold ${axis === "scripture" ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
            <BookOpen className="h-5 w-5" />釋經候選（{payload?.scripture_candidates.length ?? 0}）
          </button>
          <button onClick={() => selectAxis("topic")} className={`flex items-center justify-center gap-2 rounded-xl px-4 py-3 font-bold ${axis === "topic" ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
            <Layers3 className="h-5 w-5" />專題候選（{payload?.topic_candidates.length ?? 0}）
          </button>
        </div>

        <div className="mt-6 space-y-5">
          {items.map((item) => {
            const highlighted = item.candidate_id === target;
            return (
              <article id={`candidate-${item.candidate_id}`} key={item.candidate_id} className={`scroll-mt-24 rounded-2xl border bg-white p-6 shadow-sm transition ${highlighted ? "border-indigo-400 ring-4 ring-indigo-100" : "border-slate-200"}`}>
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="max-w-3xl">
                    <span className={`inline-flex rounded-full px-3 py-1 text-xs font-bold ${item.candidate_state === "composition_plan_ready" ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>
                      {item.candidate_state_label}
                    </span>
                    <h2 className="mt-3 text-2xl font-bold text-slate-950">{item.title}</h2>
                    <p className="mt-2 leading-7 text-slate-600">{item.description}</p>
                  </div>
                  <div className="rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
                    <strong className="block text-xl text-slate-950">{item.claim_count}</strong>條共享主張
                  </div>
                </div>

                {!!item.canonical_topics.length && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {item.canonical_topics.map((topic) => <span key={topic.topic_id} className="rounded-full bg-sky-50 px-3 py-1 text-sm font-semibold text-sky-800">{topic.label}</span>)}
                  </div>
                )}

                {!!item.decisions.length && (
                  <section className="mt-6">
                    <h3 className="font-bold text-slate-900">編排決定</h3>
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      {item.decisions.map((decision) => (
                        <div key={decision.decision_id} className="rounded-xl border border-slate-200 p-4">
                          <span className="text-xs font-bold text-indigo-700">{statusLabels[decision.review.status] ?? "待確認"}</span>
                          <p className="mt-1 font-semibold leading-6">{decision.title}</p>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                {!!item.claims.length && (
                  <details className="mt-6 rounded-xl bg-slate-50 p-4">
                    <summary className="cursor-pointer font-bold text-slate-800">查看依據的共享主張（{item.claims.length}）</summary>
                    <ul className="mt-3 space-y-2 text-sm leading-6 text-slate-700">
                      {item.claims.map((claim) => <li key={claim.claim_id} className="flex gap-2"><FileText className="mt-1 h-4 w-4 shrink-0 text-indigo-500" /><span>{claim.title}</span></li>)}
                    </ul>
                  </details>
                )}

                {item.candidate_state === "composition_plan_ready" && (
                  <Link href={`/admin/thought-review?tab=validation&plan=${encodeURIComponent(item.candidate_id)}`} className="mt-5 inline-flex items-center gap-1 font-semibold text-indigo-700">
                    前往審核編排計劃<ChevronRight className="h-4 w-4" />
                  </Link>
                )}
              </article>
            );
          })}
        </div>
      </div>
    </main>
  );
}
