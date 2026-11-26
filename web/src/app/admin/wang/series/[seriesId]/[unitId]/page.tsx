"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  ChevronDown,
  ChevronRight,
  Play,
  AlertTriangle,
  CheckCircle2,
  GitBranch,
  List,
  Network,
} from "lucide-react";
import { ArgumentDagMap } from "./ArgumentDagMap";

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

function nodeDisplayLabel(n: GraphNode): string {
  return `${n.id} ${n.kind_label}`;
}

function formatReason(reason: string, _nodes: Record<string, GraphNode>): string {
  return reason || "";
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

  if (error) return <main className="mx-auto max-w-5xl px-4 py-6 text-rose-700">{error}</main>;
  if (!unit) return <main className="mx-auto max-w-5xl px-4 py-6 text-slate-400">載入中…</main>;
  return <UnitReviewView unit={unit} reload={load} />;
}

type TreeChild = { edge: GraphEdge; node: GraphNode };
type ComponentInfo = {
  root: GraphNode;
  nodeIds: Set<string>;
  totalNodes: number;
  secondaryCount: number;
  isMain: boolean;
};

function UnitReviewView({ unit, reload }: { unit: UnitDetail; reload: () => void }) {
  const mediaList = Object.values(unit.media);
  const [activeSourceId, setActiveSourceId] = useState<string>(mediaList[0]?.source_id ?? "");
  const audioRef = useRef<HTMLAudioElement>(null);
  const pendingSeek = useRef<number | null>(null);
  const localPanelRef = useRef<HTMLDivElement>(null);

  const [viewMode, setViewMode] = useState<"explore" | "tree" | "flat">("explore");
  const [treeExpandMode, setTreeExpandMode] = useState<"spine" | "all" | "none">("spine");
  const [treeResetKey, setTreeResetKey] = useState(0);
  const [showSideInTree, setShowSideInTree] = useState(false);
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

  // Connected-component graph analysis
  const {
    nodes,
    edges,
    childrenByTarget,
    parentsBySource,
    secondaryBySource,
    mainComponents,
    sideComponents,
  } = useMemo(() => {
    const gNodes = unit.graph?.nodes ?? {};
    const gEdges = unit.graph?.edges ?? [];
    const childMap: Record<string, TreeChild[]> = {};
    const parentMap: Record<string, { edge: GraphEdge; node: GraphNode }[]> = {};
    const outCount: Record<string, number> = {};
    const undirected: Record<string, Set<string>> = {};

    for (const id of Object.keys(gNodes)) undirected[id] = new Set();
    for (const e of gEdges) {
      if (!gNodes[e.from_id] || !gNodes[e.to_id]) continue;
      outCount[e.from_id] = (outCount[e.from_id] ?? 0) + 1;
      undirected[e.from_id].add(e.to_id);
      undirected[e.to_id].add(e.from_id);
    }

    const hasUpwardParent = new Set<string>();
    for (const e of gEdges) {
      const src = gNodes[e.from_id];
      const dst = gNodes[e.to_id];
      if (!src || !dst) continue;
      if (e.relation === "refutes" && dst.kind === "position" && (outCount[dst.id] ?? 0) === 0) {
        (childMap[e.from_id] ??= []).push({ edge: e, node: dst });
        (parentMap[e.to_id] ??= []).push({ edge: e, node: src });
        hasUpwardParent.add(e.to_id);
      } else {
        (childMap[e.to_id] ??= []).push({ edge: e, node: src });
        (parentMap[e.from_id] ??= []).push({ edge: e, node: dst });
        hasUpwardParent.add(e.from_id);
      }
    }

    const collectReachable = (rootId: string): Set<string> => {
      const seen = new Set<string>();
      const stack = [rootId];
      while (stack.length > 0) {
        const cur = stack.pop()!;
        if (seen.has(cur)) continue;
        seen.add(cur);
        for (const ch of childMap[cur] ?? []) {
          if (!seen.has(ch.node.id)) stack.push(ch.node.id);
        }
      }
      return seen;
    };

    const visited = new Set<string>();
    const rawComponents: string[][] = [];
    for (const id of Object.keys(gNodes)) {
      if (visited.has(id)) continue;
      const comp: string[] = [];
      const queue = [id];
      while (queue.length > 0) {
        const cur = queue.shift()!;
        if (visited.has(cur)) continue;
        visited.add(cur);
        comp.push(cur);
        for (const nxt of undirected[cur] ?? []) {
          if (!visited.has(nxt)) queue.push(nxt);
        }
      }
      rawComponents.push(comp);
    }
    rawComponents.sort((a, b) => b.length - a.length);

    const secondaryMap: Record<string, TreeChild[]> = {};
    const comps: ComponentInfo[] = [];

    for (const comp of rawComponents) {
      if (comp.length === 1 && !["question", "claim"].includes(gNodes[comp[0]].kind)) continue;
      const compSet = new Set(comp);
      const sinks = comp.filter((id) => !hasUpwardParent.has(id));
      const candidates = sinks.length > 0 ? sinks : comp;

      let bestRoot = candidates[0];
      let bestReach = collectReachable(bestRoot);
      for (const cid of candidates.slice(1)) {
        const r = collectReachable(cid);
        const better =
          r.size > bestReach.size ||
          (r.size === bestReach.size && gNodes[cid].kind === "question" && gNodes[bestRoot].kind !== "question");
        if (better) {
          bestRoot = cid;
          bestReach = r;
        }
      }

      const secondarySinks = new Set(sinks.filter((id) => id !== bestRoot && !bestReach.has(id)));
      for (const e of gEdges) {
        if (!compSet.has(e.from_id) || !compSet.has(e.to_id)) continue;
        if (bestReach.has(e.from_id) && secondarySinks.has(e.to_id)) {
          const list = (secondaryMap[e.from_id] ??= []);
          if (!list.some((item) => item.node.id === e.to_id && item.edge.relation === e.relation)) {
            list.push({ edge: e, node: gNodes[e.to_id] });
          }
        }
      }

      comps.push({
        root: gNodes[bestRoot],
        nodeIds: compSet,
        totalNodes: comp.length,
        secondaryCount: secondarySinks.size,
        isMain: comp.length >= 6 || (comps.length === 0 && comp.length >= 2),
      });
    }

    return {
      nodes: gNodes,
      edges: gEdges,
      childrenByTarget: childMap,
      parentsBySource: parentMap,
      secondaryBySource: secondaryMap,
      mainComponents: comps.filter((c) => c.isMain),
      sideComponents: comps.filter((c) => !c.isMain),
    };
  }, [unit.graph]);

  const primaryComp = mainComponents[0] ?? sideComponents[0] ?? null;
  const [selectedId, setSelectedId] = useState<string>(primaryComp?.root.id ?? "");

  // Keep selectedId valid if unit changes
  useEffect(() => {
    if (primaryComp && (!selectedId || !nodes[selectedId])) {
      setSelectedId(primaryComp.root.id);
    }
  }, [primaryComp, selectedId, nodes]);

  const selectNodeAndScroll = useCallback((id: string, shouldScroll = false) => {
    setSelectedId(id);
    if (shouldScroll) {
      setTimeout(() => {
        localPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 40);
    }
  }, []);

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
    <main className="mx-auto max-w-5xl px-4 pb-24">
      {/* Compact sticky header + audio player */}
      <div className="sticky top-0 z-20 -mx-4 border-b border-slate-200 bg-white/95 px-4 py-2 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Link href={`/admin/wang/series/${encodeURIComponent(unit.series_id)}`} className="inline-flex items-center gap-1 text-slate-500 hover:text-slate-800">
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />{unit.series_title}
            </Link>
            <span className="text-slate-300">/</span>
            <h1 className="font-bold text-slate-900">{unit.title}</h1>
            <span className="text-xs text-slate-500">({unit.passage} · {unit.chars.toLocaleString()} 字)</span>
          </div>
          {mediaList.length > 1 && (
            <div className="flex flex-wrap gap-1">
              {mediaList.map((m) => (
                <button
                  key={m.source_id}
                  onClick={() => setActiveSourceId(m.source_id)}
                  className={`rounded px-2 py-0.5 text-xs font-medium ${m.source_id === activeSourceId ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          )}
        </div>
        {activeMedia?.media_url ? (
          <audio ref={audioRef} src={activeMedia.media_url} controls preload="metadata" className="mt-1.5 h-8 w-full" />
        ) : (
          <p className="mt-1 text-xs text-slate-500">{activeMedia?.label ?? "本單元"}無可用錄音檔。</p>
        )}
      </div>

      {/* Machine dual-check disclosure */}
      {(unit.fidelity || unit.roundtrip) && (
        <section className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-3 text-xs sm:text-sm">
              <span className="font-semibold text-slate-800">機器雙校驗</span>
              {unit.fidelity && (
                <span className={unit.fidelity.changed + unit.fidelity.added > 0 ? "font-medium text-amber-700" : "font-medium text-emerald-700"}>
                  逐點忠實度：{unit.fidelity.faithful}/{unit.fidelity.total}（改寫 {unit.fidelity.changed}、多加 {unit.fidelity.added}）
                </span>
              )}
              {unit.roundtrip && (
                <span className={unit.roundtrip.metrics.distortion_count > 0 ? "font-medium text-rose-700" : "font-medium text-emerald-700"}>
                  盲測重構：{unit.roundtrip.metrics.core_units_covered}/{unit.roundtrip.metrics.core_units_total} 核心覆蓋，{unit.roundtrip.metrics.distortion_count} 扭曲
                </span>
              )}
            </div>
            <button onClick={() => setShowChecks((v) => !v)} className="text-xs font-medium text-indigo-700 underline">
              {showChecks ? "收起詳情" : "展開細節提示"}
            </button>
          </div>
          {showChecks && (
            <div className="mt-2.5 space-y-1.5 border-t border-slate-200 pt-2.5 text-xs leading-5 text-slate-700">
              {unit.fidelity?.findings.map((f, i) => (
                <p key={i} className="rounded bg-amber-50 p-2 text-amber-900">
                  <b>忠實度提醒（{f.verdict === "added" ? "疑似多加" : "疑似改寫"}）</b>：{f.detail}
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
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
        <div>
          <h2 className="text-lg font-bold text-slate-900">論證結構</h2>
          <p className="text-xs text-slate-500">
            先從全局論證地圖看清支線匯聚，點任一節點切入下方局部詳情查對原話與錄音。
          </p>
        </div>
        <div className="inline-flex rounded-lg border border-slate-200 bg-slate-100 p-0.5 text-xs font-semibold">
          <button
            onClick={() => setViewMode("explore")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 ${viewMode === "explore" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600 hover:text-slate-900"}`}
          >
            <Network className="h-3.5 w-3.5" />全局 → 局部
          </button>
          <button
            onClick={() => setViewMode("tree")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 ${viewMode === "tree" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600 hover:text-slate-900"}`}
          >
            <GitBranch className="h-3.5 w-3.5" />完整樹（去重）
          </button>
          <button
            onClick={() => setViewMode("flat")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 ${viewMode === "flat" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600 hover:text-slate-900"}`}
          >
            <List className="h-3.5 w-3.5" />時間清單（{unit.points.length}）
          </button>
        </div>
      </div>

      {/* MODE 1: Global -> Local Navigation */}
      {viewMode === "explore" && primaryComp && (
        <section className="mt-4 space-y-4">
          {/* Global DAG Map */}
          <ArgumentDagMap
            nodes={nodes}
            edges={edges}
            rootId={primaryComp.root.id}
            componentIds={primaryComp.nodeIds}
            selectedId={selectedId}
            onSelect={(id) => selectNodeAndScroll(id, true)}
          />

          {/* Compact Side Branches Strip */}
          {sideComponents.length > 0 && (
            <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-semibold text-slate-600">獨立旁支（無主網連邊，共 {sideComponents.length} 組）：</span>
                {sideComponents.map((sc) => {
                  const active = sc.nodeIds.has(selectedId);
                  return (
                    <button
                      key={sc.root.id}
                      onClick={() => selectNodeAndScroll(sc.root.id, true)}
                      className={`rounded-full border px-2.5 py-1 text-left transition ${
                        active
                          ? "border-indigo-500 bg-indigo-600 font-semibold text-white"
                          : "border-slate-300 bg-white text-slate-700 hover:border-indigo-300 hover:text-indigo-700"
                      }`}
                    >
                      {nodeDisplayLabel(sc.root)}：{sc.root.statement.slice(0, 14)}…（{sc.totalNodes}）
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Local Focused Inspector */}
          {selectedId && nodes[selectedId] && (
            <div ref={localPanelRef}>
              <LocalNodeInspector
                node={nodes[selectedId]}
                nodes={nodes}
                parents={parentsBySource[selectedId] ?? []}
                childrenList={childrenByTarget[selectedId] ?? []}
                secondaryTargets={secondaryBySource[selectedId] ?? []}
                childrenByTarget={childrenByTarget}
                onSelectNode={(id) => selectNodeAndScroll(id, false)}
                playAt={playAt}
                rootId={primaryComp.root.id}
              />
            </div>
          )}
        </section>
      )}

      {/* MODE 2: Deduplicated Full Argument Tree */}
      {viewMode === "tree" && (
        <section className="mt-4 space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="text-slate-500">共享多父節點僅於首次出現處展開，其餘處改顯示跳轉引用，避免重複。</span>
            <div className="flex gap-2">
              <button
                onClick={() => { setTreeExpandMode("spine"); setTreeResetKey((k) => k + 1); }}
                className={`rounded px-2 py-1 font-medium ${treeExpandMode === "spine" ? "bg-indigo-100 text-indigo-800" : "text-indigo-700 hover:bg-slate-100"}`}
              >
                只看主幹（前 2 層）
              </button>
              <button
                onClick={() => { setTreeExpandMode("all"); setTreeResetKey((k) => k + 1); }}
                className={`rounded px-2 py-1 font-medium ${treeExpandMode === "all" ? "bg-indigo-100 text-indigo-800" : "text-indigo-700 hover:bg-slate-100"}`}
              >
                全部展開
              </button>
              <button
                onClick={() => { setTreeExpandMode("none"); setTreeResetKey((k) => k + 1); }}
                className={`rounded px-2 py-1 font-medium ${treeExpandMode === "none" ? "bg-indigo-100 text-indigo-800" : "text-slate-600 hover:bg-slate-100"}`}
              >
                全部收起
              </button>
            </div>
          </div>

          {mainComponents.map((comp) => {
            const canonicalParent = buildCanonicalParents(comp.root.id, childrenByTarget);
            return (
              <div key={`${comp.root.id}-${treeResetKey}`} className="rounded-xl border border-indigo-200 bg-white p-4 shadow-xs">
                <div className="mb-3 text-xs font-semibold text-indigo-700">
                  主論證網（共 {comp.totalNodes} 個節點相連）
                </div>
                <DedupTreeNode
                  node={comp.root}
                  nodes={nodes}
                  childrenByTarget={childrenByTarget}
                  secondaryBySource={secondaryBySource}
                  playAt={playAt}
                  depth={0}
                  ancestors={new Set([comp.root.id])}
                  canonicalParent={canonicalParent}
                  expandMode={treeExpandMode}
                  onJumpToExplore={(id) => {
                    setSelectedId(id);
                    setViewMode("explore");
                  }}
                />
              </div>
            );
          })}

          {sideComponents.length > 0 && (
            <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
              <button
                onClick={() => setShowSideInTree((v) => !v)}
                className="flex w-full items-center justify-between text-left text-sm font-bold text-slate-700"
              >
                <span>獨立旁支（與主論證無直接連邊，共 {sideComponents.length} 組）</span>
                <span className="text-xs font-medium text-indigo-700 underline">
                  {showSideInTree ? "收起旁支" : "展開旁支"}
                </span>
              </button>
              {showSideInTree && (
                <div className="mt-3 space-y-3">
                  {sideComponents.map((comp) => {
                    const canonicalParent = buildCanonicalParents(comp.root.id, childrenByTarget);
                    return (
                      <div key={`${comp.root.id}-${treeResetKey}`} className="rounded-lg border border-slate-200 bg-white p-3">
                        <div className="mb-2 text-xs text-slate-500">獨立小節（{comp.totalNodes} 個節點）</div>
                        <DedupTreeNode
                          node={comp.root}
                          nodes={nodes}
                          childrenByTarget={childrenByTarget}
                          secondaryBySource={secondaryBySource}
                          playAt={playAt}
                          depth={0}
                          ancestors={new Set([comp.root.id])}
                          canonicalParent={canonicalParent}
                          expandMode={treeExpandMode}
                          onJumpToExplore={(id) => {
                            setSelectedId(id);
                            setViewMode("explore");
                          }}
                        />
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {/* MODE 3: Flat Chronological List */}
      {viewMode === "flat" && (
        <section className="mt-4 space-y-3">
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
                      <span className="text-xs font-bold text-indigo-700">論點 #{p.no}</span>
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
                  <div className="space-y-2 border-t border-slate-100 px-4 py-3 text-sm">
                    {p.fidelity_warning && (
                      <p className="rounded bg-amber-50 p-2 text-xs text-amber-900">
                        <b>忠實度提醒</b>：{p.fidelity_warning.detail}
                      </p>
                    )}
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
      <section className="mt-8 rounded-xl border-2 border-indigo-200 bg-white p-5">
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

      {/* Multi-lecture transcript */}
      <section id="transcript" className="mt-8">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-bold text-slate-900">本單元完整逐字稿</h2>
          <label className="inline-flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={leftOutOnly} onChange={(e) => setLeftOutOnly(e.target.checked)} />
            只看沒整理進任何一點的話
          </label>
        </div>
        <p className="mt-1 text-xs text-slate-500">有底色的句子已被論證引用，灰字為未引用原話；點任一句可直接播放錄音。</p>

        <div className="mt-3 space-y-5">
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
                          title={pc.uses.length ? `用於論點 #${pc.uses.join("、#")}` : "未引用"}
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

/**
 * Local Focused Inspector: displays ONLY the currently selected node's immediate neighborhood
 * (upstream targets, current node + quotes, inline sub-questions/principles, and direct children).
 */
function LocalNodeInspector({
  node,
  nodes,
  parents,
  childrenList,
  secondaryTargets,
  childrenByTarget,
  onSelectNode,
  playAt,
  rootId,
}: {
  node: GraphNode;
  nodes: Record<string, GraphNode>;
  parents: { edge: GraphEdge; node: GraphNode }[];
  childrenList: TreeChild[];
  secondaryTargets: TreeChild[];
  childrenByTarget: Record<string, TreeChild[]>;
  onSelectNode: (id: string) => void;
  playAt: (sourceId: string, seconds: number | null) => void;
  rootId: string;
}) {
  const [showQuotes, setShowQuotes] = useState(true);
  const [openLeafQuotes, setOpenLeafQuotes] = useState<Set<string>>(new Set());

  const badgeCls = BADGE_STYLE[node.kind] ?? "bg-slate-100 text-slate-700 border-slate-200";
  const claimChildren = childrenList.filter((c) => c.node.kind === "claim" || c.node.kind === "question");
  const detailChildren = childrenList.filter((c) => c.node.kind !== "claim" && c.node.kind !== "question");

  return (
    <div className="rounded-xl border-2 border-indigo-300 bg-white p-5 shadow-xs">
      {/* Top navigation: back to root + upstream targets */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-2.5 text-xs">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-bold text-indigo-700">局部詳情</span>
          {node.id !== rootId && (
            <button
              onClick={() => onSelectNode(rootId)}
              className="rounded bg-slate-100 px-2 py-0.5 text-slate-600 hover:bg-indigo-50 hover:text-indigo-700"
            >
              回到總問題
            </button>
          )}
        </div>
        {parents.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-slate-500">向上連往：</span>
            {parents.map((p, idx) => (
              <button
                key={`${p.node.id}-${idx}`}
                onClick={() => onSelectNode(p.node.id)}
                className="inline-flex items-center gap-1 rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-0.5 font-medium text-indigo-800 hover:bg-indigo-100"
              >
                <ArrowUpRight className="h-3 w-3" />
                {p.edge.relation_label} → {nodeDisplayLabel(p.node)}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Selected Node Main Content */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded border px-2 py-0.5 font-bold ${badgeCls}`}>
          {node.id === rootId ? "總問題" : nodeDisplayLabel(node)}
        </span>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{node.lecture_label}</span>
        {node.start != null && (
          <button
            onClick={() => playAt(node.source_id, node.start)}
            className="inline-flex items-center gap-1 rounded bg-indigo-600 px-2 py-0.5 font-medium text-white hover:bg-indigo-700"
          >
            <Play className="h-3 w-3" />播放 {clock(node.start)}
          </button>
        )}
        {node.quotes.length > 0 && (
          <button onClick={() => setShowQuotes((v) => !v)} className="text-slate-500 underline hover:text-slate-800">
            {showQuotes ? "收起原話" : `看原話（${node.quotes.length}）`}
          </button>
        )}
      </div>

      <p className="mt-2 text-base font-semibold leading-7 text-slate-900">{node.statement}</p>

      {node.fidelity_warning && (
        <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
          <b>忠實度提醒（{node.fidelity_warning.verdict === "added" ? "疑似多加" : "疑似改寫"}）</b>：
          {node.fidelity_warning.detail}
        </p>
      )}

      {showQuotes && node.quotes.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {node.quotes.map((q, i) => (
            <div key={i} className="rounded-lg bg-slate-50 px-3 py-2 text-xs leading-6 text-slate-700">
              <button
                onClick={() => playAt(q.source_id, q.start)}
                className="mr-2 inline-flex items-center gap-0.5 rounded bg-white px-1.5 py-0.5 font-medium text-indigo-700 ring-1 ring-slate-200 hover:bg-indigo-50"
              >
                <Play className="h-2.5 w-2.5" />{q.lecture_label} {clock(q.start)}
              </button>
              「{q.text}」
            </div>
          ))}
        </div>
      )}

      {/* Secondary Sub-Questions & Principles linked from this node */}
      {secondaryTargets.length > 0 && (
        <div className="mt-4 space-y-1.5 border-t border-slate-100 pt-3">
          <div className="text-xs font-bold text-slate-600">本節點同時關聯的子問題與原則（{secondaryTargets.length}）</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {secondaryTargets.map((st, idx) => {
              const isQ = st.node.kind === "question";
              return (
                <button
                  key={`${st.node.id}-${idx}`}
                  onClick={() => onSelectNode(st.node.id)}
                  className={`rounded-lg border p-2.5 text-left text-xs transition hover:shadow-xs ${
                    isQ ? "border-violet-200 bg-violet-50/70 hover:bg-violet-50" : "border-emerald-200 bg-emerald-50/70 hover:bg-emerald-50"
                  }`}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className={`rounded px-1.5 py-0.5 font-semibold text-white ${isQ ? "bg-violet-600" : "bg-emerald-600"}`}>
                      {isQ ? "同時回答子問題" : "同時引申原則"}
                    </span>
                    <span className="inline-flex items-center gap-0.5 text-indigo-700 font-medium">
                      查看 <ArrowRight className="h-3 w-3" />
                    </span>
                  </div>
                  <p className="mt-1.5 font-medium leading-5 text-slate-900">{st.node.statement}</p>
                  {st.node.fidelity_warning && (
                    <p className="mt-1 inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-900">
                      <AlertTriangle className="h-3 w-3" />忠實度待核對
                    </p>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Child Sub-Branches (Core Claims/Questions to drill into) */}
      {claimChildren.length > 0 && (
        <div className="mt-4 space-y-2 border-t border-slate-100 pt-3">
          <div className="text-xs font-bold text-slate-700">
            下層核心支線（點卡片進入該支線 · {claimChildren.length}）
          </div>
          <div className="grid gap-2.5 sm:grid-cols-2">
            {claimChildren.map((ch, idx) => {
              const subCount = (childrenByTarget[ch.node.id] ?? []).length;
              const relCls = REL_STYLE[ch.edge.relation] ?? "bg-slate-600 text-white";
              return (
                <button
                  key={`${ch.node.id}-${idx}`}
                  onClick={() => onSelectNode(ch.node.id)}
                  className="group rounded-lg border border-indigo-200 bg-indigo-50/30 p-3 text-left transition hover:border-indigo-400 hover:bg-indigo-50"
                >
                  <div className="flex flex-wrap items-center justify-between gap-1 text-xs">
                    <div className="flex items-center gap-1.5">
                      <span className={`rounded px-1.5 py-0.5 font-semibold ${relCls}`}>{ch.edge.relation_label}</span>
                      <span className="font-bold text-indigo-800">{nodeDisplayLabel(ch.node)}</span>
                      <span className="text-slate-500">{ch.node.lecture_label}</span>
                    </div>
                    <span className="inline-flex items-center gap-0.5 font-semibold text-indigo-700 group-hover:translate-x-0.5 transition">
                      進入支線（{subCount}）<ArrowRight className="h-3.5 w-3.5" />
                    </span>
                  </div>
                  <p className="mt-1.5 text-sm font-medium leading-6 text-slate-900">{ch.node.statement}</p>
                  {ch.edge.reason && (
                    <p className="mt-1 text-xs text-slate-600">{formatReason(ch.edge.reason, nodes)}</p>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Direct Supporting Evidence / Observations / Examples / Refuted Positions */}
      {detailChildren.length > 0 && (
        <div className="mt-4 space-y-2 border-t border-slate-100 pt-3">
          <div className="text-xs font-bold text-slate-700">
            直接依據、例證與反方細節（{detailChildren.length}）
          </div>
          <div className="space-y-2">
            {detailChildren.map((ch, idx) => {
              const relCls = REL_STYLE[ch.edge.relation] ?? "bg-slate-600 text-white";
              const bCls = BADGE_STYLE[ch.node.kind] ?? "bg-slate-100 text-slate-700 border-slate-200";
              const subChildren = childrenByTarget[ch.node.id] ?? [];
              const qOpen = openLeafQuotes.has(ch.node.id);
              return (
                <div key={`${ch.node.id}-${idx}`} className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 text-xs">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={`rounded px-1.5 py-0.5 font-semibold ${relCls}`}>{ch.edge.relation_label}</span>
                    <span className={`rounded border px-1.5 py-0.5 font-semibold ${bCls}`}>{ch.node.kind_label}</span>
                    <span className="text-slate-500">{ch.node.lecture_label}</span>
                    {ch.node.start != null && (
                      <button
                        onClick={() => playAt(ch.node.source_id, ch.node.start)}
                        className="inline-flex items-center gap-0.5 rounded bg-white px-1.5 py-0.5 font-medium text-indigo-700 ring-1 ring-slate-200 hover:bg-indigo-50"
                      >
                        <Play className="h-2.5 w-2.5" />{clock(ch.node.start)}
                      </button>
                    )}
                    {ch.node.quotes.length > 0 && (
                      <button
                        onClick={() =>
                          setOpenLeafQuotes((prev) => {
                            const next = new Set(prev);
                            if (next.has(ch.node.id)) next.delete(ch.node.id);
                            else next.add(ch.node.id);
                            return next;
                          })
                        }
                        className="text-slate-600 underline hover:text-slate-900"
                      >
                        {qOpen ? "收起原話" : `看原話（${ch.node.quotes.length}）`}
                      </button>
                    )}
                    {subChildren.length > 0 && (
                      <button
                        onClick={() => onSelectNode(ch.node.id)}
                        className="ml-auto inline-flex items-center gap-0.5 font-semibold text-indigo-700 underline"
                      >
                        展開下層（{subChildren.length}）<ArrowRight className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                  <p className="mt-1.5 text-sm leading-6 text-slate-900">{ch.node.statement}</p>
                  {ch.edge.reason && <p className="mt-0.5 text-slate-600">關聯：{formatReason(ch.edge.reason, nodes)}</p>}
                  {qOpen && ch.node.quotes.length > 0 && (
                    <div className="mt-2 space-y-1 border-t border-slate-200/70 pt-2">
                      {ch.node.quotes.map((q, qi) => (
                        <div key={qi} className="rounded bg-white px-2.5 py-1.5 leading-5 text-slate-700">
                          <button
                            onClick={() => playAt(q.source_id, q.start)}
                            className="mr-1.5 inline-flex items-center gap-0.5 rounded bg-indigo-50 px-1.5 py-0.5 font-medium text-indigo-700"
                          >
                            <Play className="h-2.5 w-2.5" />{q.lecture_label} {clock(q.start)}
                          </button>
                          「{q.text}」
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Pure BFS precomputation of each node's single canonical parent in the DAG tree,
 * avoiding render-time Set mutation bugs under React StrictMode.
 */
function buildCanonicalParents(
  rootId: string,
  childrenByTarget: Record<string, TreeChild[]>
): Map<string, string> {
  const parentOf = new Map<string, string>();
  const visited = new Set<string>([rootId]);
  const queue = [rootId];
  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const ch of childrenByTarget[cur] ?? []) {
      if (!visited.has(ch.node.id)) {
        visited.add(ch.node.id);
        parentOf.set(ch.node.id, cur);
        queue.push(ch.node.id);
      }
    }
  }
  return parentOf;
}

/**
 * Deduplicated Tree Node for Mode 2: renders each shared DAG node in full only ONCE
 * under its canonical BFS parent, and shows a compact jump stub under secondary parents.
 */
function DedupTreeNode({
  node,
  edge,
  nodes,
  childrenByTarget,
  secondaryBySource,
  playAt,
  depth,
  ancestors,
  canonicalParent,
  expandMode,
  onJumpToExplore,
}: {
  node: GraphNode;
  edge?: GraphEdge;
  nodes: Record<string, GraphNode>;
  childrenByTarget: Record<string, TreeChild[]>;
  secondaryBySource: Record<string, TreeChild[]>;
  playAt: (sourceId: string, seconds: number | null) => void;
  depth: number;
  ancestors: Set<string>;
  canonicalParent: Map<string, string>;
  expandMode: "spine" | "all" | "none";
  onJumpToExplore: (id: string) => void;
}) {
  const children = (childrenByTarget[node.id] ?? []).filter((ch) => !ancestors.has(ch.node.id));
  const secondaryTargets = (secondaryBySource[node.id] ?? []).filter((st) => !ancestors.has(st.node.id));
  const defaultOpen = expandMode === "all" ? true : expandMode === "none" ? false : depth < 2;
  const [expanded, setExpanded] = useState(defaultOpen);
  const [showQuotes, setShowQuotes] = useState(false);
  const [showAllSecondary, setShowAllSecondary] = useState(secondaryTargets.length <= 2);

  const badgeCls = BADGE_STYLE[node.kind] ?? "bg-slate-100 text-slate-700 border-slate-200";
  const relCls = edge ? (REL_STYLE[edge.relation] ?? "bg-slate-600 text-white") : "";

  return (
    <div className={depth > 0 ? "mt-2 border-l-2 border-indigo-100 pl-3" : ""}>
      <div className="rounded-lg border border-slate-200 bg-white p-3">
        {edge && (
          <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
            <span className={`rounded px-1.5 py-0.5 font-semibold ${relCls}`}>{edge.relation_label}</span>
            <span className="text-slate-600">{formatReason(edge.reason, nodes)}</span>
          </div>
        )}

        <div className="flex items-start gap-2">
          {children.length > 0 ? (
            <button onClick={() => setExpanded((v) => !v)} className="mt-0.5 rounded p-0.5 text-slate-500 hover:bg-slate-100">
              {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </button>
          ) : (
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
          )}

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className={`rounded border px-1.5 py-0.5 font-semibold ${badgeCls}`}>{nodeDisplayLabel(node)}</span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{node.lecture_label}</span>
              {node.start != null && (
                <button onClick={() => playAt(node.source_id, node.start)} className="inline-flex items-center gap-0.5 rounded bg-indigo-50 px-1.5 py-0.5 font-medium text-indigo-700">
                  <Play className="h-2.5 w-2.5" />{clock(node.start)}
                </button>
              )}
              {node.quotes.length > 0 && (
                <button onClick={() => setShowQuotes((v) => !v)} className="text-slate-500 underline">
                  {showQuotes ? "收起原話" : `看原話（${node.quotes.length}）`}
                </button>
              )}
              <button onClick={() => onJumpToExplore(node.id)} className="ml-auto text-indigo-600 underline">
                在全局圖聚焦
              </button>
            </div>
            <p className="mt-1 text-sm leading-6 text-slate-900">{node.statement}</p>

            {secondaryTargets.length > 0 && (
              <div className="mt-2">
                {!showAllSecondary ? (
                  <button
                    onClick={() => setShowAllSecondary(true)}
                    className="rounded border border-violet-200 bg-violet-50 px-2 py-1 text-xs font-medium text-violet-800 hover:bg-violet-100"
                  >
                    同時關聯 {secondaryTargets.length} 項子問題／原則（點擊展開）
                  </button>
                ) : (
                  <div className="space-y-1">
                    {secondaryTargets.map((st, idx) => (
                      <div key={`${st.node.id}-${idx}`} className="rounded border border-violet-200/70 bg-violet-50/50 px-2.5 py-1.5 text-xs">
                        <span className="mr-1.5 font-semibold text-violet-800">
                          {st.node.kind === "question" ? "同時回答子問題：" : "同時引申原則："}
                        </span>
                        <span className="text-slate-800">{st.node.statement}</span>
                        {st.node.fidelity_warning && (
                          <span className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-900">
                            <AlertTriangle className="h-3 w-3" />忠實度待核對
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {showQuotes && node.quotes.length > 0 && (
              <div className="mt-2 space-y-1 border-t border-slate-100 pt-2">
                {node.quotes.map((q, i) => (
                  <div key={i} className="rounded bg-slate-50 px-2.5 py-1.5 text-xs leading-5 text-slate-700">
                    <button onClick={() => playAt(q.source_id, q.start)} className="mr-1.5 inline-flex items-center gap-0.5 rounded bg-white px-1.5 py-0.5 text-indigo-700 ring-1 ring-slate-200">
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
            if (canonicalParent.get(ch.node.id) !== node.id) {
              // DAG deduplication stub: do not duplicate entire subtree
              return (
                <div key={`${ch.node.id}-stub-${idx}`} className="mt-2 border-l-2 border-dashed border-indigo-200 pl-3">
                  <button
                    onClick={() => onJumpToExplore(ch.node.id)}
                    className="flex w-full flex-wrap items-center gap-2 rounded-lg border border-dashed border-indigo-200 bg-indigo-50/40 px-3 py-2 text-left text-xs text-indigo-900 hover:bg-indigo-50"
                  >
                    <span className={`rounded px-1.5 py-0.5 font-semibold ${REL_STYLE[ch.edge.relation] ?? "bg-slate-600 text-white"}`}>
                      {ch.edge.relation_label}
                    </span>
                    <span className="font-bold">↳ 共享節點：{nodeDisplayLabel(ch.node)}</span>
                    <span className="text-slate-700">{ch.node.statement.slice(0, 40)}…</span>
                    <span className="ml-auto font-semibold text-indigo-700 underline">已於他處展開 · 點此聚焦</span>
                  </button>
                </div>
              );
            }
            const nextAnc = new Set(ancestors);
            nextAnc.add(ch.node.id);
            return (
              <DedupTreeNode
                key={`${ch.node.id}-${idx}`}
                node={ch.node}
                edge={ch.edge}
                nodes={nodes}
                childrenByTarget={childrenByTarget}
                secondaryBySource={secondaryBySource}
                playAt={playAt}
                depth={depth + 1}
                ancestors={nextAnc}
                canonicalParent={canonicalParent}
                expandMode={expandMode}
                onJumpToExplore={onJumpToExplore}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
