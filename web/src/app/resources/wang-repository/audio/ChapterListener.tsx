"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import ChapterSlide from "./ChapterSlide";
import ScriptureSlide from "./ScriptureSlide";
import {
  citedAt,
  clock,
  judgementAt,
  mediaSrc,
  originalLanguageAt,
  verseAt,
  visible,
  type Section,
  type Sermon,
  type Stretch,
} from "./OriginalAudio";

/**
 * 聆聽模式：一個畫面裡看投影片、聽錄音、跟著逐字稿。
 *
 * 負責人 2026-11-04：總覽頁適合第一次來的人看這一章講了哪些經文、哪些題目；
 * 認真聽的人要的是投影片、播放控制（從頭、暫停、前後跳、變速）和逐字稿在同一
 * 個畫面，能跳上一段、下一段，也能直接挑一段來聽。
 *
 * 整章是一條播放清單：各段按經節次序，段內各堂課，每堂課裡要播的幾段錄音。換
 * 堂課就換錄音檔，聽的人不必知道。
 */

type Clip = {
  /** 第幾段經文（`sections` 的位置）。 */
  section: number;
  sermon: Sermon;
  stretch: Stretch;
  start: number;
  end: number;
  /** 這段錄音在整章播放清單裡從第幾秒開始。 */
  offset: number;
};

/** 整章的播放清單。灰色片段只在打開開關時進清單。 */
function playlist(sections: Section[], showLeftOut: boolean): Clip[] {
  const out: Clip[] = [];
  let offset = 0;
  sections.forEach((section, index) => {
    for (const sermon of section.sermons) {
      if (!sermon.media_url) continue;
      for (const stretch of visible(sermon, showLeftOut)) {
        out.push({ section: index, sermon, stretch, start: stretch.start, end: stretch.end, offset });
        offset += stretch.end - stretch.start;
      }
    }
  });
  return out;
}

/** 「馬太福音 5:13-16」→「5:13-16」：按鈕上書名是多餘的。 */
const shortLabel = (label: string) => label.split(" ").pop() || label;

const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];
/** ⏮ 在一段開頭幾秒內再按，就回到整章開頭。 */
const RESTART_GRACE = 3;
/** 讀者自己捲逐字稿之後，這麼久不自動捲回目前那一句。 */
const HANDS_OFF_MS = 6000;

export default function ChapterListener({
  passage,
  title,
  source,
  backHref,
  initialSection,
}: {
  passage: string;
  title: string;
  source: string;
  backHref: string;
  initialSection?: string;
}) {
  const [sections, setSections] = useState<Section[] | null>(null);
  const [error, setError] = useState("");
  const [showLeftOut, setShowLeftOut] = useState(false);
  // 教授釋經筆記的錄音是 AI 朗讀母本，不是他的原聲（負責人 2026-11-06），畫面上一直標明。
  const [aiVoice, setAiVoice] = useState(false);
  const [position, setPosition] = useState({ clip: 0, time: 0 });
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const media = useRef<HTMLAudioElement | null>(null);
  // 換錄音檔時，等新檔載入再跳、再播。
  const pending = useRef<{ time: number; play: boolean } | null>(null);

  const clips = useMemo(() => (sections ? playlist(sections, showLeftOut) : []), [sections, showLeftOut]);
  const clip: Clip | undefined = clips[position.clip];

  useEffect(() => {
    // 晚到的回應不算：開發模式下這個 effect 會跑兩次，第二次的回應若在錄音載入
    // 之後才到，會再設一次 pending，而 loadedmetadata 不會再來。
    let alive = true;
    fetch(`/api/public/original-audio/${passage}?source=${encodeURIComponent(source)}`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data) => {
        if (!alive) return;
        const loaded: Section[] = data.sections ?? [];
        setAiVoice(Boolean(data.ai_voice));
        const list = playlist(loaded, false);
        const wanted = Math.max(0, loaded.findIndex((section) => section.passage === initialSection));
        const first = Math.max(0, list.findIndex((item) => item.section === wanted));
        setSections(loaded);
        if (list[first]) {
          setPosition({ clip: first, time: list[first].start });
          pending.current = { time: list[first].start, play: false };
        }
      })
      .catch(() => alive && setError("讀不到這一章的錄音。"));
    return () => {
      alive = false;
    };
    // 只在進來時讀一次；換段由頁面自己處理，不重新讀。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [passage]);

  // ---- 跳轉 ----------------------------------------------------------------

  function seekTo(index: number, time: number, play?: boolean) {
    const target = clips[index];
    const element = media.current;
    if (!target) return;
    const wantPlay = play ?? (element ? !element.paused : false);
    const sameFile = clip?.sermon.media_url === target.sermon.media_url;
    setPosition({ clip: index, time });
    if (element && sameFile && element.readyState > 0) {
      element.currentTime = time;
      if (wantPlay) void element.play();
    } else {
      // 換檔：src 換了之後 loadedmetadata 再跳。
      pending.current = { time, play: wantPlay };
    }
  }

  /** 跳到整章播放清單的第幾秒。 */
  function seekChapter(seconds: number, play?: boolean) {
    if (clips.length === 0) return;
    const total = clips[clips.length - 1].offset + clips[clips.length - 1].end - clips[clips.length - 1].start;
    const x = Math.min(Math.max(0, seconds), Math.max(0, total - 0.5));
    let index = clips.findIndex((item) => x < item.offset + item.end - item.start);
    if (index < 0) index = clips.length - 1;
    seekTo(index, clips[index].start + (x - clips[index].offset), play);
  }

  function firstClipOf(section: number) {
    return clips.findIndex((item) => item.section === section);
  }

  function goSection(section: number) {
    const index = firstClipOf(section);
    if (index >= 0) seekTo(index, clips[index].start, true);
  }

  // ---- 播放器事件 ----------------------------------------------------------

  function onLoaded() {
    const element = media.current;
    if (!element) return;
    element.defaultPlaybackRate = rate;
    element.playbackRate = rate;
    const wanted = pending.current;
    if (!wanted) return;
    pending.current = null;
    element.currentTime = wanted.time;
    if (wanted.play) void element.play();
  }

  function advance() {
    const element = media.current;
    const next = clips[position.clip + 1];
    if (!element || !clip) return;
    if (!next) {
      element.pause();
      return;
    }
    // 同一個檔、緊接著的下一段，不必跳——設一次 currentTime 就會頓一下。
    if (next.sermon.media_url === clip.sermon.media_url && Math.abs(next.start - element.currentTime) <= 1) {
      setPosition({ clip: position.clip + 1, time: element.currentTime });
    } else {
      seekTo(position.clip + 1, next.start, true);
    }
  }

  function onTimeUpdate() {
    const element = media.current;
    if (!element || !clip) return;
    // 該跳而還沒跳的，錄音一能定位就跳，不只等 loadedmetadata：那個事件一個檔
    // 只來一次，錯過了，時間就永遠不更新，逐字稿的高亮停在第一句。
    if (pending.current) {
      if (element.readyState > 0) onLoaded();
      return;
    }
    if (element.currentTime >= clip.end) {
      advance();
      return;
    }
    setPosition((current) => ({ ...current, time: element.currentTime }));
  }

  function toggle() {
    const element = media.current;
    if (!element) return;
    if (element.paused) void element.play();
    else element.pause();
  }

  function changeRate(value: number) {
    setRate(value);
    if (media.current) {
      media.current.defaultPlaybackRate = value;
      media.current.playbackRate = value;
    }
  }

  /** 開關灰色片段：清單變了，按此刻的位置重新對上。 */
  function switchLeftOut(show: boolean) {
    if (!sections || !clip) {
      setShowLeftOut(show);
      return;
    }
    const list = playlist(sections, show);
    const time = position.time;
    let index = list.findIndex(
      (item) => item.sermon === clip.sermon && item.start <= time && time < item.end,
    );
    if (index < 0) {
      // 收起時正落在灰色片段裡：跳到同一行後面第一段保留的，沒有就往後找。
      index = list.findIndex((item) => item.sermon === clip.sermon && item.start > time);
      if (index < 0) index = list.findIndex((item) => item.offset >= clip.offset);
      if (index < 0) index = list.length - 1;
    }
    setShowLeftOut(show);
    const target = list[index];
    if (!target) return;
    const inside = target.start <= time && time < target.end;
    setPosition({ clip: index, time: inside ? time : target.start });
    const element = media.current;
    if (!inside && element) {
      if (target.sermon.media_url === clip.sermon.media_url) element.currentTime = target.start;
      else pending.current = { time: target.start, play: !element.paused };
    }
  }

  // ---- 此刻在哪 ------------------------------------------------------------

  const section = clip?.section ?? 0;
  const sectionClips = clips.filter((item) => item.section === section);
  const sectionStart = sectionClips[0]?.offset ?? 0;
  const sectionLength = sectionClips.reduce((sum, item) => sum + item.end - item.start, 0);
  const chapterTime = clip ? clip.offset + (position.time - clip.start) : 0;
  const sectionTime = Math.max(0, chapterTime - sectionStart);
  const sectionsWithAudio = (sections ?? [])
    .map((item, index) => ({ item, index }))
    .filter(({ index }) => clips.some((c) => c.section === index));
  const previousSection = [...sectionsWithAudio].reverse().find(({ index }) => index < section);
  const nextSection = sectionsWithAudio.find(({ index }) => index > section);

  function restart() {
    if (sectionTime > RESTART_GRACE) seekChapter(sectionStart, true);
    else seekChapter(0, true);
  }

  // ---- 鍵盤 ----------------------------------------------------------------

  const keys = useRef<(event: KeyboardEvent) => void>(() => {});
  keys.current = (event: KeyboardEvent) => {
    const target = event.target as HTMLElement | null;
    if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === " ") {
      event.preventDefault();
      toggle();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      seekChapter(chapterTime - 15);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      seekChapter(chapterTime + 15);
    } else if (event.key === "[" && previousSection) {
      goSection(previousSection.index);
    } else if (event.key === "]" && nextSection) {
      goSection(nextSection.index);
    }
  };
  useEffect(() => {
    const listener = (event: KeyboardEvent) => keys.current(event);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);

  // 網址跟著段走：複製下來的連結打開就是這一段。
  const sectionPassage = sections?.[section]?.passage;
  useEffect(() => {
    if (!sectionPassage) return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("section") === sectionPassage) return;
    url.searchParams.set("section", sectionPassage);
    window.history.replaceState(window.history.state, "", url);
  }, [sectionPassage]);

  // ---- 逐字稿跟著走 --------------------------------------------------------

  const box = useRef<HTMLDivElement | null>(null);
  const currentLine = useRef<HTMLButtonElement | null>(null);
  const handsOff = useRef(0);
  const [following, setFollowing] = useState(true);

  const lines = sectionClips.flatMap((item) => {
    const index = clips.indexOf(item);
    return (item.sermon.transcript ?? [])
      .filter((line) => line.at >= item.start && line.at < item.end)
      .map((line) => ({ ...line, clip: index }));
  });
  let lineIndex = -1;
  if (clip) {
    for (let i = 0; i < lines.length; i += 1) {
      if (lines[i].clip === position.clip && lines[i].at <= position.time + 0.25) lineIndex = i;
      else if (lineIndex >= 0) break;
    }
  }
  const lineKey = lineIndex >= 0 ? `${lines[lineIndex].clip}-${lines[lineIndex].at}` : "";

  function scrollToCurrent(smooth: boolean) {
    const container = box.current;
    const line = currentLine.current;
    if (!container || !line) return;
    container.scrollTo({
      top: line.offsetTop - container.clientHeight / 3,
      behavior: smooth ? "smooth" : "auto",
    });
  }

  useEffect(() => {
    if (Date.now() - handsOff.current < HANDS_OFF_MS) return;
    setFollowing(true);
    scrollToCurrent(true);
  }, [lineKey]);

  function userScrolled() {
    handsOff.current = Date.now();
    setFollowing(false);
  }

  // ---- 畫面 ----------------------------------------------------------------

  const frame = "fixed inset-0 z-[60] flex flex-col bg-stone-100 text-stone-900";
  if (error || !sections) {
    return (
      <div className={frame}>
        <p className={`m-auto ${error ? "text-rose-800" : "text-stone-600"}`} role="status">
          {error || "正在讀取…"}
        </p>
      </div>
    );
  }

  const time = position.time;
  const leftOutPart = clip?.stretch.left_out
    ? clip.stretch.parts?.find((part) => part.start <= time && time < part.end)?.reason ?? clip.stretch.reasons?.[0]
    : null;
  const table = clip?.sermon.tables?.find((item) => item.at <= time && time < item.until);
  const slides = clip?.sermon.slides ?? [];
  const slide = [...slides].reverse().find((item) => item.at <= time) ?? slides[0];
  const hasLeftOut = sections.some((item) => item.sermons.some((sermon) => sermon.stretches.some((s) => s.left_out)));
  const minutes = (seconds: number) => `${Math.round(seconds / 60)} 分`;
  const sectionSeconds = (index: number) =>
    clips.filter((item) => item.section === index).reduce((sum, item) => sum + item.end - item.start, 0);

  const button =
    "inline-flex min-h-11 min-w-11 items-center justify-center rounded-full px-3 text-stone-700 hover:bg-stone-100 disabled:opacity-30";

  return (
    <div className={frame}>
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-stone-200 bg-white px-4 py-2 sm:px-6">
        <Link href={backHref} className="text-sm font-semibold text-amber-800 hover:text-amber-900">
          <span aria-hidden="true">←</span> 本章總覽
        </Link>
        <h1 className="font-serif text-lg font-bold">
          {title}
          <span className="ml-2 font-sans text-sm font-normal text-stone-500">{source}</span>
          {aiVoice && (
            <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 font-sans text-xs font-semibold text-amber-900">
              AI 朗讀
            </span>
          )}
        </h1>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-stone-600">段落</span>
          <select
            value={section}
            onChange={(event) => goSection(Number(event.target.value))}
            className="min-h-11 rounded-lg border border-stone-300 bg-white px-3 font-semibold"
          >
            {sectionsWithAudio.map(({ item, index }) => (
              <option key={item.passage} value={index}>
                {item.label} · {minutes(sectionSeconds(index))}
              </option>
            ))}
          </select>
        </label>
        {hasLeftOut && (
          <label className="flex cursor-pointer items-center gap-2 text-sm text-stone-600 sm:ml-auto">
            <input
              type="checkbox"
              role="switch"
              checked={showLeftOut}
              onChange={(event) => switchLeftOut(event.target.checked)}
              className="h-4 w-4 accent-stone-500"
            />
            {aiVoice ? "顯示生活應用與附錄（灰色）" : "顯示其餘部分（灰色）"}
          </label>
        )}
      </header>

      <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1.15fr)_minmax(0,1fr)] gap-3 p-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-1 lg:gap-4 lg:p-4">
        <div className="min-h-0 overflow-auto rounded-xl">
          {table ? (
            <section className="min-h-full overflow-auto rounded-xl bg-slate-950 px-5 py-5 text-slate-100 shadow-inner sm:px-7 sm:py-6">
              {slide && <h2 className="font-serif text-xl font-semibold leading-snug text-white sm:text-2xl">{slide.title}</h2>}
              <table className="mt-4 w-full border-collapse text-left text-[0.95rem] leading-relaxed">
                <thead>
                  <tr>
                    {table.rows[0].map((cell, index) => (
                      <th key={index} className="border-b border-slate-600 px-3 py-2 font-semibold text-amber-200">
                        {cell}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.slice(1).map((row, index) => (
                    <tr key={index} className="border-b border-slate-800">
                      {row.map((cell, column) => (
                        <td key={column} className="px-3 py-2 align-top">
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ) : leftOutPart ? (
            <div className="flex h-full flex-col justify-center rounded-xl border border-dashed border-stone-300 bg-stone-200/60 px-6 py-6 text-stone-600">
              <p className="text-xs font-bold">{aiVoice ? "預設收起的部分" : "不屬本章釋經 · 編輯整理"}</p>
              <p className="mt-2 leading-7">{leftOutPart}</p>
            </div>
          ) : slide ? (
            <ChapterSlide slide={slide} seconds={time} className="min-h-full" />
          ) : clip ? (
            <ScriptureSlide
              slug={verseAt(clip.sermon, time, sections[section].passage)}
              title={judgementAt(clip.sermon, time)?.judgement ?? ""}
              points={judgementAt(clip.sermon, time)?.points ?? []}
              application={judgementAt(clip.sermon, time)?.application ?? false}
              originalLanguage={originalLanguageAt(clip.sermon, time)}
              cited={citedAt(clip.sermon, time, sections[section].passage)}
            />
          ) : null}
        </div>

        <div className="relative min-h-0">
          <div
            ref={box}
            onWheel={userScrolled}
            onTouchMove={userScrolled}
            className="relative h-full overflow-auto rounded-xl border border-stone-200 bg-white px-4 py-3 sm:px-5"
          >
            {lines.length === 0 && <p className="text-sm text-stone-500">這一段沒有逐字稿。</p>}
            {lines.map((line, index) => {
              const startsClip = index === 0 || lines[index - 1].clip !== line.clip;
              const here = index === lineIndex;
              const owner = clips[line.clip];
              return (
                <div key={`${line.clip}-${line.at}`}>
                  {/* 換一段錄音（他岔開去講了別的、或換一堂課）只畫一條細線：聽的人
                      關心的是經節，不是第幾堂課（負責人 2026-11-04）。 */}
                  {startsClip && index > 0 && <hr className="my-3 border-dashed border-stone-200" />}
                  {startsClip && owner.stretch.left_out && (
                    <p className="mb-1 text-[0.7rem] text-stone-400">{aiVoice ? "預設收起的部分" : "不屬本章釋經"}</p>
                  )}
                  <button
                    ref={here ? currentLine : undefined}
                    type="button"
                    onClick={() => seekTo(line.clip, line.at, true)}
                    className={`block w-full rounded-md px-2 py-1 text-left text-[0.95rem] leading-7 transition-colors ${
                      here
                        ? "bg-amber-100 text-stone-950"
                        : line.left_out
                          ? "text-stone-400 hover:bg-stone-50"
                          : "text-stone-800 hover:bg-stone-50"
                    }`}
                  >
                    {line.text}
                  </button>
                </div>
              );
            })}
          </div>
          {!following && lineIndex >= 0 && (
            <button
              type="button"
              onClick={() => {
                handsOff.current = 0;
                setFollowing(true);
                scrollToCurrent(true);
              }}
              className="absolute bottom-3 right-3 rounded-full bg-stone-900 px-4 py-2 text-sm text-white shadow-lg"
            >
              回到目前這一句
            </button>
          )}
        </div>
      </div>

      <footer className="border-t border-stone-200 bg-white px-4 pb-3 pt-2 sm:px-6">
        <div className="flex items-center gap-3 font-mono text-xs text-stone-500">
          <span className="w-12 text-right">{clock(sectionTime)}</span>
          <div
            role="slider"
            aria-label="本段進度"
            aria-valuemin={0}
            aria-valuemax={Math.round(sectionLength)}
            aria-valuenow={Math.round(sectionTime)}
            tabIndex={0}
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              seekChapter(sectionStart + ((event.clientX - rect.left) / rect.width) * sectionLength);
            }}
            className="relative flex h-6 flex-1 cursor-pointer items-center gap-[3px]"
          >
            {/* 一段錄音一截：換堂課、或中間跳過他岔開去講的地方，看得見斷口。 */}
            {sectionClips.map((item) => {
              const length = item.end - item.start;
              const done = Math.min(1, Math.max(0, (chapterTime - item.offset) / length));
              return (
                <div
                  key={`${item.offset}`}
                  className={`relative h-2 overflow-hidden rounded-full ${item.stretch.left_out ? "bg-stone-200" : "bg-slate-200"}`}
                  style={{ flexGrow: Math.max(1, length), flexBasis: 0 }}
                >
                  <div
                    className={`absolute inset-y-0 left-0 ${item.stretch.left_out ? "bg-stone-400" : "bg-amber-600"}`}
                    style={{ width: `${done * 100}%` }}
                  />
                </div>
              );
            })}
          </div>
          <span className="w-12">{clock(sectionLength)}</span>
        </div>

        <div className="mt-1 flex flex-wrap items-center justify-center gap-1 sm:gap-2">
          <button
            type="button"
            onClick={() => previousSection && goSection(previousSection.index)}
            disabled={!previousSection}
            title="上一段（[）"
            className={`${button} text-sm`}
          >
            ‹ <span className="ml-1 hidden sm:inline">{previousSection ? shortLabel(previousSection.item.label) : "上一段"}</span>
          </button>
          <button
            type="button"
            onClick={restart}
            title="回到這一段開頭；開頭再按一次，回到整章開頭"
            aria-label="從頭"
            className={button}
          >
            ⏮
          </button>
          <button type="button" onClick={() => seekChapter(chapterTime - 15)} title="倒退 15 秒（←）" className={`${button} text-sm`}>
            ↺ 15
          </button>
          <button
            type="button"
            onClick={toggle}
            title="播放／暫停（空白鍵）"
            aria-label={playing ? "暫停" : "播放"}
            className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-stone-900 text-xl text-white hover:bg-stone-700"
          >
            {playing ? "❚❚" : "▶"}
          </button>
          <button type="button" onClick={() => seekChapter(chapterTime + 15)} title="快進 15 秒（→）" className={`${button} text-sm`}>
            15 ↻
          </button>
          <select
            value={rate}
            onChange={(event) => changeRate(Number(event.target.value))}
            aria-label="速度"
            className="min-h-11 rounded-full border border-stone-300 bg-white px-3 text-sm"
          >
            {SPEEDS.map((speed) => (
              <option key={speed} value={speed}>
                {speed}×
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => nextSection && goSection(nextSection.index)}
            disabled={!nextSection}
            title="下一段（]）"
            className={`${button} text-sm`}
          >
            <span className="mr-1 hidden sm:inline">{nextSection ? shortLabel(nextSection.item.label) : "下一段"}</span> ›
          </button>
        </div>
      </footer>

      {clip?.sermon.media_url && (
        <audio
          ref={media}
          src={mediaSrc(clip.sermon.media_url)}
          preload="auto"
          onLoadedMetadata={onLoaded}
          onTimeUpdate={onTimeUpdate}
          onEnded={advance}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
        />
      )}
    </div>
  );
}
