"use client";

import { useEffect, useRef, useState } from "react";
import { load, plain } from "./ScriptureSlide";

/**
 * 按章页面的投影片：模型读保留的逐字稿写的要点，和教授此刻为止读到的经文。
 *
 * 负责人 2026-11-03：投影片要像查经 PPT，呈现他讲的内容，而不是 Claim 的论证结
 * 构；**他讲到、读到的经文必须显示在投影片上。** 要点是编辑整理，不冒充原话；
 * 经文是和合本全文，按出处去取，不经模型。
 */

// Let listeners read the next point before the professor starts expanding it.
const POINT_PREVIEW_SECONDS = 1;

export function shouldShowSlideItem(at: number, seconds: number, clipEnd: number) {
  // A chapter can jump over the next sentence even when it starts at the clip boundary.
  return at < clipEnd && at <= seconds + POINT_PREVIEW_SECONDS;
}

type InlineNote = {
  at: number;
  scripture: string;
  verse: number;
  span: string;
  original: string;
  reading: string;
  explanation: string;
};

function AnnotatedVerse({ text, notes }: { text: string; notes: InlineNote[] }) {
  const matched = notes.filter(({ span }) => span && text.indexOf(span) >= 0 && text.indexOf(span) === text.lastIndexOf(span));
  const spans = [...new Set(matched.map(({ span }) => span))]
    .map((span) => ({ span, start: text.indexOf(span) }))
    .sort((a, b) => a.start - b.start || b.span.length - a.span.length);
  let cursor = 0;
  const parts = spans.map(({ span, start }) => {
    if (start < cursor) return null;
    const before = text.slice(cursor, start);
    cursor = start + span.length;
    return <span key={span}>{before}<mark className="rounded bg-amber-300/25 text-amber-100">{span}</mark></span>;
  });
  return (
    <div>
      {parts}{text.slice(cursor)}
      {matched.filter(({ original, explanation }) => original || explanation).map((note, index) => (
        <div key={index} className="text-xs leading-4 text-amber-100/70">
          {[note.original, note.explanation].filter(Boolean).join(" · ")}
        </div>
      ))}
    </div>
  );
}

type SlideDiagram = {
  at: number;
  title: string;
  origin: string;
  kind: "professor" | "editorial";
  sha256: string;
  image_src: string;
};

export type ChapterSlideData = {
  at: number;
  seconds: number;
  title: string;
  /** at 是他开始讲这条要点的时间。 */
  points: { at: number; text: string }[];
  /** 本堂地圖：預告，要點一起出現，不逐條亮。 */
  overview?: boolean;
  verses: string[];
  inline_notes?: InlineNote[];
  diagrams?: SlideDiagram[];
  scriptures: { at: number; scripture: string; label: string }[];
};

/** `mat-5-4-9` → 逐节的出处 `mat-5-4` … `mat-5-9`；读不出的整段当一节。 */
function eachVerse(slug: string): { number: string; slug: string }[] {
  const match = slug.match(/^([a-z0-9]+)-(\d+)-(\d+)(?:-(\d+))?$/);
  if (!match) return [{ number: "", slug }];
  const [, book, chapter, from, to] = match;
  const first = Number(from);
  const last = to ? Number(to) : first;
  if (last < first || last - first > 30) return [{ number: "", slug }];
  return Array.from({ length: last - first + 1 }, (_, index) => ({
    number: String(first + index),
    slug: `${book}-${chapter}-${first + index}`,
  }));
}

/** 只留汉字，好拿逐字稿的一句和经文比。 */
function hanzi(text: string): string {
  return (text.match(/[\u3400-\u9fff]/g) ?? []).join("");
}

/**
 * 他此刻念的是哪一节：逐字稿这一句的相邻两字，有一半以上出现在某节经文里，就是那一节。
 * 他在讲解、没在念经文时对不上，返回 -1。
 */
function verseBeingRead(sentence: string, texts: string[]): number {
  const said = hanzi(sentence);
  if (said.length < 6) return -1;
  const pairs = Array.from({ length: said.length - 1 }, (_, i) => said.slice(i, i + 2));
  let best = -1;
  let bestShare = 0.5;
  texts.forEach((text, index) => {
    const verse = hanzi(text);
    const share = pairs.filter((pair) => verse.includes(pair)).length / pairs.length;
    if (share > bestShare) {
      best = index;
      bestShare = share;
    }
  });
  return best;
}

/** 手动滑过投影片后，这么久之内不自动滚，免得和手指抢。与逐字稿一致。 */
const HANDS_OFF_MS = 6000;

/**
 * 把一个元素转到投影片框里看得见的地方。只滚投影片自己的框（`data-slide-scroll`），
 * 不动整页；不在聆听模式里（没有这个框）就什么都不做。
 */
const wanted = new WeakMap<HTMLElement, HTMLElement>();

function bringIntoView(element: HTMLElement | null, { yieldToReading = false } = {}) {
  const frame = element?.closest<HTMLElement>("[data-slide-scroll]");
  if (!element || !frame) return;
  // 他正在念的經節優先：念到太5:10 時新出的要點不把那一節捲走（負責人 2026-11-16）。
  if (yieldToReading && frame.dataset.reading) return;
  // 手指剛碰過：先記下這時該看的，等手放開後再轉過去——不然他這期間出的要點
  // 會一直留在框外（負責人 2026-11-16，iPhone 上 inclusio 那條被捲出視野）。
  wanted.set(frame, element);
  const wait = HANDS_OFF_MS - (Date.now() - Number(frame.dataset.handsOff ?? 0));
  if (wait > 0) {
    window.setTimeout(() => {
      if (wanted.get(frame) === element) bringIntoView(element, { yieldToReading });
    }, wait + 50);
    return;
  }
  if (!element.isConnected) return;
  const top = element.getBoundingClientRect().top - frame.getBoundingClientRect().top + frame.scrollTop;
  frame.scrollTo({ top: Math.max(0, top - frame.clientHeight / 4), behavior: "smooth" });
}

/** 一节一行、带节号——他一节一节读，听众要找得到他读到哪里。 */
function Verse({
  slug,
  label,
  latest,
  sentence,
  notes,
}: {
  slug: string;
  label: string;
  latest: boolean;
  notes: InlineNote[];
  /** 逐字稿此刻这一句；只给最新的一段，用来跟着他念到的那一节走。 */
  sentence?: string;
}) {
  const verses = eachVerse(slug);
  const [texts, setTexts] = useState<string[] | null>(null);
  useEffect(() => {
    let alive = true;
    Promise.all(verses.map((verse) => load(verse.slug).then((data) => plain(data.passages.zh)))).then((loaded) => {
      if (alive) setTexts(loaded);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);
  // 手机上一段经文常比投影片框高：他念到哪一节，就亮那一节、滚到那一节（负责人 2026-11-09）。
  const reading = latest && texts && sentence ? verseBeingRead(sentence, texts) : -1;
  const readingLine = useRef<HTMLLIElement | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const frame = box.current?.closest<HTMLElement>("[data-slide-scroll]");
    if (!frame || sentence === undefined) return;
    frame.dataset.reading = reading >= 0 ? "1" : "";
    if (reading >= 0) bringIntoView(readingLine.current);
  }, [reading, slug]);
  useEffect(() => {
    const frame = box.current?.closest<HTMLElement>("[data-slide-scroll]");
    return () => {
      if (frame && sentence !== undefined) frame.dataset.reading = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div ref={box} className={`rounded-lg px-4 py-3 ${latest ? "bg-slate-900 ring-1 ring-amber-300/30" : "bg-slate-900/40"}`}>
      <p className="mb-1.5 font-mono text-[0.7rem] text-amber-300/80">{label}</p>
      <ol className={`space-y-1 font-serif ${latest ? "text-[0.98rem] leading-7 text-slate-200 lg:text-xl lg:leading-9" : "text-[0.9rem] leading-6 text-slate-500 lg:text-base lg:leading-7"}`}>
        {verses.map((verse, index) => (
          <li
            key={verse.slug}
            ref={index === reading ? readingLine : undefined}
            className={`flex gap-2.5 ${index === reading ? "-mx-1.5 rounded bg-amber-300/10 px-1.5 text-white" : ""}`}
          >
            {verse.number && (
              <span className="w-5 shrink-0 pt-[0.15rem] text-right font-mono text-[0.7rem] text-amber-300/60">
                {verse.number}
              </span>
            )}
            {texts ? (
              <AnnotatedVerse text={texts[index]} notes={notes.filter((note) => note.verse === Number(verse.number))} />
            ) : <span>……</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}

function Diagram({ diagram }: { diagram: SlideDiagram }) {
  const zoom = useRef<HTMLDialogElement | null>(null);
  const credit = diagram.kind === "editorial" ? "整理" : "教授的圖";
  if (!diagram.image_src.startsWith("data:image/svg+xml;base64,")) return null;
  return (
    <figure className="space-y-2">
      <button type="button" onClick={() => zoom.current?.showModal()} aria-label={`放大${diagram.title}`}
        className="block w-full overflow-hidden rounded-lg bg-white p-2">
        {/* eslint-disable-next-line @next/next/no-img-element -- Exact, verified SVG shown in image mode. */}
        <img src={diagram.image_src} alt={diagram.title} className="mx-auto block h-auto max-h-[45dvh] max-w-full object-contain" />
      </button>
      <figcaption className="text-sm leading-relaxed text-slate-300">
        <strong className="text-amber-200">{credit}</strong> · {diagram.title}
        <span className="block">{diagram.origin}</span>
      </figcaption>
      <dialog ref={zoom} aria-label={diagram.title} className="m-auto w-[min(94vw,56rem)] max-w-none rounded-xl bg-white p-4 text-stone-900 backdrop:bg-black/75">
        <form method="dialog" className="mb-3 flex justify-end">
          <button className="min-h-11 rounded-md border border-stone-300 px-3">關閉</button>
        </form>
        <div className="overflow-auto">
          {/* eslint-disable-next-line @next/next/no-img-element -- Exact, verified SVG shown in image mode. */}
          <img src={diagram.image_src} alt={diagram.title} className="mx-auto block h-auto w-full min-w-[40rem]" />
        </div>
        <p className="mt-2 text-sm">{credit} · {diagram.origin}</p>
      </dialog>
    </figure>
  );
}

export default function ChapterSlide({
  slide,
  seconds,
  clipEnd,
  className = "min-h-[20rem]",
  sentence,
}: {
  slide: ChapterSlideData;
  seconds: number;
  clipEnd: number;
  /** 聆听模式：逐字稿此刻这一句，投影片跟着他念到的经节走。 */
  sentence?: string;
  /** 聆聽模式裡投影片佔滿左邊，不要最小高度。 */
  className?: string;
}) {
  // The speech anchor stays exact while points get a short reading lead.
  const points = slide.points.filter((item) => shouldShowSlideItem(item.at, seconds, clipEnd));
  const activePoint = slide.overview ? undefined : points.filter((item) => item.at <= seconds).at(-1);
  const read = slide.scriptures.filter((item) => item.at <= seconds + 1);
  // 他開始新要點後又念新經文，前面要點的經文就收起，免得看起來屬於現在講的。
  const current = read.filter((item) => !read.some((later) => slide.points.some(
    (point) => item.at < point.at && point.at <= later.at)));
  // 同一段經文再念一次，只留最近的那一次。
  const shown = [...current].reverse().filter((item, index, all) =>
    all.findIndex((other) => other.scripture === item.scripture) === index);
  // 他在讲解、没在念经文时，新出一条要点就把它转进视野。
  const newestPoint = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    if (activePoint) bringIntoView(newestPoint.current, { yieldToReading: true });
  }, [activePoint, slide.at]);
  return (
    <section className={`flex ${className} flex-col gap-5 rounded-xl bg-slate-950 px-5 py-5 text-slate-100 shadow-inner sm:px-7 sm:py-6 lg:gap-8 lg:px-12 lg:py-10`}>
      <header>
        <h2 className="font-serif text-xl font-semibold leading-snug text-white sm:text-2xl lg:text-4xl">{slide.title}</h2>
        {points.length > 0 && (
          <ol className="mt-4 space-y-2.5 text-[1.05rem] leading-relaxed lg:mt-6 lg:space-y-4 lg:text-2xl">
            {points.map((point, index) => (
              <li
                key={point.text}
                ref={point === activePoint ? newestPoint : undefined}
                className={`flex gap-3 ${point === activePoint || slide.overview ? "text-white" : "text-slate-400"}`}
              >
                <span className="w-4 shrink-0 text-right font-mono text-sm leading-[1.75rem] text-amber-400/80 lg:w-6 lg:text-lg lg:leading-[2.25rem]">
                  {index + 1}
                </span>
                <span>{point.text}</span>
              </li>
            ))}
          </ol>
        )}
      </header>
      {(slide.diagrams ?? []).filter((diagram) => diagram.at <= seconds && diagram.at < clipEnd).map((diagram) => (
        <Diagram key={diagram.sha256} diagram={diagram} />
      ))}
      {shown.length > 0 && (
        <div className="flex flex-col gap-3">
          {shown.map((item, index) => (
            <Verse
              key={`${item.at}-${item.scripture}`}
              slug={item.scripture}
              label={item.label}
              latest={index === 0}
              notes={(slide.inline_notes ?? []).filter((note) =>
                note.scripture === item.scripture && note.at < clipEnd && note.at <= seconds)}
              sentence={index === 0 ? sentence : undefined}
            />
          ))}
        </div>
      )}
      <p className="mt-auto text-right text-[0.65rem] tracking-[0.15em] text-slate-600">要點為編輯整理，非原話</p>
    </section>
  );
}
