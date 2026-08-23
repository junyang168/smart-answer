"use client";

import { useEffect, useMemo, useRef } from "react";
import type { Fragment, Run, Segment, SourceDetail } from "./types";
import { percent, timecode } from "./types";

type Props = {
  detail: SourceDetail;
  selectedFragmentIds: Set<string>;
  onSelectFragment: (fragmentId: string) => void;
  markUncovered: boolean;
  onlyUncovered: boolean;
  filter: string;
  scrollTo: { ordinal: number; nonce: number } | null;
};

type Piece = { start: number; end: number; run: Run | null };

/**
 * Split one sentence into the pieces a browser can paint.
 *
 * A highlight cannot nest, and two fragments quoting overlapping words is
 * ordinary, so the server already flattened them into non-overlapping runs.
 * Here each run is only clipped to the sentence it falls in, so a fragment
 * spanning two sentences stays one fragment while both sentences keep their
 * own covered/uncovered verdict.
 */
function pieces(start: number, end: number, runs: Run[]): Piece[] {
  const result: Piece[] = [];
  let cursor = start;
  for (const run of runs) {
    if (run.end <= start || run.start >= end) continue;
    const from = Math.max(run.start, start);
    const to = Math.min(run.end, end);
    if (from > cursor) result.push({ start: cursor, end: from, run: null });
    result.push({ start: from, end: to, run });
    cursor = to;
  }
  if (cursor < end) result.push({ start: cursor, end, run: null });
  return result;
}

export function SourceText({
  detail,
  selectedFragmentIds,
  onSelectFragment,
  markUncovered,
  onlyUncovered,
  filter,
  scrollTo,
}: Props) {
  const scroller = useRef<HTMLDivElement>(null);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return detail.segments.filter((segment) => {
      // A heading is structure, not material anyone failed to extract, so it
      // never joins the queue of segments still to account for.
      if (onlyUncovered && (segment.fragment_ids.length > 0 || segment.is_heading)) return false;
      if (needle && !segment.text.toLowerCase().includes(needle) && !segment.key.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [detail.segments, filter, onlyUncovered]);

  useEffect(() => {
    if (!scrollTo || !scroller.current) return;
    // The detail pane receives its measured height immediately after mount.
    // Wait through that layout update before positioning a deep-linked
    // fragment, otherwise scrollIntoView runs against the unconstrained pane
    // and the subsequent height change leaves the target below the viewport.
    let innerFrame = 0;
    const outerFrame = requestAnimationFrame(() => {
      innerFrame = requestAnimationFrame(() => {
        const target = scroller.current?.querySelector(`[data-segment="${scrollTo.ordinal}"]`);
        target?.scrollIntoView({ behavior: "auto", block: "center" });
      });
    });
    return () => {
      cancelAnimationFrame(outerFrame);
      cancelAnimationFrame(innerFrame);
    };
  }, [scrollTo]);

  return (
    <div ref={scroller} className="min-w-0 flex-1 overflow-auto bg-white px-4 py-4">
      {shown.length === 0 ? (
        <p className="py-16 text-center text-sm text-slate-400">沒有符合的 segment。</p>
      ) : null}
      {shown.map((segment) => (
        <SegmentBlock
          key={segment.key}
          segment={segment}
          fragments={detail.fragments}
          selectedFragmentIds={selectedFragmentIds}
          onSelectFragment={onSelectFragment}
          markUncovered={markUncovered}
        />
      ))}
    </div>
  );
}

function SegmentBlock({
  segment,
  fragments,
  selectedFragmentIds,
  onSelectFragment,
  markUncovered,
}: {
  segment: Segment;
  fragments: Record<string, Fragment>;
  selectedFragmentIds: Set<string>;
  onSelectFragment: (fragmentId: string) => void;
  markUncovered: boolean;
}) {
  const covered = segment.fragment_ids.length > 0;
  const heading = segment.is_heading && !covered;
  const share = percent(segment.covered_chars, segment.text.length);
  const body: React.ReactNode[] = [];
  let cursor = 0;

  const push = (from: number, to: number, node: React.ReactNode) => {
    if (to > from) body.push(node);
  };

  for (const sentence of segment.sentences) {
    // Whitespace between sentences carries the manuscript's own line breaks.
    push(cursor, sentence.start, <span key={`gap-${cursor}`}>{segment.text.slice(cursor, sentence.start)}</span>);
    const uncoveredMark = markUncovered && !sentence.covered && !heading ? "border-b border-dotted border-rose-300" : "";
    body.push(
      <span key={`s-${sentence.start}`} className={uncoveredMark}>
        {pieces(sentence.start, sentence.end, segment.runs).map((piece) => {
          const text = segment.text.slice(piece.start, piece.end);
          if (!piece.run) return <span key={piece.start}>{text}</span>;
          const active = piece.run.fragment_ids.some((id) => selectedFragmentIds.has(id));
          const anchored = piece.run.fragment_ids[0];
          return (
            <mark
              key={piece.start}
              onClick={() => onSelectFragment(anchored)}
              title={piece.run.fragment_ids.map((id) => fragments[id]?.id ?? id).join("\n")}
              className={`cursor-pointer rounded-[3px] px-px ${
                active ? "bg-amber-300 text-slate-950 ring-1 ring-amber-500" : "bg-indigo-100 text-slate-900 hover:bg-indigo-200"
              }`}
            >
              {text}
            </mark>
          );
        })}
      </span>,
    );
    cursor = sentence.end;
  }
  push(cursor, segment.text.length, <span key={`gap-${cursor}`}>{segment.text.slice(cursor)}</span>);

  return (
    <article data-segment={segment.ordinal} className="flex gap-3 border-b border-slate-100 py-2.5">
      <div className="w-24 shrink-0 pt-0.5 text-right">
        <p className="font-mono text-[11px] text-slate-400">{segment.key}</p>
        {segment.start_time !== null ? (
          <p className="font-mono text-[10.5px] text-slate-300">{timecode(segment.start_time)}</p>
        ) : null}
        <div className="mt-1 ml-auto h-1 w-16 rounded-full bg-slate-100">
          <div
            className={`h-1 rounded-full ${covered ? "bg-indigo-500" : "bg-transparent"}`}
            style={{ width: `${share}%` }}
          />
        </div>
        <p className={`mt-0.5 font-mono text-[10px] ${covered ? "text-slate-400" : heading ? "text-slate-300" : "text-rose-400"}`}>
          {covered ? `${share}%` : heading ? "標題" : "未抽取"}
        </p>
      </div>
      <p
        className={`min-w-0 flex-1 whitespace-pre-wrap leading-8 ${
          heading ? "text-[14px] font-bold text-slate-700" : covered ? "text-[14px] text-slate-900" : "text-[14px] text-slate-500"
        }`}
      >
        {body}
      </p>
    </article>
  );
}
