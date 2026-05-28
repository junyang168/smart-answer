"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import {
  BookOpenText,
  ExternalLink,
  Loader2,
  Quote,
  Search as SearchIcon,
  Send,
  Sparkles,
} from "lucide-react";

import {
  Citation,
  querySermonSearch,
  SermonSearchMode,
  SermonSearchResponse,
  SourceCard,
} from "./sermon-search";

export interface ProjectLink {
  title: string;
  google_doc_url?: string | null;
  available: boolean;
}

interface SermonSearchPanelProps {
  seriesId: string;
  seriesTitle: string;
  projectLinks: Record<string, ProjectLink>;
}

const EXAMPLE_QUESTIONS = [
  "什麼是耶和華的僕人？",
  "如何解釋太 16:19？",
  "教授對 16 章釋經都覆蓋了那些 verses？",
];
const SOURCE_ID_PATTERN = /[0-9a-f]{12}-\d{4}/gi;
const SOURCE_PARENTHESES_PATTERN = /[（(]([^（）()]*\bsource\s+[0-9a-f]{12}-\d{4}[^（）()]*)[）)]/gi;

function formatHeading(path: string[]) {
  return path.filter(Boolean).join(" > ");
}

function sourceUrl(source: SourceCard, projectLinks: Record<string, ProjectLink>) {
  const project = projectLinks[source.content_id];
  return project?.available ? project.google_doc_url || null : null;
}

function sourceAnchorId(sourceId: string) {
  return `source-${sourceId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function formatAnswerWithSourceNumbers(
  answer: string,
  sources: SourceCard[],
  citations: Citation[],
) {
  const numberBySourceId = new Map<string, number>();
  sources.forEach((source, index) => {
    numberBySourceId.set(source.source_id.toLowerCase(), index + 1);
  });

  const citationFor = (sourceId: string) => {
    const sourceNumber = numberBySourceId.get(sourceId.toLowerCase());
    if (!sourceNumber) {
      return "";
    }
    return `[${sourceNumber}](#${sourceAnchorId(sourceId)})`;
  };

  const formatted = answer
    .replace(SOURCE_PARENTHESES_PATTERN, (_match, body: string) => {
      const citations = Array.from(body.matchAll(SOURCE_ID_PATTERN))
        .map((item) => item[0])
        .filter((sourceId, index, array) => array.indexOf(sourceId) === index)
        .map(citationFor)
        .filter(Boolean);
      return citations.length ? citations.join(" ") : "";
    })
    .replace(/\bsource\s+([0-9a-f]{12}-\d{4})/gi, (_match, sourceId: string) =>
      citationFor(sourceId),
    )
    .replace(/\s+([，。；：、])/g, "$1")
    .replace(/[（(]\s*[）)]/g, "");
  if (formatted.includes("](#source-")) {
    return formatted;
  }

  const fallbackCitations = citations
    .map((citation) => citation.source_id)
    .filter((sourceId, index, array) => array.indexOf(sourceId) === index)
    .map(citationFor)
    .filter(Boolean);
  const sourceFallback = sources
    .slice(0, 4)
    .map((source) => citationFor(source.source_id))
    .filter(Boolean);
  const citationLine = (fallbackCitations.length ? fallbackCitations : sourceFallback).join(" ");
  return citationLine ? `${formatted.trim()} ${citationLine}` : formatted;
}

export function SermonSearchPanel({
  seriesId,
  seriesTitle,
  projectLinks,
}: SermonSearchPanelProps) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialQuestion = searchParams.get("q") || "";
  const initialMode = searchParams.get("mode") === "deep" ? "deep" : "normal";
  const lastLoadedQueryRef = useRef<string | null>(null);
  const [question, setQuestion] = useState(initialQuestion);
  const [mode, setMode] = useState<SermonSearchMode>(initialMode);
  const [result, setResult] = useState<SermonSearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const citationBySource = useMemo(() => {
    const map = new Map<string, string>();
    result?.citations.forEach((citation) => {
      if (!map.has(citation.source_id)) {
        map.set(citation.source_id, citation.quote);
      }
    });
    return map;
  }, [result]);
  const formattedAnswer = useMemo(
    () =>
      result
        ? formatAnswerWithSourceNumbers(result.answer, result.sources, result.citations)
        : "",
    [result],
  );

  const updateSearchUrl = useCallback(
    (nextQuestion: string, nextMode: SermonSearchMode) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("q", nextQuestion);
      if (nextMode === "deep") {
        params.set("mode", "deep");
      } else {
        params.delete("mode");
      }
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const runSearch = useCallback(
    async (
      nextQuestion = question,
      options: { nextMode?: SermonSearchMode; updateUrl?: boolean } = {},
    ) => {
      const trimmed = nextQuestion.trim();
      const searchMode = options.nextMode || mode;
      if (!trimmed || isLoading) {
        return;
      }

      lastLoadedQueryRef.current = `${searchMode}:${trimmed}`;
      setQuestion(trimmed);
      setMode(searchMode);
      setIsLoading(true);
      setError(null);
      if (options.updateUrl !== false) {
        updateSearchUrl(trimmed, searchMode);
      }

      try {
        const response = await querySermonSearch({
          question: trimmed,
          mode: searchMode,
          filters: {
            series_ids: [seriesId],
            project_types: ["sermon_note"],
          },
          top_k: searchMode === "deep" ? 24 : 12,
        });
        setResult(response);
      } catch (err) {
        setError(err instanceof Error ? err.message : "搜尋失敗");
      } finally {
        setIsLoading(false);
      }
    },
    [isLoading, mode, question, seriesId, updateSearchUrl],
  );

  useEffect(() => {
    const urlQuestion = searchParams.get("q") || "";
    const urlMode = searchParams.get("mode") === "deep" ? "deep" : "normal";
    setQuestion(urlQuestion);
    setMode(urlMode);
    const loadKey = `${urlMode}:${urlQuestion}`;
    if (urlQuestion.trim() && lastLoadedQueryRef.current !== loadKey) {
      lastLoadedQueryRef.current = loadKey;
      void runSearch(urlQuestion, { nextMode: urlMode, updateUrl: false });
    }
  }, [runSearch, searchParams]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void runSearch();
  }

  return (
    <section className="container mx-auto px-6 mt-10">
      <div className="max-w-6xl">
        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-500">
          <Sparkles className="h-4 w-4 text-sky-600" />
          <span>{seriesTitle} 搜尋問答</span>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm"
        >
          <div className="flex flex-col gap-3 md:flex-row md:items-center">
            <div className="relative flex-1">
              <SearchIcon className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" />
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                className="h-12 w-full rounded-xl border border-slate-200 bg-slate-50 pl-12 pr-4 text-base text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-sky-300 focus:bg-white focus:ring-4 focus:ring-sky-100"
                placeholder="問這個系列的講稿..."
              />
            </div>

            <div className="grid h-11 grid-cols-2 rounded-xl border border-slate-200 bg-slate-50 p-1 text-sm font-semibold text-slate-600 md:w-40">
              <button
                type="button"
                onClick={() => setMode("normal")}
                className={`rounded-lg transition ${
                  mode === "normal" ? "bg-white text-slate-950 shadow-sm" : "hover:text-slate-950"
                }`}
              >
                快速
              </button>
              <button
                type="button"
                onClick={() => setMode("deep")}
                className={`rounded-lg transition ${
                  mode === "deep" ? "bg-white text-slate-950 shadow-sm" : "hover:text-slate-950"
                }`}
              >
                深入
              </button>
            </div>

            <button
              type="submit"
              disabled={isLoading || !question.trim()}
              className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-sky-700 px-5 text-sm font-semibold text-white transition hover:bg-sky-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              搜尋
            </button>
          </div>
        </form>

        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLE_QUESTIONS.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => void runSearch(example)}
              className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 transition hover:border-sky-300 hover:text-sky-700"
            >
              {example}
            </button>
          ))}
        </div>

        {isLoading ? (
          <div className="mt-8 flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 text-slate-600 shadow-sm">
            <Loader2 className="h-5 w-5 animate-spin text-sky-700" />
            <span>正在檢索講稿、整理引用...</span>
          </div>
        ) : null}

        {error ? (
          <div className="mt-8 rounded-2xl border border-red-200 bg-red-50 px-5 py-4 text-sm text-red-700">
            {error}
          </div>
        ) : null}

        {result ? (
          <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
            <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-500">
                <BookOpenText className="h-4 w-4 text-sky-700" />
                <span>回答</span>
              </div>
              <div className="prose prose-slate max-w-none whitespace-pre-wrap text-slate-800 prose-p:leading-8">
                <ReactMarkdown
                  components={{
                    a: ({ children, href }) => (
                      <a
                        href={href}
                        className="mx-0.5 inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-sky-50 px-1.5 text-sm font-semibold text-sky-700 no-underline hover:bg-sky-100"
                      >
                        {children}
                      </a>
                    ),
                  }}
                >
                  {formattedAnswer}
                </ReactMarkdown>
              </div>

              {result.citations.length > 0 ? (
                <div className="mt-6 border-t border-slate-100 pt-5">
                  <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-500">
                    <Quote className="h-4 w-4 text-sky-700" />
                    <span>引用</span>
                  </div>
                  <div className="space-y-3">
                    {result.citations.slice(0, 4).map((citation) => (
                      <blockquote
                        key={`${citation.source_id}-${citation.quote}`}
                        className="border-l-2 border-sky-200 pl-4 text-sm text-slate-600"
                      >
                        <p className="leading-6">{citation.quote}</p>
                        <footer className="mt-2 text-xs font-semibold text-slate-500">
                          {citation.doc_title}
                        </footer>
                      </blockquote>
                    ))}
                  </div>
                </div>
              ) : null}

              {result.related_questions.length > 0 ? (
                <div className="mt-6 border-t border-slate-100 pt-5">
                  <p className="mb-3 text-sm font-semibold text-slate-500">延伸問題</p>
                  <div className="flex flex-wrap gap-2">
                    {result.related_questions.map((related) => (
                      <button
                        key={related}
                        type="button"
                        onClick={() => void runSearch(related)}
                        className="rounded-full border border-slate-200 px-3 py-1.5 text-sm text-slate-600 transition hover:border-sky-300 hover:text-sky-700"
                      >
                        {related}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </article>

            <aside className="rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="border-b border-slate-100 px-5 py-4">
                <p className="text-sm font-semibold text-slate-500">來源</p>
              </div>
              {result.sources.length === 0 ? (
                <div className="px-5 py-6 text-sm text-slate-500">沒有可顯示的來源。</div>
              ) : (
                <div className="divide-y divide-slate-100">
                  {result.sources.map((source, index) => {
                    const url = sourceUrl(source, projectLinks);
                    const quote = citationBySource.get(source.source_id);
                    return (
                      <div
                        id={sourceAnchorId(source.source_id)}
                        key={source.source_id}
                        className="scroll-mt-24 px-5 py-4"
                      >
                        <div className="mb-2 flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-xs font-semibold text-sky-700">
                              {String(index + 1).padStart(2, "0")}
                            </p>
                            <h3 className="mt-1 text-sm font-semibold leading-6 text-slate-900">
                              {source.doc_title}
                            </h3>
                          </div>
                          {url ? (
                            <a
                              href={url}
                              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-400 transition hover:bg-sky-50 hover:text-sky-700"
                              aria-label={`Open ${source.doc_title}`}
                            >
                              <ExternalLink className="h-4 w-4" />
                            </a>
                          ) : null}
                        </div>

                        {source.heading_path.length > 0 ? (
                          <p className="mb-2 text-xs leading-5 text-slate-500">
                            {formatHeading(source.heading_path)}
                          </p>
                        ) : null}

                        <p className="text-sm leading-6 text-slate-700">{source.snippet}</p>

                        {quote ? (
                          <p className="mt-2 border-l-2 border-slate-200 pl-3 text-xs leading-5 text-slate-500">
                            {quote}
                          </p>
                        ) : null}

                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {source.canonical_refs.slice(0, 4).map((ref) => (
                            <span
                              key={ref}
                              className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600"
                            >
                              {ref}
                            </span>
                          ))}
                          {source.topics.slice(0, 3).map((topic) => (
                            <span
                              key={topic}
                              className="rounded-full bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-700"
                            >
                              {topic}
                            </span>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              <details className="border-t border-slate-100 px-5 py-4">
                <summary className="cursor-pointer text-sm font-semibold text-slate-500">
                  檢索紀錄
                </summary>
                <div className="mt-3 space-y-3 text-xs leading-5 text-slate-500">
                  <p>Tools: {result.search_trace.tools_used.join(", ") || "-"}</p>
                  <p>Rounds: {result.search_trace.rounds}</p>
                  {result.search_trace.notes.map((note) => (
                    <p key={note}>{note}</p>
                  ))}
                  {result.search_trace.round_traces.map((round) => (
                    <div key={round.round} className="border-t border-slate-100 pt-3">
                      <p className="font-semibold text-slate-600">Round {round.round}</p>
                      <p>{round.query}</p>
                      <p>
                        {round.candidate_count} candidates, {round.selected_count} selected
                      </p>
                    </div>
                  ))}
                </div>
              </details>
            </aside>
          </div>
        ) : null}
      </div>
    </section>
  );
}
