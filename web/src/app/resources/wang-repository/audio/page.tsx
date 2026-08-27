"use client";

import { useEffect, useRef, useState } from "react";

import ScriptureSlide, { type Gloss } from "./ScriptureSlide";

/**
 * 教授的原声，按经文重排。
 *
 * 名义上是马太福音释经，实际讲到一处经文时常跳去讲别的题目，讲完再跳回来。现
 * 场听是自然的，事后按经文找就不是。这一页把他讲太16:18-19 的话按经文聚起来
 * ——一个字都不新增，价值在次序。
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

type Stretch = { start: number; end: number };
/** 教授念到经文的时刻。只在幻灯角上标一行小字，主经文不跟着换。 */
type Spoken = { at: number; scripture: string; label: string };
/** 他立起一个判断的时刻。幻灯的标题跟着这个走。 */
type Judgement = { at: number; judgement: string };
type Sermon = {
  source_id: string;
  transcript_id: string;
  title: string;
  media_kind: "audio" | "video" | null;
  media_url: string | null;
  stretches: Stretch[];
  judgements: Judgement[];
  spoken: Spoken[];
  glosses: Gloss[];
  seconds: number;
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

/** 播到这一刻，他正在立哪个判断。 */
function judgementAt(sermon: Sermon, seconds: number) {
  let held = sermon.judgements[0]?.judgement ?? "";
  for (const mark of sermon.judgements) {
    if (mark.at > seconds) break;
    held = mark.judgement;
  }
  return held;
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

/** 播到这一刻，他正在讲哪个字。
 *
 * 取最近一次注解。两分钟以内才算他还在讲那个字——再往前就是听的人已经听过去
 * 的了。
 */
function glossAt(sermon: Sermon, seconds: number) {
  let held: Gloss | undefined;
  for (const item of sermon.glosses) {
    if (item.at > seconds) break;
    held = item;
  }
  return held && seconds - held.at <= 120 ? held : undefined;
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

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const minutes = (s: number) => `${Math.round(s / 60)} 分`;

export default function OriginalAudioPage() {
  const [reference, setReference] = useState("");
  const [sermons, setSermons] = useState<Sermon[] | null>(null);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState<{ sermon: Sermon; index: number } | null>(null);
  const [paused, setPaused] = useState(false);
  // 幻灯的标题要跟着他讲到哪里换，所以得知道播到第几秒。
  const [at, setAt] = useState(0);
  const media = useRef<HTMLAudioElement | HTMLVideoElement | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const response = await fetch("/api/public/original-audio", { cache: "no-store" });
        if (!response.ok) throw new Error(`服務回傳 ${response.status}`);
        const data = await response.json();
        setReference(data.reference ?? "");
        setSermons(data.sermons ?? []);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "讀不到");
      }
    })();
  }, []);

  // 一篇讲道里的几段接着播：放完一段自动跳到下一段的起点。跳过的地方教授在讲
  // 别的，所以时间轴上会看到断口——看得见就够了，不必打断听。
  function play(sermon: Sermon, index = 0) {
    setPlaying({ sermon, index });
    setPaused(false);
    setAt(sermon.stretches[index].start);
    window.setTimeout(() => {
      const element = media.current;
      if (element) {
        element.currentTime = sermon.stretches[index].start;
        void element.play();
      }
    }, 0);
  }

  /** 真的暫停，不是把播放器關掉。
   *
   * 原來這顆鈕畫成 `❚❚` 卻執行 `setPlaying(null)`——播放器連同進度一起消失，
   * 按的人以為自己暫停了，回來卻得從頭找。圖示承諾什麼，就要做什麼。
   */
  function toggle(sermon: Sermon) {
    if (playing?.sermon.source_id !== sermon.source_id) {
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
    setAt(element.currentTime);
    const stretch = playing.sermon.stretches[playing.index];
    if (element.currentTime < stretch.end) return;
    const next = playing.index + 1;
    if (next < playing.sermon.stretches.length) {
      element.currentTime = playing.sermon.stretches[next].start;
      setPlaying({ ...playing, index: next });
    } else {
      element.pause();
    }
  }

  if (error) return <p className="px-1 py-10 text-sm text-rose-700">{error}</p>;
  if (!sermons) return <p className="px-1 py-10 text-sm text-slate-400">讀取中…</p>;

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-6 px-5 py-10">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">
          王教授講太 16:18-19
        </h1>
        <p className="text-sm leading-relaxed text-slate-600">
          他在 {sermons.length} 篇講道裡講過這段經文。點一篇，聽他自己講——不是我們寫的字，是他的原話。
          每一遍的理由都不一樣，所以不合併。
        </p>
      </header>

      <ul className="flex flex-col divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
        {sermons.map((sermon) => {
          const open = playing?.sermon.source_id === sermon.source_id;
          const seconds = open ? at : (sermon.stretches[0]?.start ?? 0);
          return (
            <li key={sermon.source_id}>
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
                </span>
              </button>

              {open && sermon.media_url && (
                // 就地展開。原來是釘在頁面頂端的浮層，點哪一行畫面都往上跳
                // 一次——聽的人剛按下去就先失去了位置。
                <div className="flex flex-col gap-2 border-t border-slate-100 bg-slate-50 px-4 py-3">
                  {/* 只有录音的讲道，屏幕上放教授正在解的经文和他立的判断。有画
                      面的不放——那些本来就有得看。 */}
                  {sermon.media_kind === "audio" && (
                    <ScriptureSlide
                      slug={reference}
                      title={judgementAt(sermon, seconds)}
                      gloss={glossAt(sermon, seconds)}
                      cited={citedAt(sermon, seconds, reference)}
                    />
                  )}
                  {sermon.media_kind === "video" ? (
                    <video
                      ref={media as React.RefObject<HTMLVideoElement>}
                      src={mediaSrc(sermon.media_url)}
                      controls
                      autoPlay
                      onTimeUpdate={onTimeUpdate}
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
                      onPlay={() => setPaused(false)}
                      onPause={() => setPaused(true)}
                      className="w-full"
                    />
                  )}
                  {sermon.stretches.length > 1 && (
                    // 一堂課裡他把同一件事講了幾次，中間岔去講別的。斷口看
                    // 得見就夠了，不必打斷聽——放完一段自己跳到下一段。
                    <div className="flex items-center gap-1">
                      {sermon.stretches.map((stretch, index) => (
                        <button
                          key={stretch.start}
                          type="button"
                          onClick={() => play(sermon, index)}
                          title={`第 ${index + 1} 段 · ${clock(stretch.start)}`}
                          className={`h-1.5 rounded-full transition ${
                            index === playing?.index
                              ? "bg-indigo-600"
                              : "bg-slate-300 hover:bg-slate-400"
                          }`}
                          style={{ flexGrow: Math.max(1, stretch.end - stretch.start) }}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
