"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { getBookOrderIndex } from "@/app/utils/bible-order";
import type { OverviewRow, StageId } from "./operations-types";

/**
 * The corpus as a work queue, grouped the way the coverage page groups it.
 *
 * A flat 242-row table sorted by catalog id answers "what is the state of this
 * sermon" and nothing else. The work is organised by book -- a chapter gets
 * extracted together and an article is written from one passage -- so the
 * question actually being asked is "how far through 馬太福音 are we", which a
 * flat list cannot show at any length. Same grouping as
 * `/admin/wang/source-coverage`, deliberately: two pages over the same corpus
 * that disagree about its shape make the reader do the reconciling.
 */

/** Extraction exists for this source, current or not -- the same sense as 已抽取. */
export function isExtracted(row: OverviewRow): boolean {
  return ["current", "stale"].includes(row.stages.extraction.state);
}

function percent(part: number, whole: number): number {
  return whole ? Math.round((part / whole) * 100) : 0;
}

type RenderRow = (row: OverviewRow) => React.ReactNode;

export function ScriptureGroups({ rows, render }: { rows: OverviewRow[]; render: RenderRow }) {
  const books = useMemo(() => {
    const byBook = new Map<string, Map<number, OverviewRow[]>>();
    for (const row of rows) {
      if (!row.book) continue;
      const chapters = byBook.get(row.book) ?? new Map<number, OverviewRow[]>();
      // A source whose metadata names only a book sits at chapter 0 rather
      // than being given a chapter nobody chose.
      const key = row.chapter ?? 0;
      chapters.set(key, [...(chapters.get(key) ?? []), row]);
      byBook.set(row.book, chapters);
    }
    return [...byBook.entries()]
      .sort(([a], [b]) => getBookOrderIndex(a) - getBookOrderIndex(b))
      .map(([book, chapterMap]) => ({
        book,
        chapters: [...chapterMap.entries()]
          .sort(([a], [b]) => a - b)
          .map(([chapter, entries]) => ({
            chapter,
            entries: [...entries].sort(
              (a, b) =>
                (a.verse_start ?? 0) - (b.verse_start ?? 0) ||
                a.title.localeCompare(b.title, "zh-Hant"),
            ),
          })),
      }));
  }, [rows]);

  const unplaced = rows.filter((row) => !row.book);

  return (
    <div className="space-y-2">
      {books.map(({ book, chapters }) => (
        <BookGroup key={book} book={book} chapters={chapters} render={render} />
      ))}
      {unplaced.length ? (
        <FlatGroup
          title="沒有經卷歸屬"
          subtitle="目錄沒有給它 catalog_primary_passage"
          entries={unplaced}
          render={render}
        />
      ) : null}
    </div>
  );
}

export function TopicGroups({ rows, render }: { rows: OverviewRow[]; render: RenderRow }) {
  const topics = useMemo(() => {
    const byTopic = new Map<string, OverviewRow[]>();
    for (const row of rows) {
      for (const topic of row.topics) byTopic.set(topic, [...(byTopic.get(topic) ?? []), row]);
    }
    return [...byTopic.entries()].sort(([, a], [, b]) => b.length - a.length);
  }, [rows]);

  const untopiced = rows.filter((row) => row.topics.length === 0);

  return (
    <div className="space-y-2">
      {topics.map(([topic, entries]) => (
        <FlatGroup key={topic} title={topic} entries={entries} render={render} />
      ))}
      {untopiced.length ? (
        <FlatGroup
          title="沒有主題"
          subtitle="目錄沒有給它 topic。母本本來就不在講道目錄裡。"
          entries={untopiced}
          render={render}
        />
      ) : null}
    </div>
  );
}

function GroupHeader({
  title, subtitle, count, extracted, open, onToggle,
}: {
  title: string; subtitle?: string; count: number; extracted: number;
  open: boolean; onToggle: () => void;
}) {
  return (
    <button type="button" onClick={onToggle} className="flex w-full items-center gap-3 px-4 py-3 text-left">
      {open ? (
        <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
      ) : (
        <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
      )}
      <span className={`text-[15px] font-black ${extracted ? "text-slate-950" : "text-slate-400"}`}>
        {title}
      </span>
      <span className="font-mono text-[11.5px] text-slate-400">
        {subtitle ? `${subtitle} · ` : ""}
        {count} 篇
      </span>
      <span className="ml-auto flex items-center gap-2 whitespace-nowrap font-mono text-[11.5px]">
        <span className={extracted ? "text-slate-700" : "text-rose-500"}>
          已抽取 {extracted}/{count}
        </span>
        <span className="inline-block h-[7px] w-20 rounded-sm bg-rose-200">
          <span
            className="block h-[7px] rounded-sm bg-indigo-500"
            style={{ width: `${percent(extracted, count)}%` }}
          />
        </span>
      </span>
    </button>
  );
}

function BookGroup({
  book, chapters, render,
}: {
  book: string;
  chapters: Array<{ chapter: number; entries: OverviewRow[] }>;
  render: RenderRow;
}) {
  const entries = chapters.flatMap((item) => item.entries);
  const extracted = entries.filter(isExtracted).length;
  // A book nobody has touched opens to nothing useful, so it stays shut until
  // asked for; a book with work in it shows that work without a click.
  const [open, setOpen] = useState(extracted > 0);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white">
      <GroupHeader
        title={book}
        subtitle={`涵蓋 ${chapters.filter((item) => item.chapter).length} 章`}
        count={entries.length}
        extracted={extracted}
        open={open}
        onToggle={() => setOpen(!open)}
      />
      {open ? (
        <div className="border-t border-slate-100">
          {chapters.map(({ chapter, entries: chapterEntries }) => (
            <div key={chapter}>
              <p className="bg-slate-50 px-4 py-1 font-mono text-[11px] text-slate-500">
                {chapter ? `${book} ${chapter}` : "未標章"}
                <span className="ml-2 text-slate-400">{chapterEntries.length} 篇</span>
              </p>
              {chapterEntries.map((row) => (
                <div key={`${row.kind}:${row.source_id}`}>{render(row)}</div>
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function FlatGroup({
  title, subtitle, entries, render,
}: {
  title: string; subtitle?: string; entries: OverviewRow[]; render: RenderRow;
}) {
  const extracted = entries.filter(isExtracted).length;
  const [open, setOpen] = useState(extracted > 0);
  return (
    <section className="rounded-2xl border border-slate-200 bg-white">
      <GroupHeader
        title={title}
        subtitle={subtitle}
        count={entries.length}
        extracted={extracted}
        open={open}
        onToggle={() => setOpen(!open)}
      />
      {open ? (
        <div className="border-t border-slate-100">
          {entries.map((row) => (
            <div key={`${row.kind}:${row.source_id}`}>{render(row)}</div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
