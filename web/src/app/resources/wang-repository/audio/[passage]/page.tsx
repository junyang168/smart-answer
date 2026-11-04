"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import ScriptureSlide, { type OriginalLanguageEvent, type SlidePoint } from "../ScriptureSlide";
import ChapterSlide, { type ChapterSlideData } from "../ChapterSlide";

/**
 * 教授的原声，按经文重排。
 *
 * 名义上是马太福音释经，实际讲到一处经文时常跳去讲别的题目，讲完再跳回来。现
 * 场听是自然的，事后按经文找就不是。这一页把他讲某一段经文的话聚起来——一个字
 * 都不新增，价值在次序。
 *
 * 三条规则，都是从材料量出来的：
 *
 * **一段经文一个入口，不是一个观点一个入口。** 原来按中心观点分组，同一篇讲道
 * 在五组里出现两到四次，每次只播属于那一组的几段，听下来是碎的。现在一篇讲道
 * 一行，把它讲这段经文的所有材料并起来，教授原来的次序就还回去了。观点没丢,
 * 挪到幻灯上当标题。
 *
 * **一篇之内隔得近的接着播。** 隔两分钟以内不跳——跳过一两分钟省不下什么，却让
 * 人听见一次断口。剩下的断口最小的也有两分半，那种长度确实是他岔去讲了别的。
 *
 * **不去重。** 同一件事他在五篇讲道里各讲一遍，每一遍的理由都不一样——有的从信
 * 仰内容说，有的从希腊文性别说。删掉四遍等于删掉他四个论证。要综合版的读者去
 * 看文章。
 */

/** 一段录音。按章的页面里，不属这一章释经的片段也在，标为 `left_out`：听不
 * 听由读者决定，默认收起（负责人 2026-11-03）。 */
type Stretch = {
  start: number;
  end: number;
  left_out?: boolean;
  /** 为什么不算这一章的释经。编辑整理，不是他的话。 */
  reasons?: string[];
  /** 每个片段自己的理由与时间。 */
  parts?: { start: number; end: number; reason: string }[];
  /** 这一段的逐字稿。 */
  text?: string;
};
/** 教授念到经文的时刻。只在幻灯角上标一行小字，主经文不跟着换。 */
type Spoken = { at: number; scripture: string; label: string };
type Provenance = {
  claim_ids: string[];
  evidence_step_ids: string[];
  source_fragment_ids: string[];
};
/** 他立起一个判断的时刻。幻灯的标题跟着这个走。 */
type Judgement = {
  at: number;
  judgement: string;
  seconds: number;
  /** 这条主张自己的证据步骤：他怎样推到这个判断。 */
  points?: SlidePoint[];
  /** 主张类型是应用。 */
  application?: boolean;
  scripture: string;
  provenance?: Provenance;
};
type Sermon = {
  source_id: string;
  transcript_id: string;
  title: string;
  media_kind: "audio" | "video" | null;
  media_url: string | null;
  media_duration: number | null;
  stretches: Stretch[];
  judgements: Judgement[];
  spoken: Spoken[];
  /** 他念到某一节的时刻。念出来的比报出来的准。 */
  readings: { at: number; scripture: string }[];
  original_language_events: OriginalLanguageEvent[];
  /** 按章页面：模型读逐字稿写的投影片。有它就不用 Claim 拼的幻灯。 */
  slides?: ChapterSlideData[];
  seconds: number;
  left_out_seconds?: number;
};

/** 錄音的來源。
 *
 * 生產環境同源——nginx 的 `location /web/` 直接從磁碟提供。開發時走本專案的
 * `/dev-media`：Next 的 dev rewrite 會把整個檔案緩衝完才回應（59 MB 等八秒
 * 仍然 `buffered: null`），而直接連 nginx:8888 又被 CORS 擋下。
 */
const mediaSrc = (url: string) =>
  process.env.NODE_ENV === "production"
    ? url
    : url.replace("/web/video/", "/dev-media/");

/** 播到这一刻，对应哪条来源绑定的 Claim。 */
function judgementAt(sermon: Sermon, seconds: number) {
  let held = sermon.judgements[0];
  for (const mark of sermon.judgements) {
    if (mark.at > seconds) break;
    held = mark;
  }
  return held;
}

/** 幻灯上该摆哪一节。
 *
 * 不是整段。16:13-23 有十一节，铺出来 380 个字是一堵墙，而且每张幻灯长得都一
 * 样，因为整页共用同一段经文。
 *
 * 也不是「他此刻念到哪一节」——他到 2:18 还一节都没念出来，而那时候讲的是「彼
 * 得認信耶穌是基督、永生神的兒子」，太16:16；退回段首会摆成太16:13。
 *
 * 是这条 topic 自己讲的那一节。它是一条主张，主张的 `scripture_refs` 就写着：
 * 「彼得認信」是太16:16，「相較於別人如何評價耶穌」是太16:13-15，「耶穌禁止門
 * 徒立即公開祂是基督」是太16:20-23。经文跟着他讲到哪里换，跟标题同步。
 */
/** 幻灯上一次最多摆几节。
 *
 * 三十四个 topic 里二十四个只有一节，五个三节，两个四节——够读。剩三个的主张
 * 引用本身就宽（16:6-12、16:16-23、16:13-21），摆出来又是一堵墙。
 */
const MOST_VERSES = 4;

function verseAt(sermon: Sermon, seconds: number, passage: string) {
  let slug = sermon.judgements[0]?.scripture || passage;
  let since = 0;
  for (const mark of sermon.judgements) {
    if (mark.at > seconds) break;
    if (mark.scripture) {
      slug = mark.scripture;
      since = mark.at;
    }
  }

  // 这条 topic 开始之后他念到的那一节，盖过主张给的那一节。
  //
  // 一条 topic 两分钟，他在里面是会往前走的：topic 是「彼得認信耶穌是基督、永
  // 生神的兒子」，主张说太16:16，没错——可他念完十六节就接着念十七、十八、十九
  // 节，而幻灯停在十六节。
  //
  // 他念的和他报的，按时间合起来用。
  //
  // 报的常常是预告：2:15 他说「我再念一下，然後我特別來看十九節那一段」，接着
  // 从十七节念起。只听报的，2:24 会显示十九节，而他正念着「乃是我在天上的父指
  // 示你的」，十七节。
  //
  // 但报的不能丢：28:47 他说「二十二節，彼得就拉著祂，勸祂說：主啊，萬不可如
  // 此」——那一句他是复述不是照念，逐字比对认不出来。
  //
  // 按时间排，后来的盖过先前的：2:15 报十九节，2:17 起念十七节，盖回来了。
  const said = [
    ...sermon.spoken.map((m) => ({ at: m.at, scripture: m.scripture })),
    ...sermon.readings,
  ]
    // 同一章但在本页之外的经节是旁证，不得盖掉主经文。（四）1 进入 16:13-23
    // 的可听段时，lookback 还抓着 16:10；它应该只出现在角上的「他此刻在念」，
    // 而不是让 slide 主体从 Claim 的 16:13-15 跳回 16:10。
    .filter((m) => inside(m.scripture, passage))
    .sort((a, b) => a.at - b.at);
  for (const mark of said) {
    if (mark.at > seconds) break;
    if (mark.at >= since) slug = mark.scripture;
  }
  const [book, chapter, from, to] = slug.split("-");
  const [start, end] = [Number(from), Number(to ?? from)];
  if (end - start + 1 <= MOST_VERSES) return slug;

  // 引用太宽时，用他此刻念到的那一节收窄。
  //
  // 「彼得雖認識耶穌是基督，卻不認識彌賽亞受苦」引的是太16:16-23，八节；而
  // 24:05 他念到十六章二十节，26:55 讲的正是二十一、二十二节。从他念的那一节
  // 起摆四节，比铺八节准，也比截头四节准——截头会摆成 16:16-19，他早讲过了。
  let named = start;
  for (const mark of sermon.spoken) {
    if (mark.at > seconds) break;
    const parts = mark.scripture.split("-");
    if (parts[0] !== book || parts[1] !== chapter) continue;
    const verse = Number(parts[2]);
    if (verse >= start && verse <= end) named = verse;
  }
  const stop = Math.min(named + MOST_VERSES - 1, end);
  return `${book}-${chapter}-${named}` + (stop !== named ? `-${stop}` : "");
}

/** `mat-16-19` 落在 `mat-16-18-19` 里面吗。 */
function inside(slug: string, passage: string) {
  const one = slug.split("-");
  const all = passage.split("-");
  if (one.length < 3 || all.length < 3) return false;
  if (one[0] !== all[0] || one[1] !== all[1]) return false;
  const [low, high] = [Number(all[2]), Number(all[3] ?? all[2])];
  return Number(one[2]) >= low && Number(one[3] ?? one[2]) <= high;
}

/** 当前讲论要点里，到这一刻为止教授明确讲过哪些原文。
 *
 * 讲过的留着，不被后一个顶掉——他讲课就是一个个字累起来的：先说「你是彼得
 * (Petrus)」，再说「這磐石(petra)」，两个摆在一起才看得出他在比什么。换到下
 * 一个 Claim 就清空，不能把上一题的希腊文一路带到整篇讲道结尾。
 */
function originalLanguageAt(sermon: Sermon, seconds: number) {
  const current = judgementAt(sermon, seconds);
  if (!current) return [];
  const index = sermon.judgements.indexOf(current);
  const previous = sermon.judgements[index - 1];
  const stretch = sermon.stretches.find(
    (item) => item.start <= current.at && current.at < item.end,
  );
  // 一段录音开头的主导 Claim 可能到第一个 30 秒块才立标记；开场十几秒已经讲过
  // 的 Petrus/Petra 仍属于它，不能因为 mark.at=0:30 就被切掉。
  const since = stretch && (!previous || previous.at < stretch.start) ? stretch.start : current.at;
  const until = sermon.judgements[index + 1]?.at ?? Number.POSITIVE_INFINITY;
  return sermon.original_language_events.filter(
    (item) => item.at >= since && item.at <= seconds && item.at < until,
  );
}

/** 播到这一刻，他手上翻的是哪一节。
 *
 * 只在幻灯角上标一行小字，而且只在他翻出这段经文之外时才提——「他此刻在念
 * 馬太福音 16:19」是废话，幻灯上摆的就是 16:18-19。翻到弗2:20、约20:23 才值得
 * 说一句。
 *
 * 主经文不跟着它换：他整段都在拆 16:18-19 的希腊文，翻出去的是旁证。
 */
function citedAt(sermon: Sermon, seconds: number, passage: string) {
  let cited: Spoken | null = null;
  for (const mark of sermon.spoken) {
    if (mark.at > seconds) break;
    cited = mark;
  }
  return cited && !inside(cited.scripture, passage) ? cited.label : "";
}

/** 按章的页面按经节次序分段；一段经文的页面只有一段、没有段标题。 */
type Section = { passage: string; label: string; sermons: Sermon[] };

/** 这一行此刻要播的几段：收起时只播保留的，展开时连灰色的一起按原来的次序播。 */
const visible = (sermon: Sermon, showLeftOut: boolean) =>
  showLeftOut ? sermon.stretches : sermon.stretches.filter((stretch) => !stretch.left_out);

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const minutes = (s: number) => `${Math.round(s / 60)} 分`;

export default function OriginalAudioPage() {
  const passage = String(useParams()?.passage ?? "");
  const [label, setLabel] = useState("");
  const [title, setTitle] = useState("");
  const [sections, setSections] = useState<Section[] | null>(null);
  const [error, setError] = useState("");
  const [missing, setMissing] = useState<string[]>([]);
  // 不属这一章释经的片段，默认收起；读者要听就打开。
  const [showLeftOut, setShowLeftOut] = useState(false);
  const [playing, setPlaying] = useState<{ sermon: Sermon; index: number } | null>(null);
  const [paused, setPaused] = useState(false);
  // 幻灯的标题要跟着他讲到哪里换，所以得知道播到第几秒。
  const [at, setAt] = useState(0);
  const media = useRef<HTMLAudioElement | HTMLVideoElement | null>(null);
  // 要跳到的那一刻，等新的播放器载入后再跳。
  //
  // 换一行就是换一个播放器元素。`setTimeout(0)` 时新元素常常还没挂上——自动接下
  // 一段是在 timeupdate 里触发的，React 不急着提交——于是跳转落在旧元素上，新的
  // 从 0:00 播起：5:13-16 放完，5:17-20 从（二）1 的开头放，而不是 36 分。
  const pendingSeek = useRef<number | null>(null);

  function applyPendingSeek() {
    const element = media.current;
    if (!element || pendingSeek.current === null) return;
    // 离目标还远就跳，到了才算数。旧元素在卸下之前也可能收到这一跳，所以不能
    // 跳一次就清掉——要等挂上的那个元素真的播到了那里。
    if (Math.abs(element.currentTime - pendingSeek.current) > 1) {
      element.currentTime = pendingSeek.current;
    } else {
      pendingSeek.current = null;
    }
  }

  useEffect(() => {
    (async () => {
      try {
        const response = await fetch(`/api/public/original-audio/${passage}`, {
          cache: "no-store",
        });
        if (response.status === 404) throw new Error("這段經文還沒有原聲頁面。");
        if (!response.ok) throw new Error(`服務回傳 ${response.status}`);
        const data = await response.json();
        setLabel(data.label ?? "");
        setTitle(data.title ?? "");
        setMissing(data.missing_lectures ?? []);
        setSections(
          data.sections ?? [{ passage: data.passage, label: "", sermons: data.sermons ?? [] }],
        );
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "讀不到");
      }
    })();
  }, [passage]);

  // 一篇讲道里的几段接着播：放完一段自动跳到下一段的起点。跳过的地方教授在讲
  // 别的，所以时间轴上会看到断口——看得见就够了，不必打断听。
  /** 从某一刻开始播，不管它落在哪一段。
   *
   * topic 的起点未必是某一段的起点——一段里常有两三个 topic。点 topic 要跳到他
   * 说那句话的地方，不是跳到那一段的开头。
   */
  function playFrom(sermon: Sermon, seconds: number) {
    const index = Math.max(
      0,
      visible(sermon, showLeftOut).findIndex((stretch) => seconds < stretch.end),
    );
    setPlaying({ sermon, index });
    setPaused(false);
    setAt(seconds);
    pendingSeek.current = seconds;
    window.setTimeout(() => {
      const element = media.current;
      if (element) {
        element.currentTime = seconds;
        void element.play();
      }
    }, 0);
  }

  function play(sermon: Sermon, index = 0) {
    const start = visible(sermon, showLeftOut)[index].start;
    setPlaying({ sermon, index });
    setPaused(false);
    setAt(start);
    pendingSeek.current = start;
    window.setTimeout(() => {
      const element = media.current;
      if (element) {
        element.currentTime = start;
        void element.play();
      }
    }, 0);
  }

  /** 收起或展开灰色片段。正在播的那一行，按此刻的位置重新对上它在新列表里的第几段。 */
  function switchLeftOut(show: boolean) {
    setShowLeftOut(show);
    if (!playing) return;
    const now = media.current?.currentTime ?? at;
    const list = visible(playing.sermon, show);
    const index = list.findIndex((stretch) => now < stretch.end);
    if (index < 0) return;
    setPlaying({ ...playing, index });
    // 收起时正落在灰色片段里，跳到下一段保留的。
    if (now < list[index].start && media.current) media.current.currentTime = list[index].start;
  }

  /** 真的暫停，不是把播放器關掉。
   *
   * 原來這顆鈕畫成 `❚❚` 卻執行 `setPlaying(null)`——播放器連同進度一起消失，
   * 按的人以為自己暫停了，回來卻得從頭找。圖示承諾什麼，就要做什麼。
   */
  function toggle(sermon: Sermon) {
    if (playing?.sermon !== sermon) {
      play(sermon);
      return;
    }
    const element = media.current;
    if (!element) return;
    if (element.paused) {
      void element.play();
    } else {
      element.pause();
    }
  }

  function onTimeUpdate() {
    const element = media.current;
    if (!element || !playing) return;
    if (pendingSeek.current !== null) {
      applyPendingSeek();
      return;
    }
    setAt(element.currentTime);
    const list = visible(playing.sermon, showLeftOut);
    const stretch = list[playing.index];
    if (!stretch || element.currentTime < stretch.end) return;
    const next = playing.index + 1;
    if (next < list.length) {
      // 相连的两段不必跳：设一次 currentTime 就会顿一下。
      if (Math.abs(list[next].start - element.currentTime) > 1) {
        element.currentTime = list[next].start;
      }
      setPlaying({ ...playing, index: next });
      return;
    }
    // 这一行放完，接着放下一行。按章的页面是按经节次序排的，接着放就是接着往
    // 下一节讲，哪怕下一行是另一堂课。
    const rows = (sections ?? []).flatMap((section) => section.sermons);
    const following = rows[rows.indexOf(playing.sermon) + 1];
    if (following?.media_url) {
      play(following);
    } else {
      element.pause();
    }
  }

  if (error)
    return (
      <main className="min-h-screen bg-stone-50">
        <p className="mx-auto max-w-4xl px-5 py-16 text-rose-800">{error}</p>
      </main>
    );
  if (!sections)
    return (
      <main className="min-h-screen bg-stone-50">
        <p className="mx-auto max-w-4xl px-5 py-16 text-stone-600" role="status">
          正在讀取…
        </p>
      </main>
    );

  const hasLeftOut = sections.some((section) =>
    section.sermons.some((sermon) => sermon.stretches.some((stretch) => stretch.left_out)),
  );

  return (
    <main className="min-h-screen bg-stone-50">
      <div className="mx-auto flex max-w-4xl flex-col gap-8 px-5 py-12 sm:px-8 sm:py-16">
        <header className="flex flex-col gap-3">
          {/* 回得去。原来这一页是个断头路——从文库进来之后没有任何路标，只能靠
              浏览器的后退键。 */}
          <Link
            href="/resources/wang-repository"
            className="w-fit text-sm font-semibold text-amber-800 hover:text-amber-900"
          >
            <span aria-hidden="true">←</span> 王守仁教授聖經講論文庫
          </Link>
          <p className="text-sm font-bold text-amber-800">教授原聲 · {label}</p>
          <h1 className="font-serif text-3xl font-bold leading-tight text-stone-950 sm:text-4xl">
            {title || label}
          </h1>
          <p className="max-w-3xl text-lg leading-8 text-stone-600">
            {sections.length > 1
              ? `按經節次序，從 ${label} 一段段聽他講。每一段收他在各堂課裡講這幾節的原聲，放完自動接下一段。`
              : `他在 ${sections[0]?.sermons.length ?? 0} 篇講道裡講過這段經文。點一篇，聽他自己講——不是我們寫的字，是他的原話。每一遍的理由都不一樣，所以不合併。`}
          </p>
          {hasLeftOut && (
            // 不属这一章释经的部分——见证、别处经文、无关的问答——没有删，只是
            // 默认收起。要听的人打开，它们就回到原来的位置，连续播放也会播。
            <label className="flex w-fit cursor-pointer items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 py-2 text-sm text-stone-700 shadow-sm">
              <input
                type="checkbox"
                role="switch"
                checked={showLeftOut}
                onChange={(event) => switchLeftOut(event.target.checked)}
                className="h-4 w-4 accent-stone-500"
              />
              顯示其餘部分（見證、別處經文、與本章無關的問答，以灰色標出）
            </label>
          )}
          {missing.length > 0 && (
            <p className="text-sm text-stone-500">
              尚未整理：{missing.map((lecture) => lecture.split("：").pop()).join("、")}
            </p>
          )}
        </header>

        {sections.map((section) => (
        <section key={section.passage} className="flex flex-col gap-3">
        {section.label && (
          <h2 className="font-serif text-xl font-bold text-stone-900">
            {section.label}
            <span className="ml-3 font-mono text-sm font-normal text-stone-400">
              {minutes(section.sermons.reduce((sum, row) => sum + row.seconds, 0))}
            </span>
          </h2>
        )}

        <ul className="flex flex-col divide-y divide-stone-200 overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm">
        {section.sermons.map((sermon) => {
          const open = playing?.sermon === sermon;
          const seconds = open ? at : (sermon.stretches[0]?.start ?? 0);
          const currentJudgement = judgementAt(sermon, seconds);
          const leftOutHere = open
            ? sermon.stretches.find(
                (stretch) => stretch.left_out && stretch.start <= seconds && seconds < stretch.end,
              )
            : undefined;
          const shown = visible(sermon, showLeftOut);
          return (
            <li key={`${section.passage}-${sermon.source_id}`}>
              <button
                type="button"
                onClick={() => toggle(sermon)}
                disabled={!sermon.media_url}
                className="flex w-full items-baseline gap-3 px-4 py-3 text-left hover:bg-slate-50 disabled:opacity-40"
              >
                <span className={open ? "text-indigo-600" : "text-slate-400"}>
                  {open && !paused ? "❚❚" : "▶"}
                </span>
                <span className="flex-1 text-[0.88rem] text-slate-900">{sermon.title}</span>
                <span className="font-mono text-[0.75rem] text-slate-400">
                  {minutes(sermon.seconds)}
                  {showLeftOut && (sermon.left_out_seconds ?? 0) >= 30 && (
                    <span className="ml-2 text-stone-300">+ {minutes(sermon.left_out_seconds ?? 0)}</span>
                  )}
                </span>
              </button>

              {open && sermon.media_url && (
                // 就地展開。原來是釘在頁面頂端的浮層，點哪一行畫面都往上跳
                // 一次——聽的人剛按下去就先失去了位置。
                <div className="flex flex-col gap-2 border-t border-slate-100 bg-slate-50 px-4 py-3">
                  {/* 只有录音的讲道，屏幕上放教授正在解的经文和他立的判断。有画
                      面的不放——那些本来就有得看。 */}
                  {leftOutHere ? (
                    // 正在播的是不属这一章释经的片段：不拿上一张幻灯冒充。
                    <div className="rounded-xl border border-dashed border-stone-300 bg-stone-100 px-5 py-6 text-stone-500">
                      <p className="text-xs font-bold">不屬本章釋經 · 編輯整理</p>
                      <p className="mt-2 text-sm leading-6">
                        {leftOutHere.parts?.find((part) => part.start <= seconds && seconds < part.end)?.reason ??
                          leftOutHere.reasons?.[0]}
                      </p>
                    </div>
                  ) : sermon.slides ? (
                    (() => {
                      const slide = [...sermon.slides].reverse().find((item) => item.at <= seconds) ?? sermon.slides[0];
                      return slide ? <ChapterSlide slide={slide} seconds={seconds} /> : null;
                    })()
                  ) : sermon.media_kind === "audio" && (
                    <ScriptureSlide
                      slug={verseAt(sermon, seconds, passage)}
                      title={currentJudgement?.judgement ?? ""}
                      points={currentJudgement?.points ?? []}
                      application={currentJudgement?.application ?? false}
                      originalLanguage={originalLanguageAt(sermon, seconds)}
                      cited={citedAt(sermon, seconds, passage)}
                    />
                  )}
                  {sermon.media_kind === "video" ? (
                    <video
                      ref={media as React.RefObject<HTMLVideoElement>}
                      src={mediaSrc(sermon.media_url)}
                      controls
                      autoPlay
                      onTimeUpdate={onTimeUpdate}
                      onLoadedMetadata={applyPendingSeek}
                      onPlay={() => setPaused(false)}
                      onPause={() => setPaused(true)}
                      className="w-full rounded-lg"
                    />
                  ) : (
                    <audio
                      ref={media as React.RefObject<HTMLAudioElement>}
                      src={mediaSrc(sermon.media_url)}
                      controls
                      autoPlay
                      onTimeUpdate={onTimeUpdate}
                      onLoadedMetadata={applyPendingSeek}
                      onPlay={() => setPaused(false)}
                      onPause={() => setPaused(true)}
                      className="w-full"
                    />
                  )}
                  {shown.length > 1 && (
                    // 一堂課裡他把同一件事講了幾次，中間岔去講別的。斷口看
                    // 得見就夠了，不必打斷聽——放完一段自己跳到下一段。
                    <div className="flex items-center gap-1">
                      {shown.map((stretch, index) => (
                        <button
                          key={stretch.start}
                          type="button"
                          onClick={() => play(sermon, index)}
                          title={`第 ${index + 1} 段 · ${clock(stretch.start)}${stretch.left_out ? " · 不屬本章釋經" : ""}`}
                          className={`h-1.5 rounded-full transition ${
                            index === playing?.index
                              ? stretch.left_out
                                ? "bg-stone-500"
                                : "bg-indigo-600"
                              : stretch.left_out
                                ? "bg-stone-200 hover:bg-stone-300"
                                : "bg-slate-300 hover:bg-slate-400"
                          }`}
                          style={{ flexGrow: Math.max(1, stretch.end - stretch.start) }}
                        />
                      ))}
                    </div>
                  )}

                  {/* 逐字稿退到核对入口。主要播放体验由同步 slide 承担；需要查看
                      全文的人主动展开，再去原始讲道页。 */}
                  <details className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
                    <summary className="cursor-pointer select-none font-semibold text-slate-700">
                      完整逐字稿（核對用）
                    </summary>
                    <p className="mt-2 leading-relaxed text-slate-500">
                      Slide 只顯示此刻的講論要點、經文與原文講解；完整內容仍保留在原始講道頁。
                    </p>
                    <Link
                      href={`/resources/sermons/${encodeURIComponent(sermon.transcript_id)}`}
                      className="mt-2 inline-block font-semibold text-amber-800 hover:text-amber-900"
                    >
                      打開完整逐字稿 ↗
                    </Link>
                  </details>

                  {/* 他在这一篇里讲的几件事，各从第几分钟起。
                      一篇讲道二十五分钟，一整条时间轴上找不到东西；他自己立的
                      判断就是目录。点一条跳到他说那句话的地方，之后照常往下
                      播，不断——播放的连续区间和读者的入口是两件事。 */}
                  <ul className="flex flex-col divide-y divide-slate-200/70 border-t border-slate-200 pt-1">
                    {[
                      ...sermon.judgements.map((mark) => ({ at: mark.at, mark })),
                      ...(showLeftOut
                        ? sermon.stretches
                            .filter((stretch) => stretch.left_out)
                            .map((stretch) => ({ at: stretch.start, stretch }))
                        : []),
                    ]
                      .sort((a, b) => a.at - b.at)
                      .map((item) => {
                        if ("stretch" in item && item.stretch) {
                          const stretch = item.stretch;
                          const here = leftOutHere === stretch;
                          // 灰色片段留在原来的位置：他在这里岔开去讲了别的。
                          return (
                            <li key={`grey-${stretch.start}`} className="bg-stone-50/60">
                              <button
                                type="button"
                                onClick={() => playFrom(sermon, stretch.start)}
                                className="flex w-full items-baseline gap-3 py-2 text-left hover:bg-stone-100"
                              >
                                <span className={`font-mono text-[0.7rem] ${here ? "text-stone-600" : "text-stone-300"}`}>
                                  {clock(stretch.start)}
                                </span>
                                <span className={`flex-1 text-[0.8rem] leading-snug ${here ? "text-stone-600" : "text-stone-400"}`}>
                                  不屬本章釋經：{stretch.reasons?.[0]}
                                  {(stretch.reasons?.length ?? 0) > 1 && `（另 ${(stretch.reasons?.length ?? 1) - 1} 處）`}
                                </span>
                                <span className="font-mono text-[0.7rem] text-stone-300">
                                  {minutes(stretch.end - stretch.start)}
                                </span>
                              </button>
                              {stretch.text && (
                                <details className="pb-2 pl-12 pr-2 text-[0.78rem] text-stone-400">
                                  <summary className="cursor-pointer select-none">各處理由與逐字稿</summary>
                                  {(stretch.parts?.length ?? 0) > 1 && (
                                    <ul className="mt-1 flex flex-col gap-0.5">
                                      {stretch.parts?.map((part) => (
                                        <li key={part.start}>
                                          <span className="font-mono">{clock(part.start)}</span> {part.reason}
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                  <p className="mt-2 whitespace-pre-line leading-relaxed">{stretch.text}</p>
                                </details>
                              )}
                            </li>
                          );
                        }
                        const mark = (item as { mark: Judgement }).mark;
                        const here = !leftOutHere && currentJudgement?.at === mark.at;
                        return (
                          <li key={mark.at}>
                            <button
                              type="button"
                              onClick={() => playFrom(sermon, mark.at)}
                              className="flex w-full items-baseline gap-3 py-2 text-left hover:bg-white"
                            >
                              <span
                                className={`font-mono text-[0.7rem] ${here ? "text-indigo-600" : "text-slate-400"}`}
                              >
                                {clock(mark.at)}
                              </span>
                              <span
                                className={`flex-1 text-[0.8rem] leading-snug ${here ? "font-semibold text-slate-900" : "text-slate-600"}`}
                              >
                                {mark.judgement}
                              </span>
                              <span className="font-mono text-[0.7rem] text-slate-400">
                                {minutes(mark.seconds)}
                              </span>
                            </button>
                          </li>
                        );
                      })}
                  </ul>
                </div>
              )}
            </li>
          );
        })}
        </ul>
        </section>
        ))}
      </div>
    </main>
  );
}
