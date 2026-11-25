"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronRight, Play, AlertTriangle, CheckCircle2, GitBranch, List } from "lucide-react";

type Quote = { text: string; source_id: string; lecture_label: string; paragraph: string; start: number | null };
type FidelityWarn = { id: string; kind: string; verdict: string; detail: string };
type GraphNode = {
  id: string;
  kind: string;
  kind_label: string;
  point_no: number | null;
  question_no: number | null;
  statement: string;
  source_id: string;
  lecture_label: string;
  start: number | null;
  quotes: Quote[];
  fidelity_warning: FidelityWarn | null;
};
type GraphEdge = {
  from_id: string;
  to_id: string;
  relation: string;
  relation_label: string;
  reason: string;
  attribution: string;
};
type Incoming = {
  kind_label: string;
  relation: string;
  from_point_no: number | null;
  statement: string;
  reason: string;
  quotes: Quote[];
  fidelity_warning: FidelityWarn | null;
};
type Outgoing = {
  relation_label: string;
  to_point_no: number | null;
  to_question_no: number | null;
  to_statement: string;
  reason: string;
};
type Point = {
  no: number;
  statement: string;
  source_id: string;
  lecture_label: string;
  start: number | null;
  quotes: Quote[];
  incoming: Incoming[];
  outgoing: Outgoing[];
  fidelity_warning: FidelityWarn | null;
};
type QuestionItem = {
  no: number;
  statement: string;
  source_id: string;
  lecture_label: string;
  start: number | null;
  state: string;
  remaining: string;
  answers: { point_no: number | null; statement: string; reason: string; lecture_label: string }[];
};
type MediaInfo = { source_id: string; label: string; title: string; media_url: string | null; duration: number | null };
type TranscriptLecture = {
  source_id: string;
  lecture_label: string;
  title: string;
  paragraphs: { paragraph: string; pieces: { text: string; start: number | null; uses: number[] }[] }[];
};
type UnitDetail = {
  series_id: string;
  series_title: string;
  unit_id: string;
  title: string;
  passage: string;
  chars: number;
  cross_lecture: boolean;
  media: Record<string, MediaInfo>;
  questions: QuestionItem[];
  points: Point[];
  graph: { nodes: Record<string, GraphNode>; edges: GraphEdge[] };
  transcript: TranscriptLecture[];
  fidelity: { total: number; faithful: number; changed: number; added: number; findings: FidelityWarn[] } | null;
  roundtrip: {
    metrics: { core_units_covered: number; core_units_total: number; distortion_count: number; omission_count: number; overall_assessment: string };
    omissions: { severity: string; explanation: string; original_excerpt: string }[];
    distortions: { severity: string; explanation: string }[];
  } | null;
  ruling: { verdict: "approved" | "needs_revision"; note: string; at: string } | null;
};

function clock(seconds: number | null): string {
  if (seconds == null) return "";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`;
}

const BADGE_STYLE: Record<string, string> = {
  question: "bg-violet-100 text-violet-800 border-violet-200",
  claim: "bg-indigo-100 text-indigo-800 border-indigo-200",
  evidence: "bg-emerald-50 text-emerald-800 border-emerald-200",
  observation: "bg-sky-50 text-sky-800 border-sky-200",
  example: "bg-amber-50 text-amber-800 border-amber-200",
  position: "bg-rose-50 text-rose-800 border-rose-200",
};

const REL_STYLE: Record<string, string> = {
  answers: "bg-violet-600 text-white",
  supports: "bg-emerald-600 text-white",
  illustrates: "bg-amber-600 text-white",
  refutes: "bg-rose-600 text-white",
  qualifies: "bg-sky-600 text-white",
  contextualizes: "bg-slate-500 text-white",
};

export default function UnitReviewPage() {
  const params = useParams<{ seriesId: string; unitId: string }>();
  const seriesId = decodeURIComponent(params.seriesId);
  const unitId = decodeURIComponent(params.unitId);
  const [unit, setUnit] = useState<UnitDetail | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetch(`/api/admin/lecture-argument/series/${encodeURIComponent(seriesId)}/units/${encodeURIComponent(unitId)}`, { cache: "no-store" })
      .then(async (r) => (r.ok ? r.json() : Promise.reject(new Error((await r.json().catch(() => ({}))).detail ?? `讀取失敗（${r.status}）`))))
      .then(setUnit)
      .catch((e) => setError(String(e.message ?? e)));
  }, [seriesId, unitId]);
  useEffect(load, [load]);

  if (error) return <main className="mx-auto max-w-4xl px-4 py-6 text-rose-700">{error}</main>;
  if (!unit) return <main className="mx-auto max-w-4xl px-4 py-6 text-slate-400">載入中…</main>;
  return <UnitReviewView unit={unit} reload={load} />;
}

type TreeChild = {
  edge: GraphEdge;
  node: GraphNode;
  direction: "in" | "refuted_position";
};

function UnitReviewView({ unit, reload }: { unit: UnitDetail; reload: () => void }) {
  const mediaList = Object.values(unit.media);
  const [activeSourceId, setActiveSourceId] = useState<string>(mediaList[0]?.source_id ?? "");
  const audioRef = useRef<HTMLAudioElement>(null);
  const pendingSeek = useRef<number | null>(null);
  const [viewMode, setViewMode] = useState<"tree" | "flat">("tree");
  const [openFlat, setOpenFlat] = useState<Set<number>>(new Set([1]));
  const [leftOutOnly, setLeftOutOnly] = useState(false);
  const [showChecks, setShowChecks] = useState(false);
  const [note, setNote] = useState(unit.ruling?.note ?? "");
  const [saving, setSaving] = useState(false);

  const activeMedia = unit.media[activeSourceId] ?? mediaList[0];

  const playAt = useCallback((sourceId: string, seconds: number | null) => {
    if (seconds == null) return;
    const target = Math.max(seconds - 1, 0);
    if (sourceId !== activeSourceId) {
      pendingSeek.current = target;
      setActiveSourceId(sourceId);
      return;
    }
    const el = audioRef.current;
    if (!el) return;
    el.currentTime = target;
    void el.play().catch(() => undefined);
  }, [activeSourceId]);

  useEffect(() => {
    const el = audioRef.current;
    if (!el || pendingSeek.current == null) return;
    const target = pendingSeek.current;
    const onReady = () => {
      el.currentTime = target;
      pendingSeek.current = null;
      void el.play().catch(() => undefined);
    };
    el.addEventListener("loadedmetadata", onReady, { once: true });
    el.load();
    return () => el.removeEventListener("loadedmetadata", onReady);
  }, [activeSourceId]);

  // Build hierarchical argument trees from the graph:
  // Roots = top-level questions/claims that do not point upward into another claim/question via supports/answers.
  const { childrenByTarget, mainTrees, sideTrees } = useMemo(() => {
    const nodes = unit.graph?.nodes ?? {};
    const edges = unit.graph?.edges ?? [];
    const childMap: Record<string, TreeChild[]> = {};
    const hasUpwardParent = new Set<string>();

    for (const e of edges) {
      const src = nodes[e.from_id];
      const dst = nodes[e.to_id];
      if (!src || !dst) continue;
      if (e.relation === "refutes" && dst.kind === "position") {
        // Attach the refuted external position under the refuting claim so it reads top-down.
        (childMap[e.from_id] ??= []).push({ edge: e, node: dst, direction: "refuted_position" });
        hasUpwardParent.add(e.to_id);
      } else {
        (childMap[e.to_id] ??= []).push({ edge: e, node: src, direction: "in" });
        hasUpwardParent.add(e.from_id);
      }
    }

    // Count reachable subtree size to distinguish main argument trees from side branches.
    const countSubtree = (id: string, seen = new Set<string>()): number => {
      if (seen.has(id)) return 0;
      seen.add(id);
      let total = 1;
      for (const ch of childMap[id] ?? []) {
        total += countSubtree(ch.node.id, seen);
      }
      return total;
    };

    const roots = Object.values(nodes)
      .filter((n) => !hasUpwardParent.has(n.id) && (n.kind === "question" || n.kind === "claim" || (childMap[n.id]?.length ?? 0) > 0))
      .map((n) => ({ node: n, size: countSubtree(n.id) }))
      .sort((a, b) => b.size - a.size);

    // Fallback: if a cycle prevented any root, pick the first question or highest-indegree claim.
    if (roots.length === 0 && Object.keys(nodes).length > 0) {
      const first = Object.values(nodes).find((n) => n.kind === "question") ?? Object.values(nodes)[0];
      roots.push({ node: first, size: Object.keys(nodes).length });
    }

    const main = roots.filter((r) => r.size >= 5 || r.node.kind === "question");
    const side = roots.filter((r) => !main.includes(r));
    return { childrenByTarget: childMap, mainTrees: main, sideTrees: side };
  }, [unit.graph]);

  const submitRuling = async (verdict: "approved" | "needs_revision") => {
    setSaving(true);
    try {
      const r = await fetch(
        `/api/admin/lecture-argument/series/${encodeURIComponent(unit.series_id)}/units/${encodeURIComponent(unit.unit_id)}/rulings`,
        { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ verdict, note }) }
      );
      if (r.ok) reload();
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto max-w-4xl px-4 pb-28">
      {/* Sticky header + multi-lecture audio player */}
      <div className="sticky top-0 z-10 -mx-4 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <Link href={`/admin/wang/series/${encodeURIComponent(unit.series_id)}`} className="inline-flex items-center gap-1 text-slate-500 hover:text-slate-800">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />{unit.series_title}
          </Link>
          <span className="text-xs text-slate-500">{unit.passage} · 約 {unit.chars.toLocaleString()} 字</span>
        </div>
        <h1 className="mt-1 text-lg font-bold text-slate-900">{unit.title}</h1>
        {mediaList.length > 1 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {mediaList.map((m) => (
              <button
                key={m.source_id}
                onClick={() => setActiveSourceId(m.source_id)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium ${m.source_id === activeSourceId ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
              >
                {m.label}錄音
              </button>
            ))}
          </div>
        )}
        {activeMedia?.media_url ? (
          <audio ref={audioRef} src={activeMedia.media_url} controls preload="metadata" className="mt-2 w-full" />
        ) : (
          <p className="mt-2 text-xs text-slate-500">{activeMedia?.label ?? "本單元"}無可用錄音檔。</p>
        )}
      </div>

      {/* Machine dual-check disclosure */}
      {(unit.fidelity || unit.roundtrip) && (
        <section className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-semibold text-slate-800">機器雙校驗參考</span>
              {unit.fidelity && (
                <span className={unit.fidelity.changed + unit.fidelity.added > 0 ? "text-amber-700 font-medium" : "text-emerald-700 font-medium"}>
                  逐點忠實度：{unit.fidelity.faithful}/{unit.fidelity.total}（改寫 {unit.fidelity.changed}、多加 {unit.fidelity.added}）
                </span>
              )}
              {unit.roundtrip && (
                <span className={unit.roundtrip.metrics.distortion_count > 0 ? "text-rose-700 font-medium" : "text-emerald-700 font-medium"}>
                  盲測重構：{unit.roundtrip.metrics.core_units_covered}/{unit.roundtrip.metrics.core_units_total} 核心覆蓋，{unit.roundtrip.metrics.distortion_count} 扭曲
                </span>
              )}
            </div>
            <button onClick={() => setShowChecks((v) => !v)} className="text-xs font-medium text-indigo-700 underline">
              {showChecks ? "收起詳情" : "展開細節提示"}
            </button>
          </div>
          {showChecks && (
            <div className="mt-3 space-y-2 border-t border-slate-200 pt-3 text-xs leading-5 text-slate-700">
              {unit.fidelity?.findings.map((f, i) => (
                <p key={i} className="rounded bg-amber-50 p-2 text-amber-900">
                  <b>忠實度提醒（{f.verdict === "added" ? "多加內容" : "改寫判斷"}）</b>：{f.detail}
                </p>
              ))}
              {unit.roundtrip?.metrics.overall_assessment && (
                <p className="text-slate-600">{unit.roundtrip.metrics.overall_assessment}</p>
              )}
              {unit.roundtrip?.omissions.map((o, i) => (
                <p key={i} className="text-slate-600">• 細節遺漏（{o.severity}）：{o.explanation}</p>
              ))}
            </div>
          )}
        </section>
      )}

      {/* View Mode Switcher */}
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
        <div>
          <h2 className="text-xl font-bold text-slate-900">論證結構</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            自上而下展開：從頂層問題／總結論往下追每一層支線、依據、例證與反方看法；每一層皆可直接展開原話與播放錄音。
          </p>
        </div>
        <div className="inline-flex rounded-lg border border-slate-200 bg-slate-100 p-0.5 text-xs font-semibold">
          <button
            onClick={() => setViewMode("tree")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 ${viewMode === "tree" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600 hover:text-slate-900"}`}
          >
            <GitBranch className="h-3.5 w-3.5" />層級論證樹
          </button>
          <button
            onClick={() => setViewMode("flat")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 ${viewMode === "flat" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600 hover:text-slate-900"}`}
          >
            <List className="h-3.5 w-3.5" />時間順序清單（{unit.points.length}）
          </button>
        </div>
      </div>

      {/* Hierarchical Argument Tree View */}
      {viewMode === "tree" && (
        <section className="mt-5 space-y-6">
          {mainTrees.map(({ node, size }) => (
            <div key={node.id} className="rounded-xl border border-indigo-200 bg-white p-4 shadow-xs">
              <div className="mb-3 flex items-center justify-between text-xs text-slate-500">
                <span className="font-semibold text-indigo-700">主論證樹（共 {size} 個節點匯入）</span>
                <span>點左側箭頭可收放支線</span>
              </div>
              <ArgumentTreeNode
                node={node}
                childrenByTarget={childrenByTarget}
                playAt={playAt}
                depth={0}
                ancestors={new Set([node.id])}
              />
            </div>
          ))}

          {sideTrees.length > 0 && (
            <div className="space-y-4">
              <h3 className="text-sm font-bold text-slate-700">本單元內的獨立旁支與方法原則（{sideTrees.length} 組）</h3>
              {sideTrees.map(({ node, size }) => (
                <div key={node.id} className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                  <div className="mb-2 text-xs text-slate-500">獨立小樹（{size} 個節點）</div>
                  <ArgumentTreeNode
                    node={node}
                    childrenByTarget={childrenByTarget}
                    playAt={playAt}
                    depth={0}
                    ancestors={new Set([node.id])}
                  />
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Flat Chronological List View */}
      {viewMode === "flat" && (
        <section className="mt-5 space-y-3">
          <div className="flex justify-end gap-2 text-xs">
            <button onClick={() => setOpenFlat(new Set(unit.points.map((p) => p.no)))} className="text-indigo-700 underline">全部展開</button>
            <button onClick={() => setOpenFlat(new Set())} className="text-slate-500 underline">全部收起</button>
          </div>
          {unit.points.map((p) => {
            const isOpen = openFlat.has(p.no);
            return (
              <div key={p.no} className="rounded-xl border border-slate-200 bg-white">
                <button
                  onClick={() => setOpenFlat((s) => { const n = new Set(s); if (n.has(p.no)) n.delete(p.no); else n.add(p.no); return n; })}
                  className="flex w-full items-start gap-2 px-4 py-3 text-left hover:bg-slate-50"
                >
                  {isOpen ? <ChevronDown className="mt-1 h-4 w-4 shrink-0 text-slate-400" /> : <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-400" />}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-bold text-indigo-700">第 {p.no} 點</span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{p.lecture_label}</span>
                      {p.fidelity_warning && (
                        <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-900">
                          <AlertTriangle className="h-3 w-3" />忠實度待核對
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-sm font-medium leading-6 text-slate-900">{p.statement}</p>
                  </div>
                </button>
                {isOpen && (
                  <div className="space-y-3 border-t border-slate-100 px-4 py-3 text-sm">
                    {p.quotes.map((q, i) => (
                      <div key={i} className="rounded bg-slate-50 px-3 py-2 text-xs leading-6 text-slate-700">
                        <button onClick={() => playAt(q.source_id, q.start)} className="mr-2 inline-flex items-center gap-1 rounded bg-white px-1.5 py-0.5 font-medium text-indigo-700 ring-1 ring-slate-200">
                          <Play className="h-3 w-3" />{q.lecture_label} {clock(q.start)}
                        </button>
                        「{q.text}」
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </section>
      )}

      {/* Owner acceptance ruling */}
      <section className="mt-10 rounded-xl border-2 border-indigo-200 bg-white p-5">
        <h2 className="text-base font-bold text-slate-900">人工驗收裁定</h2>
        {unit.ruling && (
          <p className="mt-1 text-xs text-slate-600">
            目前狀態：
            <b className={unit.ruling.verdict === "approved" ? "text-emerald-700" : "text-rose-700"}>
              {unit.ruling.verdict === "approved" ? "已通過驗收" : "需要修改"}
            </b>
            （{unit.ruling.at}）{unit.ruling.note ? ` — ${unit.ruling.note}` : ""}
          </p>
        )}
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          placeholder="可填驗收備註（例如哪一點要微調、或確認無誤）"
          className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            disabled={saving}
            onClick={() => submitRuling("approved")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            <CheckCircle2 className="h-4 w-4" />通過驗收
          </button>
          <button
            disabled={saving}
            onClick={() => submitRuling("needs_revision")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:opacity-50"
          >
            <AlertTriangle className="h-4 w-4" />需要修改
          </button>
        </div>
      </section>

      {/* Multi-lecture transcript with highlight/gray-out */}
      <section id="transcript" className="mt-10">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-xl font-bold text-slate-900">本單元完整逐字稿</h2>
          <label className="inline-flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={leftOutOnly} onChange={(e) => setLeftOutOnly(e.target.checked)} />
            只看沒整理進任何一點的話
          </label>
        </div>
        <p className="mt-1 text-xs text-slate-500">有底色的句子已被上方論證引用，灰字為未引用原話；點任一句可直接播放錄音。</p>

        <div className="mt-4 space-y-6">
          {unit.transcript.map((lec) => (
            <div key={lec.source_id} className="rounded-xl border border-slate-200 bg-white p-4">
              <h3 className="text-sm font-bold text-indigo-800">{lec.lecture_label} · {lec.title}</h3>
              <div className="mt-3 space-y-3 text-sm leading-7">
                {lec.paragraphs.map((para) => {
                  const pieces = leftOutOnly ? para.pieces.filter((pc) => pc.uses.length === 0) : para.pieces;
                  if (pieces.length === 0) return null;
                  return (
                    <p key={para.paragraph}>
                      <span className="mr-2 font-mono text-xs text-slate-400">{para.paragraph}</span>
                      {pieces.map((pc, pi) => (
                        <span
                          key={pi}
                          onClick={() => playAt(lec.source_id, pc.start)}
                          title={pc.uses.length ? `用於第 ${pc.uses.join("、")} 點` : "未引用"}
                          className={`cursor-pointer rounded px-0.5 ${pc.uses.length ? "bg-amber-100/70 text-slate-900 hover:bg-amber-200" : "text-slate-400 hover:text-slate-700"}`}
                        >
                          {pc.text}
                        </span>
                      ))}
                    </p>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}

function ArgumentTreeNode({
  node,
  edge,
  childrenByTarget,
  playAt,
  depth,
  ancestors,
}: {
  node: GraphNode;
  edge?: GraphEdge;
  childrenByTarget: Record<string, TreeChild[]>;
  playAt: (sourceId: string, seconds: number | null) => void;
  depth: number;
  ancestors: Set<string>;
}) {
  const children = (childrenByTarget[node.id] ?? []).filter((ch) => !ancestors.has(ch.node.id));
  const [expanded, setExpanded] = useState(depth < 2);
  const [showQuotes, setShowQuotes] = useState(false);

  const badgeCls = BADGE_STYLE[node.kind] ?? "bg-slate-100 text-slate-700 border-slate-200";
  const relCls = edge ? (REL_STYLE[edge.relation] ?? "bg-slate-600 text-white") : "";

  return (
    <div className={depth > 0 ? "mt-2.5 border-l-2 border-indigo-100 pl-3.5" : ""}>
      <div className="rounded-lg border border-slate-200 bg-white p-3">
        {/* Incoming edge relation + reason */}
        {edge && (
          <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs">
            <span className={`rounded px-1.5 py-0.5 font-semibold ${relCls}`}>
              {edge.relation_label}
            </span>
            <span className="text-slate-600">{edge.reason}</span>
          </div>
        )}

        {/* Node header + statement */}
        <div className="flex items-start gap-2">
          {children.length > 0 ? (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="mt-0.5 rounded p-0.5 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
              aria-label={expanded ? "收起支線" : "展開支線"}
            >
              {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </button>
          ) : (
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
          )}

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className={`rounded border px-1.5 py-0.5 font-semibold ${badgeCls}`}>
                {node.kind_label}
                {node.point_no ? ` #${node.point_no}` : node.question_no ? ` #${node.question_no}` : ""}
              </span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{node.lecture_label}</span>
              {node.start != null && (
                <button
                  onClick={() => playAt(node.source_id, node.start)}
                  className="inline-flex items-center gap-0.5 rounded bg-indigo-50 px-1.5 py-0.5 font-medium text-indigo-700 hover:bg-indigo-100"
                >
                  <Play className="h-2.5 w-2.5" />{clock(node.start)}
                </button>
              )}
              {node.quotes.length > 0 && (
                <button
                  onClick={() => setShowQuotes((v) => !v)}
                  className="text-slate-500 underline hover:text-slate-800"
                >
                  {showQuotes ? "收起原話" : `看原話（${node.quotes.length}）`}
                </button>
              )}
              {children.length > 0 && !expanded && (
                <button onClick={() => setExpanded(true)} className="text-indigo-600 underline">
                  展開 {children.length} 條下層支線
                </button>
              )}
              {node.fidelity_warning && (
                <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-900">
                  <AlertTriangle className="h-3 w-3" />忠實度提醒
                </span>
              )}
            </div>

            <p className="mt-1 text-sm leading-6 text-slate-900">{node.statement}</p>

            {node.fidelity_warning && (
              <p className="mt-2 rounded border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs leading-5 text-amber-900">
                <b>忠實度提醒（{node.fidelity_warning.verdict === "added" ? "疑似多加" : "疑似改寫"}）</b>：{node.fidelity_warning.detail}
              </p>
            )}

            {showQuotes && node.quotes.length > 0 && (
              <div className="mt-2 space-y-1.5 border-t border-slate-100 pt-2">
                {node.quotes.map((q, i) => (
                  <div key={i} className="rounded bg-slate-50 px-2.5 py-1.5 text-xs leading-5 text-slate-700">
                    <button
                      onClick={() => playAt(q.source_id, q.start)}
                      className="mr-1.5 inline-flex items-center gap-0.5 rounded bg-white px-1.5 py-0.5 text-indigo-700 ring-1 ring-slate-200 hover:bg-indigo-50"
                    >
                      <Play className="h-2.5 w-2.5" />{q.lecture_label} {clock(q.start)}
                    </button>
                    「{q.text}」
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {expanded && children.length > 0 && (
        <div>
          {children.map((ch, idx) => {
            const nextAnc = new Set(ancestors);
            nextAnc.add(ch.node.id);
            return (
              <ArgumentTreeNode
                key={`${ch.node.id}-${idx}`}
                node={ch.node}
                edge={ch.edge}
                childrenByTarget={childrenByTarget}
                playAt={playAt}
                depth={depth + 1}
                ancestors={nextAnc}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
