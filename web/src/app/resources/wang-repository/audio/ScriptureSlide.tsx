"use client";

import { useEffect, useState } from "react";

/**
 * 教授此刻在讲的那节经文。
 *
 * 这一页的五篇讲道全是只有音频——点开就是一个光秃秃的播放器，一小时听下来屏幕
 * 上什么都没有。教授讲课本来就常写希腊原文（Petrus／Petra、ἔσται δεδεμένον、
 * φρονέω），把经文和原文摆出来，等于把他的白板还原。
 *
 * 幻灯上只有圣经经文，一个字都不是我们写的。
 */

type Passages = { zh?: string; en?: string; el?: string; he?: string };

/** 取过的经节不再取第二次。
 *
 * 同一节在一次播放里会反复出现——五个中心观点里有 23 段都在讲太16:19。缓存活在
 * 模块作用域，翻到别的观点再翻回来也不重打接口。
 */
const cache = new Map<string, Promise<{ reference: string; passages: Passages }>>();

function load(slug: string) {
  const hit = cache.get(slug);
  if (hit) return hit;
  const pending = (async () => {
    const [basic, original] = await Promise.all([
      fetch(`/api/scripture/basic/${slug}`).then((r) => (r.ok ? r.json() : null)),
      fetch(`/api/scripture/original/${slug}`).then((r) => (r.ok ? r.json() : null)),
    ]);
    return {
      reference: basic?.reference ?? original?.reference ?? "",
      passages: { ...(basic?.passages ?? {}), ...(original?.passages ?? {}) } as Passages,
    };
  })();
  cache.set(slug, pending);
  return pending;
}

/** 接口回的是 HTML 片段（`<p>`、`<br/>`、`[18]` 这样的节号）。幻灯只要字。 */
function plain(html: string | undefined) {
  if (!html) return "";
  return html
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<[^>]*>/g, "")
    .replace(/\[(\d+)\]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export default function ScriptureSlide({
  slug,
  title,
  now,
}: {
  slug: string;
  /** 教授此刻立的那个判断。幻灯的抬头。 */
  title: string;
  /** 他此刻念到的经文，中文写法（「弗 4:11」）。旁证，只占一行小字。 */
  now?: string;
}) {
  const [shown, setShown] = useState<{ reference: string; passages: Passages } | null>(null);

  useEffect(() => {
    // slug 为空就什么都不做，让上一张留在屏幕上。取不出经节的观点（`聖經`、
    // `詩篇` 这类不是节级引用的 scope，全库 170 条里有 13 条）走的就是这条路，
    // 幻灯保持不动，不闪成空白。
    //
    // 去重交给 `cache`，不要在这里用 ref 记「上次取的是哪条」：开发模式下
    // StrictMode 会把 effect 跑两次，第一次记下 slug 并发请求、清理函数把
    // `alive` 置 false 丢掉结果，第二次又因为「跟上次一样」直接返回——幻灯永远
    // 不出现。
    if (!slug) return;
    let alive = true;
    load(slug).then((data) => {
      if (alive) setShown(data);
    });
    return () => {
      alive = false;
    };
  }, [slug]);

  if (!shown) return null;
  const zh = plain(shown.passages.zh);
  const el = plain(shown.passages.el);
  if (!zh && !el) return null;

  return (
    <div className="flex flex-col gap-3 rounded-lg bg-slate-900 px-5 py-4 text-slate-100">
      {/* 幻灯上只有两样：他此刻立的判断，和他正在解的经文。
          经节号不报——整页就一段经文，页面标题已经写着「王教授講太 16:18-19」。
          讲道名和播放时间也不报——讲道名在幻灯上一行，时间在下一行的播放器里。 */}
      <p className="text-[0.95rem] font-semibold leading-snug text-amber-300">{title}</p>
      {zh && <p className="text-[0.9rem] leading-relaxed text-slate-300">{zh}</p>}
      {el && (
        <p className="border-t border-slate-700 pt-3 text-[0.8rem] leading-relaxed text-slate-400">
          {el}
        </p>
      )}
      {/* 他此刻翻到的旁证（弗2:20、约20:23…）。只报节号不铺经文——铺开就成了
          另一张幻灯，他正在拆的那句字反而被挤下去。 */}
      {now && (
        <p className="font-mono text-[0.68rem] text-slate-500">他此刻在念 {now}</p>
      )}
    </div>
  );
}
