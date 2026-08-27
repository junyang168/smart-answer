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

/** 中文经节写法：`Matt 16:18-19` → `太 16:18-19`。 */
const CHINESE_BOOK: Record<string, string> = { Matt: "太", Mark: "可", Luke: "路", John: "約", Acts: "徒", Eph: "弗" };
function heading(reference: string) {
  const [book, ...rest] = reference.split(" ");
  return `${CHINESE_BOOK[book] ?? book} ${rest.join(" ")}`;
}

export default function ScriptureSlide({
  slug,
  caption,
}: {
  slug: string;
  caption: string;
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
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-mono text-[0.78rem] tracking-wide text-amber-300">
          {heading(shown.reference)}
        </span>
        <span className="font-mono text-[0.68rem] text-slate-500">{caption}</span>
      </div>
      {zh && <p className="text-[0.95rem] leading-relaxed">{zh}</p>}
      {el && (
        <p className="border-t border-slate-700 pt-3 text-[0.82rem] leading-relaxed text-slate-400">
          {el}
        </p>
      )}
    </div>
  );
}
