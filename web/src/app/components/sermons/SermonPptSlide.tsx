"use client";

import { useEffect, useMemo, useState } from "react";

type Passages = { zh?: string };

export type SlideProvenance = {
  claim_ids: string[];
  evidence_step_ids: string[];
  source_fragment_ids: string[];
};

export type SlideLanguageNote = {
  claim_id: string;
  evidence_step_id: string;
  source_fragment_id: string;
  at: number;
  text: string;
};

export type OriginalLanguageEvent = {
  at: number;
  context: string;
  original: string;
  greek: string;
  transcript_excerpt: string;
};

export type SermonSlide = {
  at: number;
  seconds: number;
  kind: "cover" | "claim";
  title: string;
  scripture: string;
  scripture_label: string;
  provenance?: SlideProvenance;
  language_notes: SlideLanguageNote[];
};

export type SermonSlideDeck = {
  schema_version: "wang_sermon_slide_deck_v1";
  source_id: string;
  source_sha256: string;
  transcript_id: string;
  title: string;
  media_duration: number;
  slides: SermonSlide[];
  original_language_events: OriginalLanguageEvent[];
};

const scriptureCache = new Map<string, Promise<{ reference: string; passages: Passages }>>();

function loadScripture(slug: string) {
  const cached = scriptureCache.get(slug);
  if (cached) return cached;
  const pending = fetch(`/api/scripture/basic/${slug}`)
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => ({
      reference: String(data?.reference ?? ""),
      passages: (data?.passages ?? {}) as Passages,
    }));
  scriptureCache.set(slug, pending);
  return pending;
}

function plain(html: string | undefined) {
  if (!html) return "";
  return html
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<[^>]*>/g, "")
    .replace(/\[(\d+)\]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

export function slideIndexAt(deck: SermonSlideDeck, seconds: number) {
  let index = 0;
  for (let cursor = 0; cursor < deck.slides.length; cursor += 1) {
    if (deck.slides[cursor].at > seconds) break;
    index = cursor;
  }
  return index;
}

export function SermonPptSlide({
  deck,
  seconds,
  onSeek,
  coverTitle,
}: {
  deck: SermonSlideDeck;
  seconds: number;
  onSeek: (seconds: number) => void;
  coverTitle?: string;
}) {
  const index = slideIndexAt(deck, seconds);
  const slide = deck.slides[index];
  const nextAt = deck.slides[index + 1]?.at ?? deck.media_duration;
  const [scripture, setScripture] = useState<{ reference: string; passages: Passages } | null>(null);

  useEffect(() => {
    if (!slide?.scripture) {
      setScripture(null);
      return;
    }
    let active = true;
    loadScripture(slide.scripture).then((data) => {
      if (active) setScripture(data);
    });
    return () => {
      active = false;
    };
  }, [slide?.scripture]);

  const heardTerms = useMemo(
    () =>
      deck.original_language_events.filter(
        (event) => event.at >= slide.at && event.at <= seconds && event.at < nextAt,
      ),
    [deck.original_language_events, nextAt, seconds, slide.at],
  );
  const heardNotes = slide.language_notes.filter(
    (note) => note.at >= slide.at && note.at <= seconds && note.at < nextAt,
  );

  if (!slide) return null;
  const verse = plain(scripture?.passages.zh);
  const label = slide.scripture_label || scripture?.reference || "";

  return (
    <section className="flex min-h-[22rem] flex-col rounded-xl bg-slate-950 px-5 py-5 text-slate-100 shadow-inner sm:aspect-video sm:min-h-0 sm:px-8 sm:py-7">
      <div className="flex items-center justify-between gap-4 border-b border-slate-800 pb-3">
        <p className="text-[0.68rem] font-bold uppercase tracking-[0.16em] text-amber-400">
          {slide.kind === "cover" ? "本講" : "講論要點 · 根據教授講論整理"}
        </p>
        <p className="font-mono text-[0.68rem] text-slate-500">
          {clock(slide.at)} · {index + 1}/{deck.slides.length}
        </p>
      </div>

      <div className="flex flex-1 flex-col justify-center gap-5 py-5">
        <h2 className="font-serif text-2xl font-semibold leading-snug text-white sm:text-3xl">
          {slide.kind === "cover" && coverTitle ? coverTitle : slide.title}
        </h2>

        {label && (
          <div>
            <p className="mb-2 text-sm font-semibold text-amber-300">{label}</p>
            {verse && (
              <p className="font-serif text-base leading-8 text-slate-300 sm:text-lg">{verse}</p>
            )}
          </div>
        )}

        {(heardNotes.length > 0 || heardTerms.length > 0) && (
          <div className="grid gap-3 border-t border-amber-300/20 pt-4 sm:grid-cols-2">
            {heardNotes.map((note) => (
              <blockquote
                key={note.source_fragment_id}
                className="rounded-lg bg-amber-300/[0.07] px-4 py-3 text-sm leading-relaxed text-amber-50"
              >
                <span className="mb-1 block text-[0.65rem] font-bold tracking-wider text-amber-400">
                  原文講解 · 教授原話
                </span>
                {note.text}
              </blockquote>
            ))}
            {heardTerms.map((event) => (
              <div
                key={`${event.at}-${event.greek}-${event.original}`}
                className="rounded-lg bg-slate-900 px-4 py-3"
              >
                <p className="font-serif text-xl text-amber-100">
                  {event.greek || event.original}
                </p>
                {event.greek && event.original !== event.greek && (
                  <p className="mt-1 font-mono text-xs text-slate-400">{event.original}</p>
                )}
                <details className="mt-2 text-xs leading-relaxed text-slate-400">
                  <summary className="cursor-pointer text-slate-500">查看逐字稿原話</summary>
                  <p className="mt-2">{event.transcript_excerpt}</p>
                </details>
              </div>
            ))}
          </div>
        )}
      </div>

      <nav className="flex items-center justify-between border-t border-slate-800 pt-3 text-xs">
        <button
          type="button"
          disabled={index === 0}
          onClick={() => onSeek(deck.slides[index - 1].at)}
          className="rounded px-2 py-1 text-slate-400 hover:bg-slate-800 hover:text-white disabled:opacity-30"
        >
          ← 上一張
        </button>
        <span className="text-slate-600">PPT 隨音頻自動切換</span>
        <button
          type="button"
          disabled={index + 1 >= deck.slides.length}
          onClick={() => onSeek(deck.slides[index + 1].at)}
          className="rounded px-2 py-1 text-slate-400 hover:bg-slate-800 hover:text-white disabled:opacity-30"
        >
          下一張 →
        </button>
      </nav>
    </section>
  );
}
