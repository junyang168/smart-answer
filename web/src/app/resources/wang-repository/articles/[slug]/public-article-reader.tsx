"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { BookOpen, BookOpenText, CalendarDays, Headphones, Pause, Play, RotateCcw } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { PublicArticleClip, PublicAudioSection, PublicWangArticle } from "../article-types";
import { SourceEvidenceDisclosure } from "@/app/admin/wang/operations/articles/reviews/SourceEvidenceDisclosure";
import type { ReviewSourceAnnotation } from "@/app/admin/wang/operations/articles/reviews/types";

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
  const players = useRef(new Map<string, HTMLMediaElement>());

  const hasAudio = article.audio_sections.length > 0;
  const articleBody = article.markdown.replace(/^#\s+.+(?:\r?\n)+/, "");
  const { body: articleProse, footnotes } = useMemo(() => prepareFootnotes(articleBody), [articleBody]);
  const sourceAnnotations = useMemo(
    () => new Map((article.source_annotations ?? []).map((item: ReviewSourceAnnotation) => [item.annotation_id, item.sources])),
    [article.source_annotations],
  );

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
    if (hasAudio) return headingGroups.map((group) => group.heading);
    const h2 = [...articleProse.matchAll(/^##\s+(.+)$/gm)].map((match) => match[1].trim());
    if (h2.length >= 2) return h2;
    return [...articleProse.matchAll(/^###\s+(.+)$/gm)].map((match) => match[1].trim());
  }, [hasAudio, headingGroups, articleProse]);

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

  return (
    <main className="min-h-screen bg-[#f7f4ee] pb-28 text-stone-900">
      <header className="border-b border-stone-200/80 bg-[#eee7da]">
        <div className="mx-auto max-w-6xl px-5 py-12 sm:px-8 sm:py-16 lg:py-20">
          <Link href="/resources/wang-repository" className="text-sm font-semibold text-amber-900 underline-offset-4 hover:underline">王守仁教授聖經講論文庫</Link>
          <p className="mt-8 text-sm font-bold tracking-[0.18em] text-amber-900">馬太福音 · {article.passage.replace(/^太/, "")}</p>
          <h1 className="mt-4 max-w-4xl font-serif text-4xl font-bold leading-[1.2] text-stone-950 sm:text-5xl lg:text-6xl">{article.title}</h1>
          <p className="mt-6 max-w-2xl text-base leading-8 text-stone-700 sm:text-lg">
            沿著經文安靜閱讀，也可以隨時翻到王教授的原聲講解；兩個視角會保留在同一個閱讀位置。
          </p>
        </div>
      </header>

      <div className="sticky top-[139px] z-40 border-b border-stone-200 bg-[#f7f4ee]/95 shadow-sm backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3 sm:px-8">
          <div className="flex rounded-full border border-stone-300 bg-white p-1" role="tablist" aria-label="文章閱讀方式">
            <button type="button" role="tab" aria-selected={mode === "read"} onClick={() => scrollToView("read", activeSection)} className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition sm:px-5 ${mode === "read" ? "bg-stone-900 text-white" : "text-stone-600 hover:text-stone-950"}`}>
              <BookOpen className="h-4 w-4" />閱讀文章
            </button>
            {hasAudio && (
              <button type="button" role="tab" aria-selected={mode === "listen"} onClick={() => scrollToView("listen", activeSection)} className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition sm:px-5 ${mode === "listen" ? "bg-stone-900 text-white" : "text-stone-600 hover:text-stone-950"}`}>
                <Headphones className="h-4 w-4" />聆聽原聲
              </button>
            )}
          </div>
          <span className="hidden text-sm text-stone-500 sm:inline">{mode === "read" ? "完整文章" : `${article.audio_section_count} 個原聲段落 · ${article.player_count} 段錄音/錄像`}</span>
        </div>
      </div>

      <div className="mx-auto max-w-6xl px-5 py-10 sm:px-8 lg:py-14">
        <nav aria-label="本文目錄" className="mb-12 rounded-2xl border border-stone-200 bg-white/70 p-5 sm:p-6">
          <p className="text-xs font-bold tracking-[0.16em] text-stone-500">{sectionCountLabel(sectionHeadings.length)}</p>
          <ol className={`mt-4 grid gap-2 sm:grid-cols-2 ${sectionHeadings.length >= 5 ? "lg:grid-cols-4" : "lg:grid-cols-3"}`}>
            {sectionHeadings.map((heading, index) => (
              <li key={heading}>
                <button type="button" onClick={() => scrollToView(mode, index)} className={`h-full w-full rounded-xl px-3 py-3 text-left text-sm leading-6 transition ${activeSection === index ? "bg-amber-100 font-semibold text-amber-950" : "hover:bg-stone-100"}`}>
                  <span className="mr-2 text-stone-400">{index + 1}</span>{heading}
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <section role="tabpanel" aria-label="閱讀文章" aria-hidden={mode !== "read"} className={mode === "read" ? "block" : "hidden"}>
          <article className="mx-auto max-w-3xl rounded-[2rem] bg-[#fffdf9] px-6 py-9 shadow-[0_18px_60px_rgba(70,55,35,0.08)] sm:px-10 sm:py-12 lg:px-14">
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
            <div className="prose prose-stone max-w-none prose-headings:font-serif prose-h2:mt-16 prose-h2:scroll-mt-[220px] prose-h2:border-b prose-h2:border-stone-200 prose-h2:pb-3 prose-h2:text-2xl prose-h3:mt-14 prose-h3:scroll-mt-[220px] prose-h3:text-2xl prose-p:text-[1.05rem] prose-p:leading-8 prose-blockquote:border-amber-700 prose-blockquote:bg-amber-50/70 prose-blockquote:px-5 prose-blockquote:py-2 prose-blockquote:not-italic prose-li:leading-8 sm:prose-p:text-[1.1rem]">
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
                  h2: ({ children }) => {
                    const title = nodeText(children).trim();
                    const index = sectionHeadings.indexOf(title);
                    return <h2 id={index >= 0 ? sectionId(index) : undefined}>{children}</h2>;
                  },
                  h3: ({ children }) => {
                    const title = nodeText(children).trim();
                    const index = sectionHeadings.indexOf(title);
                    const count = clipCounts.get(title) ?? 0;
                    return (
                      <div id={index >= 0 ? sectionId(index) : undefined} className="group scroll-mt-[220px]">
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
                    <li key={footnote.id} id={`article-footnote-${footnote.id}`} className="flex scroll-mt-[220px] items-start gap-2">
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
          </article>
        </section>

        {hasAudio && (
        <section role="tabpanel" aria-label="聆聽原聲" aria-hidden={mode !== "listen"} className={mode === "listen" ? "block" : "hidden"}>
          <div className="mx-auto max-w-4xl space-y-14">
            {headingGroups.map((group, sectionIndex) => (
              <section key={group.heading} id={listenId(sectionIndex)} className="scroll-mt-[220px]" onFocus={() => setActiveSection(sectionIndex)}>
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
