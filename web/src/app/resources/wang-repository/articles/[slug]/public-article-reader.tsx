"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { BookOpen, BookOpenText, CalendarDays, ChevronDown, Headphones, ListOrdered, Pause, Play, RotateCcw } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { PublicArticleClip, PublicAudioSection, PublicWangArticle } from "../article-types";
import { SourceEvidenceDisclosure } from "@/app/admin/wang/operations/articles/reviews/SourceEvidenceDisclosure";
import type { ReviewSourceAnnotation } from "@/app/admin/wang/operations/articles/reviews/types";
import { extractArticleToc, type ArticleTocHeading } from "./article-toc";
import { collectArticleSources } from "./article-sources";

type ViewMode = "read" | "listen";

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (node && typeof node === "object" && "props" in node) {
    return nodeText((node as { props?: { children?: ReactNode } }).props?.children);
  }
  return "";
}

function nodeHref(node: ReactNode): string | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const href = nodeHref(child);
      if (href) return href;
    }
  }
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props?: { href?: unknown; children?: ReactNode } }).props;
    if (typeof props?.href === "string") return props.href;
    return nodeHref(props?.children);
  }
  return null;
}

type Footnote = { id: string; markdown: string; sourceAnnotationId: string | null };

function prepareFootnotes(markdown: string): { body: string; footnotes: Footnote[] } {
  const footnotes: Footnote[] = [];
  const body = markdown
    .split("\n")
    .filter((line) => {
      const match = line.match(/^\[\^([^\]]+)\]:\s*(.+)$/);
      if (!match) return true;
      const sourceMatch = match[2].match(
        /\s*\[查看本注来源\]\(#review-source-evidence-(p\d+)\)\s*$/,
      );
      footnotes.push({
        id: match[1],
        markdown: sourceMatch ? match[2].slice(0, sourceMatch.index).trimEnd() : match[2],
        sourceAnnotationId: sourceMatch?.[1] ?? null,
      });
      return false;
    })
    .join("\n")
    .replace(/\[\^([^\]]+)\]/g, (_match, id: string) => `[${id}](#article-footnote-${id})`);
  return { body, footnotes };
}

const CHINESE_NUMERALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];

function sectionCountLabel(count: number) {
  const numeral = count >= 1 && count <= 10 ? CHINESE_NUMERALS[count - 1] : String(count);
  return `本文${numeral}節`;
}

/** 页头的经文：专题文章列全部核心经文，释经文章照旧是「馬太福音 · 章:節」。 */
function passageLabel(article: PublicWangArticle) {
  const references = (article.core_scripture ?? []).map((reference) => reference.display);
  if (article.product_kind === "theological_topic_essay" && references.length > 0) {
    return `專題 · ${references.join("；")}`;
  }
  return `馬太福音 · ${article.passage.replace(/^太/, "")}`;
}

function formatTime(value: number | null) {
  if (typeof value !== "number") return "";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = Math.floor(value % 60);
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatDeliveredOn(value: string | null) {
  if (!value) return null;
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return match ? `${match[1]} 年 ${Number(match[2])} 月 ${Number(match[3])} 日` : value;
}

function sectionId(index: number) {
  return `article-section-${index + 1}`;
}

function listenId(index: number) {
  return `original-audio-section-${index + 1}`;
}

type PlayerProps = {
  clip: PublicArticleClip;
  playerKey: string;
  onRegister: (key: string, media: HTMLMediaElement | null) => void;
  onPlay: (key: string, label: string, heading: string) => void;
  onPause: (key: string) => void;
  heading: string;
};

function OriginalClipPlayer({ clip, playerKey, onRegister, onPlay, onPause, heading }: PlayerProps) {
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const [clipPlaying, setClipPlaying] = useState(false);
  const seekToStart = useCallback((media: HTMLMediaElement) => {
    if (typeof clip.start_seconds === "number" && clip.start_seconds >= 0) {
      media.currentTime = clip.start_seconds;
    }
  }, [clip.start_seconds]);
  useEffect(() => {
    const media = mediaRef.current;
    if (!media) return;
    const applyStart = () => seekToStart(media);
    if (media.readyState >= 1) applyStart();
    else media.addEventListener("loadedmetadata", applyStart, { once: true });
    return () => media.removeEventListener("loadedmetadata", applyStart);
  }, [clip.media.url, seekToStart]);
  const timeLabel = typeof clip.start_seconds === "number"
    ? `${formatTime(clip.start_seconds)}${typeof clip.end_seconds === "number" ? `–${formatTime(clip.end_seconds)}` : ""}`
    : null;
  const deliveredOn = formatDeliveredOn(clip.delivered_on);
  const commonProps = {
    controls: true,
    preload: "metadata" as const,
    onLoadedMetadata: (event: React.SyntheticEvent<HTMLMediaElement>) => seekToStart(event.currentTarget),
    onPlay: (event: React.SyntheticEvent<HTMLMediaElement>) => {
      const media = event.currentTarget;
      if (
        typeof clip.start_seconds === "number"
        && (media.currentTime < clip.start_seconds - 0.5 || (typeof clip.end_seconds === "number" && media.currentTime >= clip.end_seconds))
      ) seekToStart(media);
      setClipPlaying(true);
      onPlay(playerKey, clip.title, heading);
    },
    onPause: () => {
      setClipPlaying(false);
      onPause(playerKey);
    },
    onTimeUpdate: (event: React.SyntheticEvent<HTMLMediaElement>) => {
      if (typeof clip.end_seconds === "number" && event.currentTarget.currentTime >= clip.end_seconds) {
        event.currentTarget.pause();
      }
    },
  };

  return (
    <article className="overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-[0_10px_35px_rgba(70,55,35,0.06)]">
      <div className="p-4 sm:p-5">
        <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:gap-3">
          <div className="min-w-0 flex-1">
            <h4 className="font-semibold leading-6 text-stone-900">{clip.title}</h4>
            {clip.sermon_label !== clip.title && <p className="mt-1 text-sm text-stone-500">{clip.sermon_label}</p>}
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-500">
              {deliveredOn && <span className="inline-flex items-center gap-1"><CalendarDays className="h-3.5 w-3.5" />{deliveredOn}</span>}
              {timeLabel && <span>原聲定位 {timeLabel}</span>}
            </div>
          </div>
          <div className="flex w-full shrink-0 flex-wrap items-center justify-between gap-3 sm:w-auto sm:justify-start">
            <button
              type="button"
              onClick={() => {
                const media = mediaRef.current;
                if (!media) return;
                if (media.paused) void media.play();
                else media.pause();
              }}
              className="inline-flex items-center gap-1.5 rounded-full bg-stone-900 px-3 py-2 text-sm font-semibold text-white hover:bg-amber-900"
              aria-label={`${clipPlaying ? "暫停" : "播放"}${clip.title}`}
            >
              {clipPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              {clipPlaying ? "暫停" : "播放本段"}
            </button>
            <Link href={clip.public_url} target="_blank" rel="noopener noreferrer" className="text-sm font-semibold text-amber-800 underline-offset-4 hover:underline">
              完整講道與逐字稿 ↗
            </Link>
          </div>
        </div>
      </div>
      {clip.media.kind === "video" ? (
        <video
          {...commonProps}
          ref={(media) => { mediaRef.current = media; onRegister(playerKey, media); }}
          className="max-h-[32rem] w-full bg-black"
          aria-label={`${clip.title} 原聲錄像`}
        >
          <source src={clip.media.url ?? undefined} type="video/mp4" />
          您的瀏覽器不支持影片播放。
        </video>
      ) : (
        <div className="border-t border-stone-100 bg-[#fbfaf7] px-4 py-4 sm:px-5">
          <audio
            {...commonProps}
            ref={(media) => { mediaRef.current = media; onRegister(playerKey, media); }}
            className="w-full"
            aria-label={`${clip.title} 原聲錄音`}
          >
            <source src={clip.media.url ?? undefined} type="audio/mpeg" />
            您的瀏覽器不支持音訊播放。
          </audio>
        </div>
      )}
    </article>
  );
}

export function PublicArticleReader({ article }: { article: PublicWangArticle }) {
  const [mode, setMode] = useState<ViewMode>("read");
  const [activeSection, setActiveSection] = useState(0);
  const [showSources, setShowSources] = useState(false);
  const [activePlayer, setActivePlayer] = useState<{ key: string; label: string; heading: string } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [tocOpen, setTocOpen] = useState(false);
  const [siteHeader, setSiteHeader] = useState(139);
  const [readerOffset, setReaderOffset] = useState(220);
  const players = useRef(new Map<string, HTMLMediaElement>());
  const barRef = useRef<HTMLDivElement | null>(null);

  const hasAudio = article.audio_sections.length > 0;
  // 标题已在页头；稿件的 H1 不一定在第一行（专题文章先是「讀者問」），去掉第一个 H1 即可。
  const articleBody = article.markdown.replace(/^#\s+.+(?:\r?\n)+/m, "");
  const { body: articleProse, footnotes } = useMemo(() => prepareFootnotes(articleBody), [articleBody]);
  const sourceAnnotations = useMemo(
    () => new Map((article.source_annotations ?? []).map((item: ReviewSourceAnnotation) => [item.annotation_id, item.sources])),
    [article.source_annotations],
  );

  const articleSources = useMemo(() => collectArticleSources(article.source_annotations ?? []), [article.source_annotations]);

  const headingGroups = useMemo(() => {
    const groups: Array<{ heading: string; blocks: PublicAudioSection[] }> = [];
    for (const block of article.audio_sections) {
      const existing = groups.find((group) => group.heading === block.heading);
      if (existing) existing.blocks.push(block);
      else groups.push({ heading: block.heading, blocks: [block] });
    }
    return groups;
  }, [article.audio_sections]);

  const clipCounts = useMemo(
    () => new Map(headingGroups.map((group) => [group.heading, group.blocks.reduce((sum, block) => sum + block.clips.length, 0)])),
    [headingGroups],
  );

  // 目录以正文标题为准：旧管线的文章跟着原声段落走（两边标题一致），
  // draft-first 的文章没有 audio_sections，目录从 markdown 的 ##/### 标题生成。
  const sectionHeadings = useMemo(() => {
    if (hasAudio) {
      return headingGroups.map<ArticleTocHeading>((group, ordinal) => ({
        id: sectionId(ordinal),
        label: group.heading,
        level: 3,
        line: 0,
        ordinal,
      }));
    }
    return extractArticleToc(articleProse);
  }, [hasAudio, headingGroups, articleProse]);

  // 网站顶栏在笔记本和手机上高度不同，吸顶栏、目录和跳转位置都按实际高度算。
  useEffect(() => {
    const measure = () => {
      const site = document.getElementById("global-site-header")?.offsetHeight ?? 0;
      const bar = barRef.current?.offsetHeight ?? 0;
      setSiteHeader(site);
      setReaderOffset(site + bar + 16);
    };
    measure();
    const observer = new ResizeObserver(measure);
    const site = document.getElementById("global-site-header");
    if (site) observer.observe(site);
    if (barRef.current) observer.observe(barRef.current);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  useEffect(() => {
    if (mode !== "read") return;
    const nodes = sectionHeadings.map((_, index) => document.getElementById(sectionId(index))).filter(Boolean) as HTMLElement[];
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (visible) setActiveSection(Number(visible.target.id.replace("article-section-", "")) - 1);
    }, { rootMargin: "-18% 0px -68% 0px" });
    nodes.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [sectionHeadings, mode]);

  const scrollToView = useCallback((targetMode: ViewMode, index: number) => {
    setTocOpen(false);
    setMode(targetMode);
    setActiveSection(index);
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        document.getElementById(targetMode === "read" ? sectionId(index) : listenId(index))?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  }, []);

  const registerPlayer = useCallback((key: string, media: HTMLMediaElement | null) => {
    if (media) players.current.set(key, media);
    else players.current.delete(key);
  }, []);

  const handlePlay = useCallback((key: string, label: string, heading: string) => {
    for (const [otherKey, media] of players.current.entries()) {
      if (otherKey !== key && !media.paused) media.pause();
    }
    setActivePlayer({ key, label, heading });
    setPlaying(true);
  }, []);

  const handlePause = useCallback((key: string) => {
    if (activePlayer?.key === key) setPlaying(false);
  }, [activePlayer?.key]);

  const toggleCurrentPlayer = useCallback(() => {
    if (!activePlayer) return;
    const media = players.current.get(activePlayer.key);
    if (!media) return;
    if (media.paused) void media.play();
    else media.pause();
  }, [activePlayer]);

  const sectionIndexForHeading = (level: 2 | 3, title: string, line?: number) => {
    return sectionHeadings.findIndex(
      (heading) =>
        heading.level === level
        && heading.label === title
        && (hasAudio || heading.line === line),
    );
  };

  const tocItems = sectionHeadings.map((heading, index) => (
    <li key={heading.id}>
      <button
        type="button"
        onClick={() => scrollToView(mode, index)}
        aria-current={activeSection === index ? "location" : undefined}
        className={`w-full rounded-lg px-3 py-2 text-left text-sm leading-6 transition ${
          activeSection === index ? "bg-amber-100 font-semibold text-amber-950" : "text-stone-600 hover:bg-stone-100 hover:text-stone-900"
        }`}
      >
        {heading.label}
      </button>
    </li>
  ));
  const showBar = hasAudio || sectionHeadings.length > 0;

  return (
    <main
      className="min-h-screen bg-[#f7f4ee] pb-28 text-stone-900"
      style={{ "--site-header": `${siteHeader}px`, "--reader-offset": `${readerOffset}px` } as React.CSSProperties}
    >
      <header className="border-b border-stone-200/80 bg-[#eee7da]">
        <div className="mx-auto max-w-6xl px-5 py-7 sm:px-8 sm:py-10 lg:py-12">
          <Link href="/resources/wang-repository" className="text-sm font-semibold text-amber-900 underline-offset-4 hover:underline">王守仁教授聖經講論文庫</Link>
          <p className="mt-5 text-xs font-bold tracking-[0.14em] text-amber-900 sm:text-sm">{passageLabel(article)}</p>
          <h1 className="mt-3 max-w-4xl text-balance font-serif text-[1.75rem] font-bold leading-[1.3] text-stone-950 sm:text-4xl lg:text-[2.6rem]">{article.title}</h1>
          {article.reader_summary && (
            <section aria-label="本文提要" className="mt-6 max-w-3xl border-l-4 border-amber-700/70 pl-4 sm:pl-5">
              <p className="text-xs font-bold tracking-[0.16em] text-amber-900">本文提要</p>
              <p className="mt-2 text-[1rem] leading-[1.9] text-stone-800 sm:text-[1.05rem]">{article.reader_summary}</p>
            </section>
          )}
        </div>
      </header>

      {showBar && (
        <div
          ref={barRef}
          className={`sticky z-40 border-b border-stone-200 bg-[#f7f4ee]/95 shadow-sm backdrop-blur ${hasAudio ? "" : "lg:hidden"}`}
          style={{ top: "var(--site-header, 0px)" }}
        >
          <div className="relative mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-2.5 sm:px-8">
            {hasAudio ? (
              <div className="flex rounded-full border border-stone-300 bg-white p-1" role="tablist" aria-label="文章閱讀方式">
                <button type="button" role="tab" aria-selected={mode === "read"} onClick={() => scrollToView("read", activeSection)} className={`inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-sm font-semibold transition sm:px-5 ${mode === "read" ? "bg-stone-900 text-white" : "text-stone-600 hover:text-stone-950"}`}>
                  <BookOpen className="h-4 w-4" />閱讀文章
                </button>
                <button type="button" role="tab" aria-selected={mode === "listen"} onClick={() => scrollToView("listen", activeSection)} className={`inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-sm font-semibold transition sm:px-5 ${mode === "listen" ? "bg-stone-900 text-white" : "text-stone-600 hover:text-stone-950"}`}>
                  <Headphones className="h-4 w-4" />聆聽原聲
                </button>
              </div>
            ) : (
              <p className="min-w-0 truncate text-sm font-semibold text-stone-700">{sectionHeadings[activeSection]?.label}</p>
            )}
            {hasAudio && (
              <span className="hidden text-sm text-stone-500 lg:inline">{mode === "read" ? "完整文章" : `${article.audio_section_count} 個原聲段落 · ${article.player_count} 段錄音/錄像`}</span>
            )}
            {sectionHeadings.length > 0 && (
              <button
                type="button"
                onClick={() => setTocOpen((open) => !open)}
                aria-expanded={tocOpen}
                aria-controls="article-toc-mobile"
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-stone-300 bg-white px-3 py-1.5 text-sm font-semibold text-stone-700 lg:hidden"
              >
                <ListOrdered className="h-4 w-4" />目錄
                <ChevronDown className={`h-4 w-4 transition-transform ${tocOpen ? "rotate-180" : ""}`} />
              </button>
            )}
            {tocOpen && (
              <nav id="article-toc-mobile" aria-label="本文目錄" className="absolute inset-x-3 top-full mt-2 max-h-[60vh] overflow-y-auto rounded-2xl border border-stone-200 bg-white p-3 shadow-xl lg:hidden">
                <p className="px-3 pb-2 text-xs font-bold tracking-[0.16em] text-stone-500">{sectionCountLabel(sectionHeadings.length)}</p>
                <ol className="space-y-0.5">{tocItems}</ol>
              </nav>
            )}
          </div>
        </div>
      )}

      <div className="mx-auto max-w-6xl px-3 py-6 sm:px-8 sm:py-10 lg:grid lg:grid-cols-[minmax(0,1fr)_13.5rem] lg:gap-10 lg:py-12">
        {sectionHeadings.length > 0 && (
          <aside className="hidden lg:col-start-2 lg:row-start-1 lg:block">
            <nav aria-label="本文目錄" className="sticky" style={{ top: "var(--reader-offset)" }}>
              <p className="px-3 text-xs font-bold tracking-[0.16em] text-stone-500">{sectionCountLabel(sectionHeadings.length)}</p>
              <ol className="mt-3 space-y-0.5 border-l border-stone-200 pl-2">{tocItems}</ol>
            </nav>
          </aside>
        )}
        <div className="min-w-0 lg:col-start-1 lg:row-start-1">
        <section role="tabpanel" aria-label="閱讀文章" aria-hidden={mode !== "read"} className={mode === "read" ? "block" : "hidden"}>
          <article className="mx-auto max-w-3xl rounded-2xl bg-[#fffdf9] px-5 py-7 shadow-[0_18px_60px_rgba(70,55,35,0.08)] sm:rounded-[2rem] sm:px-10 sm:py-12 lg:px-14">
            {sourceAnnotations.size > 0 && (
              <div className="not-prose mb-9 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3.5">
                <div className="flex items-center gap-3">
                  <span className="rounded-xl bg-amber-100 p-2 text-amber-900">
                    <BookOpenText className="h-4 w-4" />
                  </span>
                  <div>
                    <p className="text-sm font-bold text-stone-900">显示原文来源</p>
                    <p className="mt-0.5 text-xs text-stone-500">逐字稿、时间点 Audio 与母本片段</p>
                  </div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={showSources}
                  aria-label="显示段落原文来源"
                  onClick={() => setShowSources((current) => !current)}
                  className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-700 focus-visible:ring-offset-2 ${
                    showSources ? "bg-amber-800" : "bg-stone-300"
                  }`}
                >
                  <span
                    aria-hidden="true"
                    className={`h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
                      showSources ? "translate-x-6" : "translate-x-1"
                    }`}
                  />
                </button>
              </div>
            )}
            <div className="prose prose-stone max-w-none prose-headings:font-serif prose-h2:mt-14 prose-h2:scroll-mt-[var(--reader-offset)] prose-h2:border-b prose-h2:border-stone-200 prose-h2:pb-3 prose-h2:text-[1.45rem] sm:prose-h2:text-2xl prose-h3:mt-14 prose-h3:scroll-mt-[var(--reader-offset)] prose-h3:text-xl sm:prose-h3:text-2xl prose-p:text-[1.05rem] prose-p:leading-[1.9] prose-blockquote:border-amber-700 prose-blockquote:bg-amber-50/70 prose-blockquote:px-5 prose-blockquote:py-2 prose-blockquote:not-italic prose-blockquote:font-normal [&_blockquote_p:first-of-type]:before:content-none [&_blockquote_p:last-of-type]:after:content-none prose-li:leading-8 sm:prose-p:text-[1.1rem]">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  p: ({ children }) => {
                    const href = nodeHref(children);
                    const match = href?.match(/^#review-source-evidence-(p\d+)$/);
                    if (!match) return <p>{children}</p>;
                    const sources = sourceAnnotations.get(match[1]);
                    if (!sources || !showSources) return null;
                    return <SourceEvidenceDisclosure sources={sources} />;
                  },
                  h2: ({ children, node }) => {
                    const title = nodeText(children).trim();
                    const index = sectionIndexForHeading(2, title, node?.position?.start.line);
                    return <h2 id={index >= 0 ? sectionId(index) : undefined}>{children}</h2>;
                  },
                  h3: ({ children, node }) => {
                    const title = nodeText(children).trim();
                    const index = sectionIndexForHeading(3, title, node?.position?.start.line);
                    const count = clipCounts.get(title) ?? 0;
                    return (
                      <div id={index >= 0 ? sectionId(index) : undefined} className="group scroll-mt-[var(--reader-offset)]">
                        <h3>{children}</h3>
                        {hasAudio && index >= 0 && count > 0 && (
                          <button type="button" onClick={() => scrollToView("listen", index)} className="not-prose mt-3 inline-flex items-center gap-2 rounded-full bg-amber-100 px-4 py-2 text-sm font-semibold text-amber-950 hover:bg-amber-200">
                            <Headphones className="h-4 w-4" />聽本節原聲 · {count} 段
                          </button>
                        )}
                      </div>
                    );
                  },
                }}
              >{articleProse}</ReactMarkdown>
            </div>
            {footnotes.length > 0 && (
              <aside aria-label="文章注釋" className="mt-14 border-t border-stone-200 pt-7">
                <p className="text-xs font-bold tracking-[0.14em] text-stone-500">注釋</p>
                <ol className="mt-4 space-y-4 text-sm leading-7 text-stone-600">
                  {footnotes.map((footnote) => (
                    <li key={footnote.id} id={`article-footnote-${footnote.id}`} className="flex scroll-mt-[var(--reader-offset)] items-start gap-2">
                      <span className="font-bold text-stone-900">{footnote.id}.</span>
                      <div className="min-w-0 flex-1">
                        <div className="prose prose-stone max-w-none text-sm leading-7 prose-p:m-0">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{footnote.markdown}</ReactMarkdown>
                        </div>
                        {showSources && footnote.sourceAnnotationId && sourceAnnotations.has(footnote.sourceAnnotationId) ? (
                          <SourceEvidenceDisclosure sources={sourceAnnotations.get(footnote.sourceAnnotationId)!} />
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ol>
              </aside>
            )}
            {articleSources.length > 0 && (
              <aside aria-label="參考來源" className="mt-14 border-t border-stone-200 pt-7">
                <p className="text-xs font-bold tracking-[0.14em] text-stone-500">參考來源</p>
                <p className="mt-2 text-sm leading-7 text-stone-500">
                  本文共引用 {[
                    articleSources.some((source) => source.kind === "sermon") ? `${articleSources.filter((source) => source.kind === "sermon").length} 篇講道` : null,
                    articleSources.some((source) => source.kind === "notes") ? `${articleSources.filter((source) => source.kind === "notes").length} 章筆記` : null,
                  ].filter(Boolean).join("、")}；括號內是本文引用它的段數。
                </p>
                {(["sermon", "notes"] as const).map((kind) => {
                  const items = articleSources.filter((source) => source.kind === kind);
                  if (items.length === 0) return null;
                  return (
                    <div key={kind} className="mt-5">
                      <p className="text-sm font-bold text-stone-800">{kind === "sermon" ? "講道" : "筆記"}</p>
                      <ul className="mt-2 space-y-2 text-sm leading-7 text-stone-700">
                        {items.map((source) => (
                          <li key={`${source.kind}-${source.title}`}>
                            {source.url ? (
                              <Link href={source.url} className="font-semibold text-amber-900 underline-offset-4 hover:underline">{source.title}</Link>
                            ) : (
                              <span className="font-semibold">{source.title}</span>
                            )}
                            <span className="ml-2 text-stone-500">（{source.paragraphs} 段）</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </aside>
            )}
          </article>
        </section>

        {hasAudio && (
        <section role="tabpanel" aria-label="聆聽原聲" aria-hidden={mode !== "listen"} className={mode === "listen" ? "block" : "hidden"}>
          <div className="mx-auto max-w-4xl space-y-14">
            {headingGroups.map((group, sectionIndex) => (
              <section key={group.heading} id={listenId(sectionIndex)} className="scroll-mt-[var(--reader-offset)]" onFocus={() => setActiveSection(sectionIndex)}>
                <div className="flex flex-wrap items-end justify-between gap-4 border-b border-stone-300 pb-5">
                  <div>
                    <p className="text-sm font-bold text-amber-900">第 {sectionIndex + 1} 節</p>
                    <h2 className="mt-2 font-serif text-3xl font-bold leading-tight text-stone-950">{group.heading}</h2>
                  </div>
                  <button type="button" onClick={() => scrollToView("read", sectionIndex)} className="inline-flex items-center gap-2 rounded-full border border-stone-300 bg-white px-4 py-2 text-sm font-semibold text-stone-700 hover:border-stone-500">
                    <BookOpen className="h-4 w-4" />回到本節正文
                  </button>
                </div>
                <div className="mt-7 space-y-9">
                  {group.blocks.map((block, blockIndex) => (
                    <section key={`${block.title}-${blockIndex}`}>
                      <div className="mb-4">
                        <h3 className="text-lg font-bold text-stone-900">{block.title}</h3>
                        {block.passage && <p className="mt-1 text-sm text-stone-500">{block.passage}</p>}
                      </div>
                      <div className="space-y-4">
                        {block.clips.map((clip, clipIndex) => {
                          const key = `${sectionIndex}-${blockIndex}-${clipIndex}`;
                          return <OriginalClipPlayer key={key} clip={clip} playerKey={key} heading={group.heading} onRegister={registerPlayer} onPlay={handlePlay} onPause={handlePause} />;
                        })}
                      </div>
                    </section>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </section>
        )}
        </div>
      </div>

      {activePlayer && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-stone-300 bg-stone-950 text-white shadow-2xl" aria-live="polite">
          <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3 sm:px-8">
            <button type="button" onClick={toggleCurrentPlayer} className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-amber-300 text-stone-950" aria-label={playing ? "暫停原聲" : "繼續播放原聲"}>
              {playing ? <Pause className="h-5 w-5" /> : <Play className="ml-0.5 h-5 w-5" />}
            </button>
            <div className="min-w-0 flex-1">
              <p className="text-xs text-stone-400">正在聆聽</p>
              <p className="truncate text-sm font-semibold">{activePlayer.label}</p>
            </div>
            <button type="button" onClick={() => {
              const index = headingGroups.findIndex((group) => group.heading === activePlayer.heading);
              scrollToView("listen", Math.max(0, index));
            }} className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-amber-200 hover:text-amber-100">
              <RotateCcw className="h-4 w-4" /><span className="hidden sm:inline">返回原聲位置</span>
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
