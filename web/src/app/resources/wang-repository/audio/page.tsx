"use client";

import { useEffect, useRef, useState } from "react";

import ScriptureSlide from "./ScriptureSlide";

/**
 * 教授的原声，按中心观点重排。
 *
 * 名义上是马太福音释经，实际讲到一处经文时常跳去讲别的题目，讲完再跳回来。现
 * 场听是自然的，事后按经文找就不是。这一页把他的原话按判断重排——一个字都不
 * 新增，价值在次序。
 *
 * 三条规则，都是从材料量出来的：一个判断按讲道分开列（跨讲道接起来会跳过半小
 * 时）；一篇之内的几段接着播（他在同一堂课里常翻来覆去讲同一件事）；不去重
 * （五篇里每一遍的理由都不一样，删掉就是删掉他的论证——要综合版的去看文章）。
 */

type Stretch = { start: number; end: number };
/** 教授念到经文的时刻。幻灯跟着这个走，不跟着段走——他在一段之内会翻好几处。 */
type Spoken = { at: number; scripture: string; label: string };
type Occasion = {
  source_id: string;
  transcript_id: string;
  title: string;
  media_kind: "audio" | "video" | null;
  media_url: string | null;
  saying: string;
  other_sayings: string[];
  stretches: Stretch[];
  spoken: Spoken[];
  seconds: number;
};
type Viewpoint = {
  structure_id: string;
  central_proposition: string;
  scripture_scope: string[];
  focal_count: number;
  scripture: string;
  occasions: Occasion[];
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

/** 播到这一刻，教授手上翻的是哪一节。
 *
 * 只用来在幻灯角上标一行小字。主经文不跟着它换——他整组都在拆同一段经文，翻出
 * 来的那些是旁证。
 */
function citedAt(occasion: Occasion, seconds: number, main: string) {
  let cited: Spoken | null = null;
  for (const mark of occasion.spoken) {
    if (mark.at > seconds) break;
    cited = mark;
  }
  // 他翻到的正是幻灯上那一节时，不必再说一遍。
  return cited && cited.scripture !== main ? cited.label : "";
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const minutes = (s: number) => `${Math.round(s / 60)} 分`;

export default function OriginalAudioPage() {
  const [rows, setRows] = useState<Viewpoint[] | null>(null);
  const [error, setError] = useState("");
  // 认的是「哪个判断底下的哪一篇」，不能只认 source_id。
  //
  // 同一篇讲道会出现在多个判断底下——这正是这一页的前提：教授讲一次话同时立了
  // 几个判断。只认 source_id 的话，点一行会让所有同源的行一起展开，而 `media`
  // ref 指向最后挂载的那个，于是暂停键操作的是你看不见的播放器。
  const [playing, setPlaying] = useState<
    { structureId: string; occasion: Occasion; index: number } | null
  >(null);
  const [paused, setPaused] = useState(false);
  // 幻灯要跟着他念到哪一节换，所以得知道播到第几秒。
  const [at, setAt] = useState(0);
  const media = useRef<HTMLAudioElement | HTMLVideoElement | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const response = await fetch("/api/public/original-audio", { cache: "no-store" });
        if (!response.ok) throw new Error(`服務回傳 ${response.status}`);
        const data = await response.json();
        setRows(data.viewpoints ?? []);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "讀不到");
      }
    })();
  }, []);

  // 一篇讲道里的几段接着播：放完一段自动跳到下一段的起点。跳过的地方教授在讲
  // 别的，所以时间轴上会看到断口——看得见就够了，不必打断听。
  function play(structureId: string, occasion: Occasion, index = 0) {
    setPlaying({ structureId, occasion, index });
    setPaused(false);
    window.setTimeout(() => {
      const element = media.current;
      if (element) {
        element.currentTime = occasion.stretches[index].start;
        setAt(occasion.stretches[index].start);
        void element.play();
      }
    }, 0);
  }

  /** 真的暫停，不是把播放器關掉。
   *
   * 原來這顆鈕畫成 `❚❚` 卻執行 `setPlaying(null)`——播放器連同進度一起消失，
   * 按的人以為自己暫停了，回來卻得從頭找。圖示承諾什麼，就要做什麼。
   */
  function toggle(structureId: string, occasion: Occasion) {
    if (playing?.structureId !== structureId || playing?.occasion.source_id !== occasion.source_id) {
      play(structureId, occasion);
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
    const stretch = playing.occasion.stretches[playing.index];
    if (element.currentTime < stretch.end) return;
    const next = playing.index + 1;
    if (next < playing.occasion.stretches.length) {
      element.currentTime = playing.occasion.stretches[next].start;
      setPlaying({ ...playing, index: next });
    } else {
      element.pause();
    }
  }

  if (error) return <p className="px-1 py-10 text-sm text-rose-700">{error}</p>;
  if (!rows) return <p className="px-1 py-10 text-sm text-slate-400">讀取中…</p>;

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-8 px-5 py-10">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">
          王教授講太 16:18-19
        </h1>
        <p className="text-sm leading-relaxed text-slate-600">
          下面是他在這段經文上的幾個判斷。點一行，聽他自己講——不是我們寫的字，是他的原話。
          同一個判斷他在幾篇講道裡都講過，每一遍的理由不同，所以不合併。
        </p>
      </header>

      {rows.map((row) => (
        <section key={row.structure_id} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <h2 className="text-base font-semibold leading-snug text-slate-900">
              {row.central_proposition}
            </h2>
            <p className="font-mono text-[0.7rem] text-slate-400">
              {row.scripture_scope.join("、")} · 他在 {row.occasions.length} 篇講道裡講過
            </p>
          </div>
          <ul className="flex flex-col divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
            {row.occasions.map((occasion) => {
              const open =
                playing?.structureId === row.structure_id &&
                playing?.occasion.source_id === occasion.source_id;
              return (
                <li key={occasion.source_id}>
                  <button
                    type="button"
                    onClick={() => toggle(row.structure_id, occasion)}
                    disabled={!occasion.media_url}
                    className="flex w-full items-baseline gap-3 px-4 py-3 text-left hover:bg-slate-50 disabled:opacity-40"
                  >
                    <span className={open ? "text-indigo-600" : "text-slate-400"}>
                      {open && !paused ? "❚❚" : "▶"}
                    </span>
                    {/* 讀者只需要「哪一堂課、多長」。起點秒數、段數、媒體類型
                        是我調試時要看的，對聽的人沒有用。 */}
                    <span className="flex-1 text-[0.88rem] text-slate-900">{occasion.title}</span>
                    <span className="font-mono text-[0.75rem] text-slate-400">
                      {minutes(occasion.seconds)}
                    </span>
                  </button>

                  {open && occasion.media_url && (
                    // 就地展開。原來是釘在頁面頂端的浮層，點哪一行畫面都往上跳
                    // 一次——聽的人剛按下去就先失去了位置。
                    <div className="flex flex-col gap-2 border-t border-slate-100 bg-slate-50 px-4 py-3">
                      <p className="text-[0.82rem] leading-relaxed text-slate-700">
                        {occasion.saying}
                      </p>
                      {/* 只有录音的讲道，屏幕上放教授此刻在讲的那节经文。有画面
                          的不放——那些本来就有得看。 */}
                      {occasion.media_kind === "audio" && (
                        <ScriptureSlide
                          slug={row.scripture}
                          title={row.central_proposition}
                          caption={`${occasion.title.slice(-6)} · ${clock(
                            open ? at : (occasion.stretches[0]?.start ?? 0),
                          )}`}
                          now={citedAt(
                            occasion,
                            open ? at : (occasion.stretches[0]?.start ?? 0),
                            row.scripture,
                          )}
                        />
                      )}
                      {occasion.media_kind === "video" ? (
                        <video
                          ref={media as React.RefObject<HTMLVideoElement>}
                          src={mediaSrc(occasion.media_url)}
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
                          src={mediaSrc(occasion.media_url)}
                          controls
                          autoPlay
                          onTimeUpdate={onTimeUpdate}
                          onPlay={() => setPaused(false)}
                          onPause={() => setPaused(true)}
                          className="w-full"
                        />
                      )}
                      {occasion.stretches.length > 1 && (
                        // 一堂課裡他把同一件事講了幾次，中間岔去講別的。斷口看
                        // 得見就夠了，不必打斷聽——放完一段自己跳到下一段。
                        <div className="flex items-center gap-1">
                          {occasion.stretches.map((stretch, index) => (
                            <button
                              key={stretch.start}
                              type="button"
                              onClick={() => play(row.structure_id, occasion, index)}
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
        </section>
      ))}
    </main>
  );
}
