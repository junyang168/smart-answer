"use client";

import { useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";

export type DagGraphNode = {
  id: string;
  kind: string;
  kind_label: string;
  point_no: number | null;
  question_no: number | null;
  statement: string;
  source_id: string;
  lecture_label: string;
  start: number | null;
  quotes: { text: string; source_id: string; lecture_label: string; paragraph: string; start: number | null }[];
  fidelity_warning: { id: string; kind: string; verdict: string; detail: string } | null;
};

export type DagGraphEdge = {
  from_id: string;
  to_id: string;
  relation: string;
  relation_label: string;
  reason: string;
  attribution: string;
};

type LayoutNode = {
  node: DagGraphNode;
  layer: number;
  x: number;
  y: number;
  w: number;
  h: number;
  hiddenChildCount: number;
  subQuestionCount: number;
  isRoot: boolean;
};

const NODE_W = 216;
const NODE_H = 78;
const H_GAP = 28;
const V_GAP = 64;
const PAD_X = 28;
const PAD_Y = 24;

function shortTitle(text: string, maxChars = 30): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > maxChars ? `${clean.slice(0, maxChars)}…` : clean;
}

function nodeColors(kind: string, isRoot: boolean, selected: boolean) {
  if (selected) {
    return { fill: "#eef2ff", stroke: "#4f46e5", badgeBg: "#4f46e5", badgeText: "#ffffff" };
  }
  if (isRoot || kind === "question") {
    return { fill: "#f5f3ff", stroke: "#7c3aed", badgeBg: "#7c3aed", badgeText: "#ffffff" };
  }
  if (kind === "claim") {
    return { fill: "#ffffff", stroke: "#6366f1", badgeBg: "#e0e7ff", badgeText: "#3730a3" };
  }
  if (kind === "position") {
    return { fill: "#fff1f2", stroke: "#e11d48", badgeBg: "#ffe4e6", badgeText: "#9f1239" };
  }
  return { fill: "#f8fafc", stroke: "#94a3b8", badgeBg: "#e2e8f0", badgeText: "#334155" };
}

function edgeStroke(relation: string, active: boolean): string {
  if (!active) return "#cbd5e1";
  switch (relation) {
    case "answers":
      return "#7c3aed";
    case "supports":
      return "#059669";
    case "refutes":
      return "#e11d48";
    case "illustrates":
      return "#d97706";
    case "qualifies":
      return "#0284c7";
    default:
      return "#64748b";
  }
}

export function ArgumentDagMap({
  nodes,
  edges,
  rootId,
  componentIds,
  selectedId,
  onSelect,
}: {
  nodes: Record<string, DagGraphNode>;
  edges: DagGraphEdge[];
  rootId: string;
  componentIds: Set<string>;
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const [showSubQuestions, setShowSubQuestions] = useState(false);

  const { layoutNodes, layoutMap, visibleEdges, svgWidth, svgHeight, hiddenSubQuestionTotal } = useMemo(() => {
    const compEdges = edges.filter((e) => componentIds.has(e.from_id) && componentIds.has(e.to_id));
    const directToRoot = new Set(
      compEdges.filter((e) => e.to_id === rootId).map((e) => e.from_id)
    );

    // Core spine = root + all claims in component + positions directly attached to root.
    // Optionally include secondary questions when user toggles showSubQuestions.
    const visibleIds = new Set<string>([rootId]);
    let subQTotal = 0;
    for (const id of componentIds) {
      const n = nodes[id];
      if (!n || id === rootId) continue;
      if (n.kind === "claim") {
        visibleIds.add(id);
      } else if (n.kind === "position" && directToRoot.has(id)) {
        visibleIds.add(id);
      } else if (n.kind === "question") {
        subQTotal += 1;
        if (showSubQuestions) visibleIds.add(id);
      }
    }

    // Count hidden leaf children (evidence/observation/example/position) & sub-questions per visible node
    const hiddenCount: Record<string, number> = {};
    const subQCount: Record<string, number> = {};
    for (const e of compEdges) {
      if (visibleIds.has(e.to_id) && !visibleIds.has(e.from_id)) {
        hiddenCount[e.to_id] = (hiddenCount[e.to_id] ?? 0) + 1;
      }
      if (visibleIds.has(e.from_id) && !visibleIds.has(e.to_id) && nodes[e.to_id]?.kind === "question") {
        subQCount[e.from_id] = (subQCount[e.from_id] ?? 0) + 1;
      }
    }

    const vEdges = compEdges.filter((e) => visibleIds.has(e.from_id) && visibleIds.has(e.to_id));

    // Compute top-down depth layers from rootId:
    // Layer 0 = rootId (main question), Layer 1 = nodes pointing directly into rootId,
    // Layer 2 = nodes pointing into Layer 1, etc.
    // Secondary sinks (e.g. principles illustrated by a claim, or sub-questions answered by a claim)
    // are placed one layer below the node that points into them so flow stays cleanly top-down.
    const layerById: Record<string, number> = { [rootId]: 0 };
    const queue: string[] = [rootId];
    while (queue.length > 0) {
      const cur = queue.shift()!;
      const curLayer = layerById[cur];
      for (const e of vEdges) {
        // Upstream support/answer/contextualizes flows INTO cur -> place child below cur
        if (e.to_id === cur && layerById[e.from_id] == null) {
          layerById[e.from_id] = curLayer + 1;
          queue.push(e.from_id);
        }
      }
    }
    // Second pass: place secondary targets (e.g., C006 --illustrates--> C007, C006 --answers--> Q002)
    // one layer below their source so they hang cleanly underneath the branch that produces them.
    let changed = true;
    while (changed) {
      changed = false;
      for (const e of vEdges) {
        if (layerById[e.from_id] != null && layerById[e.to_id] == null) {
          layerById[e.to_id] = layerById[e.from_id] + 1;
          changed = true;
        } else if (layerById[e.to_id] != null && layerById[e.from_id] == null) {
          layerById[e.from_id] = layerById[e.to_id] + 1;
          changed = true;
        }
      }
    }
    for (const id of visibleIds) {
      if (layerById[id] == null) layerById[id] = 1;
    }

    // Group by layer and order nodes within each layer by barycenter of their connected neighbors
    // in the previous layer (reduces edge crossings).
    const maxLayer = Math.max(...Object.values(layerById), 0);
    const layers: string[][] = Array.from({ length: maxLayer + 1 }, () => []);
    for (const id of visibleIds) {
      layers[layerById[id]].push(id);
    }
    // Initial stable sort by lecture start time / point_no
    for (const l of layers) {
      l.sort((a, b) => (nodes[a].point_no ?? 999) - (nodes[b].point_no ?? 999));
    }
    // One barycenter pass top-down to reduce crossings
    for (let li = 1; li < layers.length; li++) {
      const prevIdx = new Map(layers[li - 1].map((id, idx) => [id, idx]));
      layers[li].sort((a, b) => {
        const avg = (id: string) => {
          const ps: number[] = [];
          for (const e of vEdges) {
            if (e.from_id === id && prevIdx.has(e.to_id)) ps.push(prevIdx.get(e.to_id)!);
            if (e.to_id === id && prevIdx.has(e.from_id)) ps.push(prevIdx.get(e.from_id)!);
          }
          return ps.length ? ps.reduce((s, v) => s + v, 0) / ps.length : 99;
        };
        return avg(a) - avg(b);
      });
    }

    const maxCols = Math.max(...layers.map((l) => l.length), 1);
    const totalW = Math.max(760, maxCols * NODE_W + (maxCols - 1) * H_GAP + PAD_X * 2);
    const totalH = (maxLayer + 1) * NODE_H + maxLayer * V_GAP + PAD_Y * 2;

    const placed: LayoutNode[] = [];
    const map: Record<string, LayoutNode> = {};
    layers.forEach((layerIds, li) => {
      const rowW = layerIds.length * NODE_W + (layerIds.length - 1) * H_GAP;
      const startX = (totalW - rowW) / 2;
      const y = PAD_Y + li * (NODE_H + V_GAP);
      layerIds.forEach((id, ci) => {
        const ln: LayoutNode = {
          node: nodes[id],
          layer: li,
          x: startX + ci * (NODE_W + H_GAP),
          y,
          w: NODE_W,
          h: NODE_H,
          hiddenChildCount: hiddenCount[id] ?? 0,
          subQuestionCount: subQCount[id] ?? 0,
          isRoot: id === rootId,
        };
        placed.push(ln);
        map[id] = ln;
      });
    });

    return {
      layoutNodes: placed,
      layoutMap: map,
      visibleEdges: vEdges,
      svgWidth: totalW,
      svgHeight: totalH,
      hiddenSubQuestionTotal: subQTotal,
    };
  }, [nodes, edges, rootId, componentIds, showSubQuestions]);

  // Highlight selected node and its immediate neighbors in the DAG
  const neighborIds = useMemo(() => {
    const s = new Set<string>([selectedId]);
    for (const e of visibleEdges) {
      if (e.from_id === selectedId) s.add(e.to_id);
      if (e.to_id === selectedId) s.add(e.from_id);
    }
    return s;
  }, [selectedId, visibleEdges]);

  return (
    <div className="rounded-xl border border-indigo-200 bg-white shadow-xs">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-bold text-indigo-700">全局論證地圖（先看全貌，點任一節點切入下方局部詳情）</span>
          <span className="text-slate-500">
            顯示 {layoutNodes.length} 個主幹節點 · 箭頭方向為「推導／支持／回答 → 目標」
          </span>
        </div>
        {hiddenSubQuestionTotal > 0 && (
          <label className="inline-flex cursor-pointer items-center gap-1.5 font-medium text-violet-700">
            <input
              type="checkbox"
              checked={showSubQuestions}
              onChange={(e) => setShowSubQuestions(e.target.checked)}
              className="rounded border-violet-300"
            />
            同時在圖上顯示 {hiddenSubQuestionTotal} 個支線子問題
          </label>
        )}
      </div>

      <div className="overflow-x-auto bg-slate-50/50 p-2">
        <svg
          width={svgWidth}
          height={svgHeight}
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          className="mx-auto block select-none"
          role="img"
          aria-label="論證結構全局圖"
        >
          <defs>
            {["#7c3aed", "#059669", "#e11d48", "#d97706", "#0284c7", "#64748b", "#cbd5e1"].map((color) => (
              <marker
                key={color}
                id={`arrow-${color.slice(1)}`}
                viewBox="0 0 10 10"
                refX="8.5"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill={color} />
              </marker>
            ))}
          </defs>

          {/* Edges */}
          {visibleEdges.map((e, idx) => {
            const src = layoutMap[e.from_id];
            const dst = layoutMap[e.to_id];
            if (!src || !dst) return null;
            const isHighlighted = !selectedId || e.from_id === selectedId || e.to_id === selectedId;
            const stroke = edgeStroke(e.relation, isHighlighted);

            // Connect from top-center of lower node to bottom-center of upper node (or vice versa)
            const goingUp = src.y > dst.y;
            const sameRow = src.y === dst.y;
            let x1 = src.x + src.w / 2;
            let y1 = goingUp ? src.y : src.y + src.h;
            let x2 = dst.x + dst.w / 2;
            let y2 = goingUp ? dst.y + dst.h : dst.y;
            let d: string;
            let labelX = (x1 + x2) / 2;
            let labelY = (y1 + y2) / 2;

            if (sameRow) {
              const leftToRight = src.x < dst.x;
              x1 = leftToRight ? src.x + src.w : src.x;
              y1 = src.y + src.h / 2;
              x2 = leftToRight ? dst.x : dst.x + dst.w;
              y2 = dst.y + dst.h / 2;
              const arch = -22;
              d = `M ${x1} ${y1} Q ${(x1 + x2) / 2} ${y1 + arch} ${x2} ${y2}`;
              labelY = y1 + arch / 2 - 4;
            } else {
              const midY = (y1 + y2) / 2;
              d = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
            }

            return (
              <g key={`${e.from_id}-${e.to_id}-${idx}`} opacity={isHighlighted ? 1 : 0.35}>
                <path
                  d={d}
                  fill="none"
                  stroke={stroke}
                  strokeWidth={isHighlighted ? 2 : 1.3}
                  markerEnd={`url(#arrow-${stroke.slice(1)})`}
                />
                {isHighlighted && (
                  <g transform={`translate(${labelX}, ${labelY})`}>
                    <rect x={-26} y={-9} width={52} height={16} rx={4} fill="#ffffff" stroke={stroke} strokeWidth={0.8} />
                    <text textAnchor="middle" y={2.5} fontSize={9.5} fontWeight={600} fill={stroke}>
                      {e.relation_label.slice(0, 4)}
                    </text>
                  </g>
                )}
              </g>
            );
          })}

          {/* Nodes */}
          {layoutNodes.map((ln) => {
            const isSelected = ln.node.id === selectedId;
            const isRelated = !selectedId || neighborIds.has(ln.node.id);
            const c = nodeColors(ln.node.kind, ln.isRoot, isSelected);
            const tagText = ln.isRoot
              ? "總問題"
              : ln.node.point_no
              ? `論點 #${ln.node.point_no}`
              : ln.node.question_no
              ? `子問題 #${ln.node.question_no}`
              : ln.node.kind_label;
            const title = shortTitle(ln.node.statement, 30);
            const line1 = title.slice(0, 15);
            const line2 = title.slice(15);

            return (
              <g
                key={ln.node.id}
                transform={`translate(${ln.x}, ${ln.y})`}
                onClick={() => onSelect(ln.node.id)}
                className="cursor-pointer"
                opacity={isRelated ? 1 : 0.45}
              >
                <rect
                  width={ln.w}
                  height={ln.h}
                  rx={10}
                  fill={c.fill}
                  stroke={c.stroke}
                  strokeWidth={isSelected ? 2.5 : 1.3}
                />
                {/* Top badge + lecture label */}
                <rect x={8} y={7} width={58} height={17} rx={4} fill={c.badgeBg} />
                <text x={37} y={18.5} textAnchor="middle" fontSize={10} fontWeight={700} fill={c.badgeText}>
                  {tagText}
                </text>
                <text x={72} y={18.5} fontSize={9.5} fill="#64748b">
                  {ln.node.lecture_label}
                </text>
                {/* Child evidence / sub-question counts */}
                {(ln.hiddenChildCount > 0 || ln.subQuestionCount > 0) && (
                  <text x={ln.w - 8} y={18.5} textAnchor="end" fontSize={9.5} fontWeight={600} fill="#475569">
                    {ln.hiddenChildCount > 0 ? `+${ln.hiddenChildCount}依據` : ""}
                    {ln.hiddenChildCount > 0 && ln.subQuestionCount > 0 ? " " : ""}
                    {!showSubQuestions && ln.subQuestionCount > 0 ? `+${ln.subQuestionCount}問` : ""}
                  </text>
                )}
                {/* Fidelity warning indicator */}
                {ln.node.fidelity_warning && (
                  <g transform={`translate(${ln.w - 18}, ${ln.h - 18})`}>
                    <circle r={7} fill="#fef3c7" stroke="#d97706" strokeWidth={1} />
                    <text textAnchor="middle" y={3.5} fontSize={10} fontWeight={800} fill="#b45309">!</text>
                  </g>
                )}
                {/* Two-line statement summary */}
                <text x={10} y={40} fontSize={11.5} fontWeight={600} fill="#0f172a">
                  {line1}
                </text>
                {line2 && (
                  <text x={10} y={57} fontSize={11.5} fontWeight={600} fill="#0f172a">
                    {line2}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 px-4 py-2 text-[11px] text-slate-500">
        <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-xs bg-violet-600" />回答</span>
        <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-xs bg-emerald-600" />支持</span>
        <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-xs bg-rose-600" />反駁</span>
        <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-xs bg-amber-600" />例證／引申</span>
        <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-xs bg-slate-500" />背景</span>
        <span className="inline-flex items-center gap-1 text-amber-700"><AlertTriangle className="h-3 w-3" />有忠實度提醒</span>
      </div>
    </div>
  );
}
