"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { BookOpen, ChevronLeft, ChevronRight, FileText, GitBranch, Layers3, Loader2, RefreshCw } from "lucide-react";

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
  decisions: { decision_id: string; passage?: string; title: string; review: { status: string } }[];
  decision_count: number;
  scripture_navigation?: {
    located: boolean;
    source: string;
    book: string | null;
    book_code: string | null;
    book_order?: number;
    chapter: number | null;
    testament: "new" | "old" | null;
    references: string[];
  } | null;
};

type Payload = {
  title: string;
  description: string;
  scripture_candidates: Candidate[];
  topic_candidates: Candidate[];
  topic_structures: TopicStructureBatch[];
};

type TopicStructureBatch = {
  batch_id: string;
  status: string;
  summary: string;
  family_count: number;
  subtopic_count: number;
  claim_count: number;
  unassigned_claim_count: number;
  families: {
    family_id: string;
    title: string;
    organizing_question: string;
    editorial_rationale: string;
    review_state: "ai_consensus" | "human_review_required";
    claim_count: number;
    subtopic_count: number;
    subtopics: {
      subtopic_id: string;
      title: string;
      central_question: string;
      editorial_rationale: string;
      claim_count: number;
      sections: {
        section_id: string;
        title: string;
        role: string;
        purpose: string;
        claim_count: number;
        claims: { claim_id: string; title: string }[];
      }[];
    }[];
  }[];
};

const statusLabels: Record<string, string> = {
  candidate: "待審核",
  approved: "已批准",
  changes_requested: "需要修改",
  rejected: "不採用",
};

export default function ThoughtReviewCandidatesPage() {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [axis, setAxis] = useState<"scripture" | "topic" | "structure">("scripture");
  const [target, setTarget] = useState("");
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [selectedScriptureBook, setSelectedScriptureBook] = useState<string | null>(null);

  async function loadCandidates({ background = false }: { background?: boolean } = {}) {
    if (!background) setRefreshing(true);
    setError("");
    try {
      const response = await fetch(`/api/admin/thought-review/candidates?fresh=${Date.now()}`, {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache" },
      });
      if (!response.ok) throw new Error((await response.json()).detail ?? "無法載入候選內容");
      setPayload(await response.json());
      setLoadedAt(new Date());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法載入候選內容");
    } finally {
      if (!background) setRefreshing(false);
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("axis") === "topic") setAxis("topic");
    if (params.get("axis") === "structure") setAxis("structure");
    setTarget(params.get("target") ?? "");
    void loadCandidates();

    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void loadCandidates({ background: true });
    };
    window.addEventListener("focus", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.removeEventListener("focus", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, []);

  useEffect(() => {
    if (!payload || !target) return;
    const targetCandidate = payload.scripture_candidates.find((item) => item.candidate_id === target);
    if (axis === "scripture" && targetCandidate?.scripture_navigation?.book) {
      setSelectedScriptureBook(targetCandidate.scripture_navigation.book);
      return;
    }
    document.getElementById(`candidate-${target}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [payload, target, axis]);

  useEffect(() => {
    if (!payload || !target || axis !== "scripture" || !selectedScriptureBook) return;
    requestAnimationFrame(() => document.getElementById(`candidate-${target}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
  }, [payload, target, axis, selectedScriptureBook]);

  const items = useMemo(
    () => axis === "scripture" ? payload?.scripture_candidates ?? [] : axis === "topic" ? payload?.topic_candidates ?? [] : [],
    [axis, payload],
  );

  function selectAxis(nextAxis: "scripture" | "topic" | "structure") {
    setAxis(nextAxis);
    setTarget("");
    setSelectedScriptureBook(null);
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
        <header className="mt-4 flex flex-wrap items-end justify-between gap-5 rounded-3xl bg-slate-900 p-7 text-white shadow-sm">
          <div>
            <p className="text-sm font-semibold text-indigo-300">共享知識的產品出口</p>
            <h1 className="mt-2 text-3xl font-bold">{payload?.title}</h1>
            <p className="mt-3 max-w-3xl leading-7 text-slate-300">{payload?.description}</p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <button
              type="button"
              onClick={() => void loadCandidates()}
              disabled={refreshing}
              className="inline-flex items-center gap-2 rounded-xl bg-indigo-500 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-indigo-400 disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
              {refreshing ? "正在更新…" : "重新載入最新資料"}
            </button>
            {loadedAt && <p className="text-xs text-slate-400">本頁更新於 {loadedAt.toLocaleTimeString("zh-TW")}</p>}
          </div>
        </header>

        <div className="mt-6 grid gap-3 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm md:grid-cols-3">
          <button onClick={() => selectAxis("scripture")} className={`flex items-center justify-center gap-2 rounded-xl px-4 py-3 font-bold ${axis === "scripture" ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
            <BookOpen className="h-5 w-5" />釋經候選（{payload?.scripture_candidates.length ?? 0}）
          </button>
          <button onClick={() => selectAxis("topic")} className={`flex items-center justify-center gap-2 rounded-xl px-4 py-3 font-bold ${axis === "topic" ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
            <Layers3 className="h-5 w-5" />專題候選（{payload?.topic_candidates.length ?? 0}）
          </button>
          <button onClick={() => selectAxis("structure")} className={`flex items-center justify-center gap-2 rounded-xl px-4 py-3 font-bold ${axis === "structure" ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
            <GitBranch className="h-5 w-5" />專題結構（{payload?.topic_structures.reduce((total, batch) => total + batch.family_count, 0) ?? 0}）
          </button>
        </div>

        {axis === "structure" ? (
          <TopicStructureView batches={payload?.topic_structures ?? []} />
        ) : axis === "scripture" ? (
          <ScriptureCandidateView
            items={items}
            target={target}
            selectedBook={selectedScriptureBook}
            onSelectBook={setSelectedScriptureBook}
          />
        ) : (
          <div className="mt-6 space-y-5">
            {items.map((item) => <CandidateCard key={item.candidate_id} item={item} highlighted={item.candidate_id === target} />)}
          </div>
        )}
      </div>
    </main>
  );
}

function ScriptureCandidateView({
  items,
  target,
  selectedBook,
  onSelectBook,
}: {
  items: Candidate[];
  target: string;
  selectedBook: string | null;
  onSelectBook: (book: string | null) => void;
}) {
  const located = items.filter((item) => item.scripture_navigation?.located && item.scripture_navigation.book);
  const unresolved = items.filter((item) => !item.scripture_navigation?.located || !item.scripture_navigation.book);
  const books = Array.from(new Set(located.map((item) => item.scripture_navigation!.book!)))
    .map((book) => {
      const candidates = located.filter((item) => item.scripture_navigation?.book === book);
      const navigation = candidates[0].scripture_navigation!;
      return {
        book,
        testament: navigation.testament,
        order: navigation.book_order ?? 999,
        candidates,
        chapters: Array.from(new Set(candidates.map((item) => item.scripture_navigation?.chapter).filter((chapter): chapter is number => typeof chapter === "number"))),
      };
    })
    .sort((a, b) => a.order - b.order || a.book.localeCompare(b.book, "zh-Hant"));

  if (!selectedBook) {
    return (
      <div className="mt-6 space-y-8">
        {(["new", "old"] as const).map((testament) => {
          const testamentBooks = books.filter((book) => book.testament === testament);
          if (!testamentBooks.length) return null;
          return (
            <section key={testament}>
              <h2 className="text-xl font-bold text-slate-950">{testament === "new" ? "新約聖經" : "舊約聖經"}</h2>
              <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {testamentBooks.map((entry) => (
                  <button
                    key={entry.book}
                    type="button"
                    onClick={() => onSelectBook(entry.book)}
                    className="group rounded-2xl border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:border-indigo-300 hover:shadow-md"
                  >
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <h3 className="text-xl font-bold text-slate-950 group-hover:text-indigo-700">{entry.book}</h3>
                        <p className="mt-2 text-sm text-slate-500">{entry.chapters.length} 章 · {entry.candidates.length} 個釋經候選</p>
                      </div>
                      <ChevronRight className="h-5 w-5 text-slate-400 group-hover:text-indigo-600" />
                    </div>
                  </button>
                ))}
              </div>
            </section>
          );
        })}
        {!!unresolved.length && (
          <section>
            <h2 className="text-xl font-bold text-slate-950">尚待定位</h2>
            <p className="mt-1 text-sm text-slate-500">這些候選尚無足夠的結構化經文資料，沒有依標題猜測書卷。</p>
            <div className="mt-3 space-y-5">
              {unresolved.map((item) => <CandidateCard key={item.candidate_id} item={item} highlighted={item.candidate_id === target} />)}
            </div>
          </section>
        )}
      </div>
    );
  }

  const selectedItems = located
    .filter((item) => item.scripture_navigation?.book === selectedBook)
    .sort((a, b) => (a.scripture_navigation?.chapter ?? 999) - (b.scripture_navigation?.chapter ?? 999) || a.title.localeCompare(b.title, "zh-Hant"));
  const chapters = Array.from(new Set(selectedItems.map((item) => item.scripture_navigation?.chapter).filter((chapter): chapter is number => typeof chapter === "number"))).sort((a, b) => a - b);

  return (
    <div className="mt-6">
      <button type="button" onClick={() => onSelectBook(null)} className="inline-flex items-center gap-1 text-sm font-bold text-indigo-700">
        <ChevronLeft className="h-4 w-4" />返回書卷目錄
      </button>
      <div className="mt-4 border-b-2 border-sky-500 pb-3">
        <h2 className="text-3xl font-bold text-slate-950">{selectedBook}</h2>
        <p className="mt-1 text-sm text-slate-500">按章號排列，共 {selectedItems.length} 個釋經候選</p>
      </div>
      <div className="mt-7 space-y-9">
        {chapters.map((chapter) => (
          <section key={chapter}>
            <h3 className="mb-4 text-2xl font-bold text-slate-800">第 {chapter} 章</h3>
            <div className="space-y-5">
              {selectedItems.filter((item) => item.scripture_navigation?.chapter === chapter).map((item) => (
                <CandidateCard key={item.candidate_id} item={item} highlighted={item.candidate_id === target} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function CandidateCard({ item, highlighted }: { item: Candidate; highlighted: boolean }) {
  return (
    <article id={`candidate-${item.candidate_id}`} className={`scroll-mt-24 rounded-2xl border bg-white p-6 shadow-sm transition ${highlighted ? "border-indigo-400 ring-4 ring-indigo-100" : "border-slate-200"}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <span className={`inline-flex rounded-full px-3 py-1 text-xs font-bold ${item.candidate_state === "composition_plan_ready" ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>{item.candidate_state_label}</span>
          <h2 className="mt-3 text-2xl font-bold text-slate-950">{item.title}</h2>
          <p className="mt-2 leading-7 text-slate-600">{item.description}</p>
        </div>
        <div className="rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600"><strong className="block text-xl text-slate-950">{item.claim_count}</strong>條共享主張</div>
      </div>
      {!!item.canonical_topics.length && <div className="mt-4 flex flex-wrap gap-2">{item.canonical_topics.map((topic) => <span key={topic.topic_id} className="rounded-full bg-sky-50 px-3 py-1 text-sm font-semibold text-sky-800">{topic.label}</span>)}</div>}
      {!!item.decisions.length && (
        <section className="mt-6">
          <h3 className="font-bold text-slate-900">編排決定</h3>
          <div className="mt-2 divide-y divide-slate-200 border-y border-slate-200">
            {item.decisions.map((decision) => (
              <div key={decision.decision_id} className="grid gap-1 py-3 sm:grid-cols-[8rem_minmax(0,1fr)_auto] sm:items-center sm:gap-4">
                <span className="font-bold text-sky-800">{decision.passage || "經文待定位"}</span>
                <span className="font-semibold leading-6 text-slate-900">{decision.title}</span>
                <span className="text-xs font-bold text-indigo-700">{statusLabels[decision.review.status] ?? "待確認"}</span>
              </div>
            ))}
          </div>
        </section>
      )}
      {!!item.claims.length && <details className="mt-6 rounded-xl bg-slate-50 p-4"><summary className="cursor-pointer font-bold text-slate-800">查看依據的共享主張（{item.claims.length}）</summary><ul className="mt-3 space-y-2 text-sm leading-6 text-slate-700">{item.claims.map((claim) => <li key={claim.claim_id} className="flex gap-2"><FileText className="mt-1 h-4 w-4 shrink-0 text-indigo-500" /><span>{claim.title}</span></li>)}</ul></details>}
      {item.candidate_state === "composition_plan_ready" && <Link href={`/admin/thought-review?tab=validation&plan=${encodeURIComponent(item.candidate_id)}`} className="mt-5 inline-flex items-center gap-1 font-semibold text-indigo-700">前往審核編排計劃<ChevronRight className="h-4 w-4" /></Link>}
    </article>
  );
}

const roleLabels: Record<string, string> = {
  question_frame: "提出問題",
  core_thesis: "核心主張",
  scripture_evidence: "經文依據",
  reasoning: "論證推理",
  qualification: "限制與澄清",
  application: "生活應用",
  appendix: "附錄背景",
};

function TopicStructureView({ batches }: { batches: TopicStructureBatch[] }) {
  if (!batches.length) {
    return <div className="mt-6 rounded-2xl border border-amber-200 bg-amber-50 p-6 text-amber-900">尚未產生專題結構候選。</div>;
  }
  return (
    <div className="mt-6 space-y-6">
      <div className="rounded-2xl border border-sky-200 bg-sky-50 p-5 text-sky-950">
        <h2 className="font-bold">這裡顯示 AI 發現的候選層級</h2>
        <p className="mt-1 leading-7">母題是較大的研究領域；子專題回答一個集中問題；篇章段落是未來文章的寫作順序。它們尚未等同於已批准的專題文章。</p>
      </div>
      {batches.map((batch) => (
        <section key={batch.batch_id} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-7">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-sm font-bold text-indigo-700">核心語料驗證</p>
              <h2 className="mt-1 text-2xl font-bold text-slate-950">自動發現的專題結構</h2>
              {batch.summary && <p className="mt-2 max-w-4xl leading-7 text-slate-600">{batch.summary}</p>}
            </div>
            <div className="grid grid-cols-3 gap-2 text-center text-sm">
              <Metric value={batch.family_count} label="母題" />
              <Metric value={batch.subtopic_count} label="子專題" />
              <Metric value={batch.claim_count} label="主張" />
            </div>
          </div>
          <div className="mt-6 space-y-4">
            {batch.families.map((family) => (
              <details key={family.family_id} className="group rounded-2xl border border-slate-200 bg-slate-50 open:bg-white">
                <summary className="cursor-pointer list-none p-5 sm:p-6">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="flex flex-wrap gap-2">
                        <span className="rounded-full bg-indigo-100 px-3 py-1 text-xs font-bold text-indigo-800">候選母題</span>
                        <span className={`rounded-full px-3 py-1 text-xs font-bold ${family.review_state === "ai_consensus" ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"}`}>
                          {family.review_state === "ai_consensus" ? "雙模型共識" : "需要同工判斷"}
                        </span>
                      </div>
                      <h3 className="mt-3 text-xl font-bold text-slate-950">{family.title}</h3>
                      <p className="mt-2 leading-7 text-slate-600">{family.organizing_question}</p>
                    </div>
                    <span className="shrink-0 text-sm font-semibold text-slate-500">{family.subtopic_count} 個子專題 · {family.claim_count} 條主張</span>
                  </div>
                </summary>
                <div className="border-t border-slate-200 p-5 sm:p-6">
                  <p className="rounded-xl bg-slate-50 p-4 leading-7 text-slate-700"><strong>為何形成這個母題：</strong>{family.editorial_rationale}</p>
                  <div className="mt-5 space-y-4">
                    {family.subtopics.map((subtopic) => (
                      <details key={subtopic.subtopic_id} className="rounded-xl border border-indigo-100 bg-indigo-50/40">
                        <summary className="cursor-pointer list-none p-4 sm:p-5">
                          <span className="text-xs font-bold text-indigo-700">候選子專題</span>
                          <h4 className="mt-1 text-lg font-bold text-slate-950">{subtopic.title}</h4>
                          <p className="mt-2 leading-6 text-slate-600">{subtopic.central_question}</p>
                          <p className="mt-2 text-sm font-semibold text-slate-500">{subtopic.sections.length} 個篇章段落 · {subtopic.claim_count} 條主張</p>
                        </summary>
                        <div className="space-y-3 border-t border-indigo-100 p-4 sm:p-5">
                          {subtopic.sections.map((section, index) => (
                            <details key={section.section_id} className="rounded-xl border border-slate-200 bg-white p-4">
                              <summary className="cursor-pointer list-none">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                  <div>
                                    <span className="text-xs font-bold text-sky-700">篇章段落 {index + 1} · {roleLabels[section.role] ?? section.role}</span>
                                    <h5 className="mt-1 font-bold text-slate-900">{section.title}</h5>
                                    <p className="mt-1 text-sm leading-6 text-slate-600">{section.purpose}</p>
                                  </div>
                                  <span className="text-sm font-semibold text-slate-500">{section.claim_count} 條主張</span>
                                </div>
                              </summary>
                              <ul className="mt-4 space-y-2 border-t border-slate-100 pt-4 text-sm leading-6 text-slate-700">
                                {section.claims.map((claim) => <li key={claim.claim_id} className="flex gap-2"><FileText className="mt-1 h-4 w-4 shrink-0 text-indigo-500" /><span>{claim.title}</span></li>)}
                              </ul>
                            </details>
                          ))}
                        </div>
                      </details>
                    ))}
                  </div>
                </div>
              </details>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function Metric({ value, label }: { value: number; label: string }) {
  return <div className="min-w-20 rounded-xl bg-slate-100 px-3 py-2"><strong className="block text-lg text-slate-950">{value}</strong><span className="text-slate-500">{label}</span></div>;
}
