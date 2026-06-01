"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { BookMarked, BookOpen, ChevronDown, ChevronRight, FileText, Layers, Search, Sparkles } from "lucide-react";

import { SermonSearchPanel, type ProjectLink } from "./SermonSearchPanel";
import type { NotesToManuscriptLecture, TopicCard } from "./data";

type ViewKey = "chapter" | "topic" | "manuscript" | "ask";

interface TopicNavigatorProps {
  seriesId: string;
  seriesTitle: string;
  topics: TopicCard[];
  topicsAvailable: boolean;
  projectLinks: Record<string, ProjectLink>;
  lectures: NotesToManuscriptLecture[];
}

function manuscriptHref(
  projectId: string,
  anchor: string | undefined,
  projectLinks: Record<string, ProjectLink>,
): string | null {
  const project = projectLinks[projectId];
  if (!project?.available || !project.href) return null;
  return anchor ? `${project.href}#${anchor}` : project.href;
}

// In chapter view the verse badge already shows the reference, so strip a
// leading "太 1:1–17：" prefix from the name to avoid showing it twice.
function nameWithoutRef(t: TopicCard): string {
  const ref = t.canonical_ref_raw?.trim();
  if (ref && t.name.startsWith(ref)) {
    return t.name.slice(ref.length).replace(/^[：:\-－—\s]+/, "").trim() || t.name;
  }
  return t.name;
}

// Sort key from an OSIS start ref like "Matt.1.6" -> [1, 6].
function osisStartKey(canonicalRef?: string | null): [number, number] {
  if (!canonicalRef) return [9999, 9999];
  const start = canonicalRef.split("-")[0];
  const parts = start.split(".");
  const chapter = Number(parts[1]) || 9999;
  const verse = Number(parts[2]) || 0;
  return [chapter, verse];
}

type Segment = { key: ViewKey; label: string; icon: typeof Layers };

const TOPIC_SEGMENTS: Segment[] = [
  { key: "chapter", label: "按章節", icon: BookMarked },
  { key: "topic", label: "按主題", icon: Layers },
];
const MANUSCRIPT_SEGMENT: Segment = { key: "manuscript", label: "逐篇瀏覽", icon: FileText };
const ASK_SEGMENT: Segment = { key: "ask", label: "問答", icon: Sparkles };

export function TopicNavigator({
  seriesId,
  seriesTitle,
  topics,
  topicsAvailable,
  projectLinks,
  lectures,
}: TopicNavigatorProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const hasTopics = topicsAvailable && topics.length > 0;
  // Chapter/topic only exist when an index is built; 逐篇 and 問答 are always present.
  const segments: Segment[] = [
    ...(hasTopics ? TOPIC_SEGMENTS : []),
    MANUSCRIPT_SEGMENT,
    ASK_SEGMENT,
  ];

  // Default: browse-first (按章節), but a shared/refreshed ?q= link lands on 問答.
  const hasQuery = Boolean(searchParams.get("q"));
  const fallbackView: ViewKey = hasQuery ? "ask" : hasTopics ? "chapter" : "manuscript";
  const urlView = searchParams.get("view") as ViewKey | null;
  const view: ViewKey = urlView && segments.some((s) => s.key === urlView) ? urlView : fallbackView;

  const setView = useCallback(
    (next: ViewKey) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("view", next);
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const passages = useMemo(
    () =>
      topics
        .filter((t) => t.type === "passage")
        .sort((a, b) => {
          const ka = osisStartKey(a.canonical_ref);
          const kb = osisStartKey(b.canonical_ref);
          return ka[0] - kb[0] || ka[1] - kb[1];
        }),
    [topics],
  );

  const concepts = useMemo(() => topics.filter((t) => t.type === "concept"), [topics]);

  return (
    <section className="container mx-auto px-6 mt-8">
      <div className="inline-flex flex-wrap rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
        {segments.map((seg) => {
          const Icon = seg.icon;
          const active = seg.key === view;
          return (
            <button
              key={seg.key}
              type="button"
              onClick={() => setView(seg.key)}
              className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${
                active ? "bg-sky-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              <Icon className="h-4 w-4" />
              {seg.label}
            </button>
          );
        })}
      </div>

      <div className="mt-6">
        {view === "chapter" && hasTopics ? (
          <ChapterView passages={passages} projectLinks={projectLinks} />
        ) : null}
        {view === "topic" && hasTopics ? (
          <TopicView concepts={concepts} projectLinks={projectLinks} />
        ) : null}
        {view === "manuscript" ? (
          <ManuscriptView seriesId={seriesId} lectures={lectures} />
        ) : null}
        {view === "ask" ? (
          <SermonSearchPanel
            seriesId={seriesId}
            seriesTitle={seriesTitle}
            projectLinks={projectLinks}
            embedded
          />
        ) : null}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 按章節
// ---------------------------------------------------------------------------

function ChapterView({
  passages,
  projectLinks,
}: {
  passages: TopicCard[];
  projectLinks: Record<string, ProjectLink>;
}) {
  const groups = useMemo(() => {
    const byChapter = new Map<number, TopicCard[]>();
    for (const t of passages) {
      const ch = t.chapter ?? 9999;
      if (!byChapter.has(ch)) byChapter.set(ch, []);
      byChapter.get(ch)!.push(t);
    }
    return Array.from(byChapter.entries()).sort((a, b) => a[0] - b[0]);
  }, [passages]);

  if (groups.length === 0) {
    return <EmptyHint text="此系列尚未建立經文主題索引。" />;
  }

  return (
    <div className="space-y-6">
      {groups.map(([chapter, items]) => (
        <article key={chapter} className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          <div className="border-b border-slate-200 bg-slate-50 px-6 py-4">
            <h3 className="text-xl font-bold text-slate-900">
              {chapter === 9999 ? "其他" : `馬太福音 第 ${chapter} 章`}
            </h3>
          </div>
          <div className="divide-y divide-slate-100">
            {items.map((t) => {
              const src = t.sources[0];
              const href = src
                ? manuscriptHref(src.project_id, src.section_anchors[0], projectLinks)
                : null;
              const row = (
                <div className="flex min-w-0 items-baseline gap-2.5">
                  {t.canonical_ref_raw ? (
                    <span className="shrink-0 rounded-md bg-sky-50 px-2 py-0.5 text-xs font-semibold text-sky-700">
                      {t.canonical_ref_raw}
                    </span>
                  ) : null}
                  <p className="font-semibold text-slate-900">{nameWithoutRef(t)}</p>
                </div>
              );
              return href ? (
                <Link key={t.id} href={href} className="block px-6 py-4 transition hover:bg-sky-50/50">
                  {row}
                </Link>
              ) : (
                <div key={t.id} className="px-6 py-4">
                  {row}
                </div>
              );
            })}
          </div>
        </article>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 按主題
// ---------------------------------------------------------------------------

function TopicView({
  concepts,
  projectLinks,
}: {
  concepts: TopicCard[];
  projectLinks: Record<string, ProjectLink>;
}) {
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return concepts;
    return concepts.filter((t) => {
      const hay = [t.name, ...t.aliases, ...t.sources.flatMap((s) => s.lun_dian)];
      return hay.some((h) => h.toLowerCase().includes(needle));
    });
  }, [concepts, filter]);

  if (concepts.length === 0) {
    return <EmptyHint text="此系列尚未建立概念主題索引。" />;
  }

  return (
    <div>
      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="篩選主題…（名稱、原文、論點）"
          className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-10 pr-4 text-sm outline-none focus:border-sky-400"
        />
      </div>

      <div className="mt-4 space-y-2">
        {filtered.map((t) => {
          const isOpen = expanded === t.id;
          return (
            <div key={t.id} className="rounded-xl border border-slate-200 bg-white">
              <button
                type="button"
                onClick={() => setExpanded(isOpen ? null : t.id)}
                className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left"
              >
                <span className="font-semibold text-slate-900">{t.name}</span>
                {isOpen ? (
                  <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
                ) : (
                  <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
                )}
              </button>

              {isOpen ? (
                <div className="border-t border-slate-100 px-5 py-4">
                  {t.notes ? <p className="mb-3 text-sm italic text-slate-500">{t.notes}</p> : null}
                  {t.sources.map((src, idx) => {
                    const href = manuscriptHref(src.project_id, src.section_anchors[0], projectLinks);
                    return (
                      <div key={idx} className="mb-4 last:mb-0">
                        <ul className="space-y-1.5">
                          {src.lun_dian.map((ld, i) => (
                            <li key={i} className="flex gap-2 text-sm leading-7 text-slate-700">
                              <span className="text-sky-400">•</span>
                              <span>{ld}</span>
                            </li>
                          ))}
                        </ul>
                        <p className="mt-2 text-xs text-slate-400">
                          出處：{src.project_title} · {src.source_sections.join("、")}
                        </p>
                        {href ? (
                          <Link
                            href={href}
                            className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-sky-50 px-3.5 py-2 text-sm font-semibold text-sky-700 transition hover:bg-sky-100"
                          >
                            <BookOpen className="h-4 w-4" />
                            閱讀段落
                            <span aria-hidden>→</span>
                          </Link>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
        {filtered.length === 0 ? <EmptyHint text="沒有符合的主題。" /> : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 逐篇瀏覽
// ---------------------------------------------------------------------------

function ManuscriptView({
  seriesId,
  lectures,
}: {
  seriesId: string;
  lectures: NotesToManuscriptLecture[];
}) {
  if (lectures.length === 0) {
    return <EmptyHint text="此系列目前尚未整理任何講次。" />;
  }
  return (
    <div className="space-y-8">
      {lectures.map((lecture, lectureIndex) => (
        <article key={lecture.id} className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          <div className="px-6 py-5 border-b border-slate-200 bg-slate-50">
            <p className="text-sm font-semibold text-slate-400">第 {lectureIndex + 1} 講</p>
            <h2 className="mt-1 text-2xl font-bold text-slate-900">{lecture.title}</h2>
            {lecture.description ? <p className="mt-2 text-slate-600">{lecture.description}</p> : null}
          </div>
          <div className="p-6 space-y-3">
            {lecture.projects.map((project) =>
              project.available ? (
                // Row + title open the internal (auth-gated) reader via a
                // stretched link; the Google Doc link sits above it (z-10) as
                // an independent secondary action. Both are reachable only by
                // signed-in site users since this whole section is gated.
                <div
                  key={project.id}
                  className="relative flex items-center justify-between gap-4 rounded-xl border border-slate-200 px-4 py-4 transition hover:border-sky-300 hover:bg-sky-50/40"
                >
                  <Link
                    href={`/resources/notes_to_manuscript_series/${seriesId}/${project.id}`}
                    className="after:absolute after:inset-0"
                  >
                    <h3 className="text-lg font-semibold text-slate-900">{project.title}</h3>
                  </Link>
                  {project.google_doc_url ? (
                    <a
                      href={project.google_doc_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="relative z-10 whitespace-nowrap text-sm font-semibold text-sky-700 hover:underline"
                    >
                      Google Doc ↗
                    </a>
                  ) : (
                    <span className="text-sm font-semibold text-sky-700 whitespace-nowrap">查看稿件 →</span>
                  )}
                </div>
              ) : (
                <div key={project.id} className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-4">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <h3 className="text-lg font-semibold text-slate-700">{project.title}</h3>
                      <p className="mt-1 text-sm text-slate-500">尚未發布逐字稿</p>
                    </div>
                    <span className="text-sm font-semibold text-slate-400 whitespace-nowrap">未就緒</span>
                  </div>
                </div>
              ),
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center text-slate-500">
      {text}
    </div>
  );
}
