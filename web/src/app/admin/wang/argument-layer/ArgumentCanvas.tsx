"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CLAIM_H,
  COL_W,
  LANE_H,
  PAD_T,
  ZONE_GAP,
  buildLayout,
  colX,
  type Placed,
} from "./geometry";
import {
  CLAIM_COLOR,
  LANE_COLOR,
  LANE_HINT,
  OBS_COLOR,
  REL_COLOR,
  isWithheld,
  type Claim,
  type Focus,
  type Observation,
  type Question,
  type Source,
  type Step,
} from "./types";

const RELATIONS = ["supports", "qualifies", "refutes", "answers", "applies", "contextualizes"];

type Props = {
  source: Source;
  lanes: string[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  scrollTarget: { id: string; nonce: number } | null;
  /** Records another page linked to; shown alone until the reader turns it off. */
  spotlight?: Focus | null;
};

export function ArgumentCanvas({ source, lanes, selectedId, onSelect, scrollTarget, spotlight }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(0.72);
  const [hovered, setHovered] = useState<string | null>(null);
  const [rels, setRels] = useState<Set<string>>(new Set(RELATIONS));
  const [onlyIsolated, setOnlyIsolated] = useState(false);
  const [onlySpotlight, setOnlySpotlight] = useState(true);
  const [onlyWithheld, setOnlyWithheld] = useState(false);
  const [showObs, setShowObs] = useState(true);
  const [showClaims, setShowClaims] = useState(true);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportW, setViewportW] = useState(1200);

  const obsLane = lanes.length;
  const filtersOn = onlyIsolated || onlyWithheld;
  // Arriving on a link means arriving with a question already asked, so the
  // filter starts on -- and stays a chip, because the next question is
  // usually "and where does this sit in the rest of the argument".
  const spotlightOn = !!spotlight && onlySpotlight;
  // A spotlight on stranded records contains no claims by definition, and an
  // empty claims zone still labelled "claims 17" reads as if seventeen of them
  // were in the answer.
  const spotlightHasClaim = !!spotlight && source.claims.some((claim) => spotlight.ids.has(claim.id));

  const layout = useMemo(
    () =>
      buildLayout(source, {
        laneCount: lanes.length,
        showObs,
        showClaims: showClaims && !filtersOn && (!spotlightOn || spotlightHasClaim),
      }),
    [source, lanes.length, showObs, showClaims, filtersOn, spotlightOn, spotlightHasClaim],
  );

  const visible = useMemo(() => {
    const kept = layout.placed.filter((item) => {
      if (item.kind === "observation" && !showObs) return false;
      if (spotlightOn && !spotlight.ids.has(item.id)) return false;
      if (onlyIsolated && item.linked) return false;
      if (onlyWithheld && !(item.kind === "step" && isWithheld((item.node as Step).eligibility))) return false;
      return true;
    });
    return new Map(kept.map((item) => [item.id, item]));
  }, [layout, showObs, onlyIsolated, onlyWithheld, spotlightOn, spotlight]);

  const focus = hovered ?? selectedId;
  const near = useMemo(() => {
    if (!focus) return null;
    const set = new Set<string>([focus]);
    source.edges.forEach((edge) => {
      if (edge.from === focus) set.add(edge.to);
      if (edge.to === focus) set.add(edge.from);
    });
    const item = layout.byId.get(focus);
    if (item?.kind === "claim") {
      (item.node as Claim).step_ids.forEach((id) => set.add(id));
      (item.node as Claim).opposed_position_ids.forEach((id) => set.add(id));
    }
    if (item?.kind === "step") (item.node as Step).claim_ids.forEach((id) => set.add(id));
    if (item?.kind === "question") (item.node as Question).claim_ids.forEach((id) => set.add(id));
    return set;
  }, [focus, source.edges, layout]);

  useEffect(() => {
    const element = wrap.current;
    if (!element) return;
    const onScroll = () => {
      setScrollLeft(element.scrollLeft);
      setScrollTop(element.scrollTop);
    };
    const onResize = () => setViewportW(element.clientWidth);
    onResize();
    element.addEventListener("scroll", onScroll);
    window.addEventListener("resize", onResize);
    return () => {
      element.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  // Open a source showing every lane at once — the point of the view is the
  // shape of the whole argument, not a window onto part of it.
  useEffect(() => {
    if (!wrap.current) return;
    wrap.current.scrollTo({ left: 0, top: 0 });
    const fit = wrap.current.clientHeight / layout.height;
    setZoom(Math.min(1, Math.max(0.3, Number(fit.toFixed(2)))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.key]);

  useEffect(() => {
    if (!scrollTarget || !wrap.current) return;
    const item = visible.get(scrollTarget.id);
    if (!item) return;
    wrap.current.scrollTo({ left: item.x * zoom - wrap.current.clientWidth / 2 + 80, behavior: "smooth" });
    // Only a new request should move the canvas, not a re-render of the same one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollTarget?.nonce]);

  const laneNames = showObs ? [...lanes, "observations"] : lanes;
  const stats = source.stats;

  const edges = source.edges.filter(
    (edge) => rels.has(edge.type) && visible.has(edge.from) && visible.has(edge.to),
  );

  const chip = (active: boolean, label: string, onClick: () => void, swatch?: string) => (
    <button
      key={label}
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition ${
        active ? "border-indigo-300 bg-indigo-50 text-slate-900" : "border-slate-300 text-slate-500 hover:bg-slate-100"
      }`}
    >
      {swatch ? <i className="inline-block h-0 w-3.5 border-t-2" style={{ borderColor: swatch }} /> : null}
      {label}
    </button>
  );

  const minimapScale = viewportW / Math.max(layout.width, 1);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 bg-white px-4 py-1.5">
        <span className="text-xs text-slate-500">關係</span>
        {RELATIONS.map((relation) =>
          chip(
            rels.has(relation),
            relation,
            () =>
              setRels((current) => {
                const next = new Set(current);
                if (next.has(relation)) next.delete(relation);
                else next.add(relation);
                return next;
              }),
            REL_COLOR[relation],
          ),
        )}
        <span className="mx-1 h-4 w-px bg-slate-200" />
        {spotlight ? chip(onlySpotlight, spotlight.label, () => setOnlySpotlight((value) => !value), "#be123c") : null}
        {chip(onlyIsolated, "只看孤立節點", () => setOnlyIsolated((value) => !value))}
        {chip(onlyWithheld, "只看不合格證據", () => setOnlyWithheld((value) => !value))}
        {chip(showObs, "observations", () => setShowObs((value) => !value))}
        {chip(showClaims, "claims", () => setShowClaims((value) => !value))}
        <span className="mx-1 h-4 w-px bg-slate-200" />
        <label className="inline-flex items-center gap-1.5 text-xs text-slate-500">
          縮放
          <input
            type="range"
            min={30}
            max={150}
            value={Math.round(zoom * 100)}
            onChange={(event) => setZoom(Number(event.target.value) / 100)}
            className="w-24"
          />
          {Math.round(zoom * 100)}%
        </label>
      </div>

      {/* The whole source compressed into one strip: a canvas several thousand
          pixels wide is otherwise navigated blind. */}
      <div
        className="relative h-[38px] flex-none cursor-crosshair overflow-hidden border-b border-slate-200 bg-white"
        onPointerDown={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const x = event.clientX - box.left;
          wrap.current?.scrollTo({ left: (x / minimapScale) * zoom - (wrap.current?.clientWidth ?? 0) / 2 });
        }}
      >
        {[...visible.values()].map((item) => (
          <i
            key={item.id}
            className="absolute rounded-[1px]"
            style={{
              left: item.x * minimapScale,
              width: Math.max(1.5, COL_W * minimapScale),
              top: 3 + (item.kind === "claim" ? 0 : item.lane === obsLane ? 7 : item.lane + 1) * 4,
              height: 3,
              background:
                item.kind === "claim" ? CLAIM_COLOR : item.lane === obsLane ? OBS_COLOR : LANE_COLOR[item.lane],
              opacity: item.kind === "observation" && !item.linked ? 0.45 : 1,
            }}
          />
        ))}
        <div
          className="pointer-events-none absolute bottom-0 top-0 border border-indigo-500 bg-indigo-500/10"
          style={{ left: (scrollLeft / zoom) * minimapScale, width: Math.min(layout.width, viewportW / zoom) * minimapScale }}
        />
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Lane names stay put: scroll to the end of an argument and you still
            need to know which row is 結論. */}
        <div className="relative w-[106px] flex-none overflow-hidden border-r border-slate-200 bg-white">
          <div className="absolute inset-0" style={{ transform: `translateY(${-scrollTop}px)` }}>
            {layout.claimRows ? (
              <div className="absolute left-0 right-0 pl-2.5 font-mono text-[11px] font-semibold" style={{ top: PAD_T * zoom, color: CLAIM_COLOR }}>
                claims
                <i className="block font-mono text-[10px] font-normal not-italic text-slate-400">{stats.claims}</i>
              </div>
            ) : null}
            {laneNames.map((name, index) => (
              <div
                key={name}
                className={`absolute left-0 right-0 pl-2.5 text-[11px] font-semibold text-slate-500 ${
                  index === obsLane ? "font-mono" : ""
                }`}
                style={{ top: (layout.laneTop + index * LANE_H - 4) * zoom, color: index === obsLane ? OBS_COLOR : undefined }}
              >
                {name}
                <i className="block font-mono text-[10px] font-normal not-italic text-slate-400">
                  {index === obsLane
                    ? `${stats.observations}，linked ${stats.observations_linked}`
                    : LANE_HINT[index]}
                </i>
              </div>
            ))}
          </div>
        </div>

        <div ref={wrap} className="relative min-w-0 flex-1 overflow-auto bg-slate-50">
          <div style={{ width: layout.width, height: layout.height, transformOrigin: "0 0", transform: `scale(${zoom})`, position: "relative" }}>
            {layout.claimRows ? (
              <div className="absolute left-0 border-t border-slate-300" style={{ top: layout.laneTop - ZONE_GAP / 2, width: layout.width }} />
            ) : null}
            {laneNames.map((name, index) => (
              <div
                key={name}
                className="pointer-events-none absolute left-0 border-t border-dashed border-slate-200"
                style={{ top: layout.laneTop + index * LANE_H - 8, width: layout.width }}
              />
            ))}

            <svg width={layout.width} height={layout.height} className="pointer-events-none absolute inset-0 overflow-visible">
              <defs>
                {RELATIONS.map((relation) => (
                  <marker
                    key={relation}
                    id={`arrow-${relation}`}
                    viewBox="0 0 8 8"
                    refX="7"
                    refY="4"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M0,0 L8,4 L0,8 z" fill={REL_COLOR[relation]} />
                  </marker>
                ))}
              </defs>
              {edges.map((edge) => {
                const a = visible.get(edge.from)!;
                const b = visible.get(edge.to)!;
                const forward = a.x <= b.x;
                const ax = forward ? a.x + a.w : a.x;
                const bx = forward ? b.x : b.x + b.w;
                const ay = a.y + a.h / 2;
                const by = b.y + b.h / 2;
                const dx = Math.max(26, Math.abs(bx - ax) * 0.4) * (forward ? 1 : -1);
                const hot = focus === edge.from || focus === edge.to;
                return (
                  <path
                    key={edge.id}
                    d={`M${ax},${ay} C${ax + dx},${ay} ${bx - dx},${by} ${bx},${by}`}
                    fill="none"
                    stroke={REL_COLOR[edge.type]}
                    strokeWidth={hot ? 2.6 : 1.5}
                    strokeDasharray={edge.type === "contextualizes" ? "4 3" : undefined}
                    opacity={focus ? (hot ? 1 : 0.05) : 0.45}
                    markerEnd={`url(#arrow-${edge.type})`}
                  />
                );
              })}
              {/* Which steps a claim rests on is drawn only for the claim in
                  focus; all of them at once buries the argument in lines. */}
              {focus && layout.byId.get(focus)?.kind === "claim"
                ? (layout.byId.get(focus)!.node as Claim).step_ids.map((stepId) => {
                    const claim = layout.byId.get(focus)!;
                    const step = visible.get(stepId);
                    if (!step) return null;
                    const ax = claim.x + claim.w / 2;
                    const ay = claim.y + claim.h;
                    const bx = step.x + step.w / 2;
                    const by = step.y;
                    return (
                      <path
                        key={stepId}
                        d={`M${ax},${ay} C${ax},${ay + 40} ${bx},${by - 40} ${bx},${by}`}
                        fill="none"
                        stroke={CLAIM_COLOR}
                        strokeWidth={1.2}
                        strokeDasharray="2 3"
                        opacity={0.85}
                      />
                    );
                  })
                : null}
            </svg>

            {[...visible.values()].map((item) => (
              <NodeCard
                key={item.id}
                item={item}
                obsLane={obsLane}
                dim={!!near && !near.has(item.id)}
                selected={item.id === selectedId}
                onSelect={() => onSelect(item.id)}
                onHover={setHovered}
              />
            ))}

            {showClaims && !filtersOn
              ? layout.placed
                  .filter((item) => item.kind === "claim" && (item.reachFrom ?? 0) < item.col)
                  .map((claim) => (
                    <div
                      key={`reach-${claim.id}`}
                      className="pointer-events-none absolute rounded-sm"
                      style={{
                        left: colX(claim.reachFrom ?? 0) + 4,
                        top: claim.y + CLAIM_H - 2,
                        width: colX(claim.col) - colX(claim.reachFrom ?? 0) + COL_W / 2,
                        height: focus === claim.id ? 3 : 2,
                        background: CLAIM_COLOR,
                        opacity: focus ? (focus === claim.id ? 0.9 : 0.06) : 0.3,
                      }}
                    />
                  ))
              : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function NodeCard({
  item,
  obsLane,
  dim,
  selected,
  onSelect,
  onHover,
}: {
  item: Placed;
  obsLane: number;
  dim: boolean;
  selected: boolean;
  onSelect: () => void;
  onHover: (id: string | null) => void;
}) {
  const { kind, node } = item;
  const step = kind === "step" ? (node as Step) : null;
  const withheld = !!step && isWithheld(step.eligibility);
  const accent =
    kind === "claim" ? CLAIM_COLOR : item.lane === obsLane ? OBS_COLOR : LANE_COLOR[item.lane] ?? "#64748b";
  const kindLabel =
    kind === "question"
      ? (node as Question).question_type || "問題"
      : kind === "position"
        ? "反方立場"
        : kind === "claim"
          ? (node as Claim).claim_type
          : kind === "observation"
            ? (node as Observation).observation_type || "observation"
            : step!.step_type || step!.discourse_role || "—";
  const lineClamp = kind === "observation" ? 3 : kind === "claim" ? 2 : 4;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onSelect();
      }}
      onMouseEnter={() => onHover(item.id)}
      onMouseLeave={() => onHover(null)}
      className={`absolute cursor-pointer overflow-hidden rounded-lg px-2 py-1.5 transition ${
        kind === "observation" && !item.linked ? "border-dashed bg-transparent" : "bg-white"
      } ${selected ? "ring-2 ring-indigo-500" : ""} hover:shadow-md`}
      style={{
        left: item.x,
        top: item.y,
        width: item.w,
        height: item.h,
        opacity: dim ? 0.14 : 1,
        borderWidth: 1,
        borderStyle: kind === "observation" && !item.linked ? "dashed" : "solid",
        borderColor: withheld ? "#be123c" : "#cbd5e1",
        borderLeftWidth: kind === "claim" || kind === "position" ? 4 : 3,
        borderLeftColor: kind === "position" ? "#be123c" : accent,
        background: kind === "claim" ? "#eef2ff" : undefined,
      }}
    >
      <span className="flex justify-between gap-1 font-mono text-[9.5px] text-slate-400">
        <span className="truncate">{node.label}</span>
        {withheld ? <span className="font-bold text-rose-600">⚠</span> : null}
      </span>
      <span className="block truncate font-mono text-[9.5px] text-slate-500">{kindLabel}</span>
      <div
        className="mt-0.5 overflow-hidden text-[11.5px] leading-[1.45]"
        style={{ display: "-webkit-box", WebkitBoxOrient: "vertical", WebkitLineClamp: lineClamp }}
      >
        {node.statement}
      </div>
    </div>
  );
}
