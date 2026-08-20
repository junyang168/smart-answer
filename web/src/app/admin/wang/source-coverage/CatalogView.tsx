"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { getBookOrderIndex } from "@/app/utils/bible-order";
import type { CatalogEntry, SourceMeta } from "./types";
import { percent } from "./types";

type Props = {
  catalog: CatalogEntry[];
  sources: SourceMeta[];
  onOpen: (sourceId: string) => void;
};

type Stats = Map<string, SourceMeta>;

/** Book and chapter only. The verse range is the sermon's business, not the index's. */
function chapterLabel(entry: CatalogEntry): string {
  if (!entry.book) return "";
  return entry.chapter ? `${entry.book} ${entry.chapter}` : entry.book;
}

/**
 * The corpus as a work queue.
 *
 * The flat table lists the sources that have a claim layer.  It cannot show
 * the ones that do not, and today those are nine sermons in ten.  Grouping the
 * whole catalog by book and chapter makes the untouched part the thing you see
 * first — the same move as highlighting only the extracted words on a
 * transcript.
 */
export function CatalogView({ catalog, sources, onOpen }: Props) {
  const [tab, setTab] = useState<"scripture" | "topic">("scripture");
  const statsById = useMemo(() => new Map(sources.map((item) => [item.source_id, item])), [sources]);

  return (
    <>
      <div className="mb-4 flex flex-wrap gap-2">
        {(
          [
            ["scripture", "聖經目錄"],
            ["topic", "講道主題"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`rounded-xl px-4 py-2 text-[13px] font-bold ${
              tab === key ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "scripture" ? (
        <ScriptureGroups catalog={catalog} statsById={statsById} onOpen={onOpen} />
      ) : (
        <TopicGroups catalog={catalog} statsById={statsById} onOpen={onOpen} />
      )}
    </>
  );
}

type GroupProps = { catalog: CatalogEntry[]; statsById: Stats; onOpen: (sourceId: string) => void };

function ScriptureGroups({ catalog, statsById, onOpen }: GroupProps) {
  const books = useMemo(() => {
    const byBook = new Map<string, Map<number, CatalogEntry[]>>();
    for (const entry of catalog) {
      if (!entry.book) continue;
      const chapters = byBook.get(entry.book) ?? new Map<number, CatalogEntry[]>();
      // A source whose metadata names only a book sits at chapter 0 rather
      // than being given a chapter nobody chose.
      const key = entry.chapter ?? 0;
      chapters.set(key, [...(chapters.get(key) ?? []), entry]);
      byBook.set(entry.book, chapters);
    }
    return [...byBook.entries()]
      .sort(([a], [b]) => getBookOrderIndex(a) - getBookOrderIndex(b))
      .map(([book, chapterMap]) => ({
        book,
        chapters: [...chapterMap.entries()]
          .sort(([a], [b]) => a - b)
          .map(([chapter, entries]) => ({
            chapter,
            // Verse order is the chapter's reading order, and it is still the
            // right sort even though the verse itself is not shown.
            entries: [...entries].sort(
              (a, b) =>
                (a.verse_start ?? 0) - (b.verse_start ?? 0) || a.title.localeCompare(b.title, "zh-Hant"),
            ),
          })),
      }));
  }, [catalog]);

  const unplaced = catalog.filter((entry) => !entry.book);

  return (
    <div className="space-y-2">
      {books.map(({ book, chapters }) => (
        <BookGroup key={book} book={book} chapters={chapters} statsById={statsById} onOpen={onOpen} />
      ))}
      {unplaced.length ? (
        <FlatGroup
          title="沒有經卷歸屬"
          subtitle="目錄沒有給它 catalog_primary_passage"
          entries={unplaced}
          statsById={statsById}
          onOpen={onOpen}
          showPassage
        />
      ) : null}
    </div>
  );
}

function TopicGroups({ catalog, statsById, onOpen }: GroupProps) {
  const topics = useMemo(() => {
    const byTopic = new Map<string, CatalogEntry[]>();
    for (const entry of catalog) {
      for (const topic of entry.topics) byTopic.set(topic, [...(byTopic.get(topic) ?? []), entry]);
    }
    return [...byTopic.entries()].sort(([, a], [, b]) => b.length - a.length);
  }, [catalog]);

  const untopiced = catalog.filter((entry) => entry.topics.length === 0);

  return (
    <div className="space-y-2">
      {topics.map(([topic, entries]) => (
        <FlatGroup key={topic} title={topic} entries={entries} statsById={statsById} onOpen={onOpen} showPassage />
      ))}
      {untopiced.length ? (
        <FlatGroup
          title="沒有主題"
          subtitle="目錄沒有給它 topic。三份母本本來就不在講道目錄裡。"
          entries={untopiced}
          statsById={statsById}
          onOpen={onOpen}
          showPassage
        />
      ) : null}
    </div>
  );
}

function GroupHeader({
  title,
  subtitle,
  count,
  extracted,
  open,
  onToggle,
}: {
  title: string;
  subtitle?: string;
  count: number;
  extracted: number;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button type="button" onClick={onToggle} className="flex w-full items-center gap-3 px-4 py-3 text-left">
      {open ? (
        <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
      ) : (
        <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
      )}
      <span className={`text-[15px] font-black ${extracted ? "text-slate-950" : "text-slate-400"}`}>{title}</span>
      <span className="font-mono text-[11.5px] text-slate-400">
        {subtitle ? `${subtitle} · ` : ""}
        {count} 篇
      </span>
      <span className="ml-auto flex items-center gap-2 whitespace-nowrap font-mono text-[11.5px]">
        <span className={extracted ? "text-slate-700" : "text-rose-500"}>
          已抽取 {extracted}/{count}
        </span>
        <span className="inline-block h-[7px] w-20 rounded-sm bg-rose-200">
          <span className="block h-[7px] rounded-sm bg-indigo-500" style={{ width: `${percent(extracted, count)}%` }} />
        </span>
      </span>
    </button>
  );
}

function SermonRow({
  entry,
  statsById,
  onOpen,
  showPassage,
}: {
  entry: CatalogEntry;
  statsById: Stats;
  onOpen: (sourceId: string) => void;
  showPassage?: boolean;
}) {
  const stats = entry.source_id ? statsById.get(entry.source_id) : undefined;
  return (
    <div
      onClick={() => entry.source_id && onOpen(entry.source_id)}
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 py-2 pl-10 pr-4 text-[12.5px] last:border-b-0 ${
        entry.source_id ? "cursor-pointer hover:bg-indigo-50" : ""
      }`}
    >
      {showPassage ? (
        <span className="w-28 shrink-0 font-mono text-[11px] text-slate-400">{chapterLabel(entry) || "—"}</span>
      ) : null}
      <span className="min-w-0 flex-1">
        <span className={`block truncate ${stats ? "text-slate-900" : "text-slate-400"}`}>
          {entry.title}
          {entry.kind === "notes_manuscript" ? (
            <i className="ml-2 font-mono text-[10.5px] not-italic text-slate-400">母本</i>
          ) : null}
        </span>
        {entry.file ? <span className="block truncate font-mono text-[10.5px] text-slate-400">{entry.file}</span> : null}
      </span>
      {stats ? (
        <span className="flex shrink-0 items-center gap-3 font-mono text-[11px] text-slate-500">
          <span>
            句子{" "}
            <b className="text-slate-800">
              {stats.stats.prose_sentences_covered}/{stats.stats.prose_sentences}
            </b>{" "}
            {percent(stats.stats.prose_sentences_covered, stats.stats.prose_sentences)}%
          </span>
          <span>claims {stats.stats.claims}</span>
        </span>
      ) : (
        <span className="shrink-0 font-mono text-[11px] text-rose-400">未抽取</span>
      )}
    </div>
  );
}

function BookGroup({
  book,
  chapters,
  statsById,
  onOpen,
}: {
  book: string;
  chapters: Array<{ chapter: number; entries: CatalogEntry[] }>;
  statsById: Stats;
  onOpen: (sourceId: string) => void;
}) {
  const entries = chapters.flatMap((item) => item.entries);
  const extracted = entries.filter((entry) => entry.source_id).length;
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
              {chapterEntries.map((entry, index) => (
                <SermonRow
                  key={`${entry.item ?? entry.source_id}-${index}`}
                  entry={entry}
                  statsById={statsById}
                  onOpen={onOpen}
                />
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function FlatGroup({
  title,
  subtitle,
  entries,
  statsById,
  onOpen,
  showPassage,
}: {
  title: string;
  subtitle?: string;
  entries: CatalogEntry[];
  statsById: Stats;
  onOpen: (sourceId: string) => void;
  showPassage?: boolean;
}) {
  const extracted = entries.filter((entry) => entry.source_id).length;
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
          {entries.map((entry, index) => (
            <SermonRow
              key={`${entry.item ?? entry.source_id}-${index}`}
              entry={entry}
              statsById={statsById}
              onOpen={onOpen}
              showPassage={showPassage}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}
