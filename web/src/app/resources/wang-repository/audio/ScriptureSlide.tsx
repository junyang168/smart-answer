"use client";

import { useEffect, useState } from "react";

/**
 * 教授此刻在讲的判断，和他正在解的那节经文。
 *
 * 这一页的讲道全是只有音频——点开就是一个光秃秃的播放器，一小时听下来屏幕上什
 * 么都没有。幻灯上放三样：他此刻立的判断、经文、以及他正在讲的那个字的原文。
 *
 * 经文是圣经的字，判断是他自己的话。一个字都不是我们写的。
 */

type Passages = { zh?: string; en?: string; el?: string; he?: string };
/** 教授给经文里的字作的注解：「你是彼得(Petrus)」。 */
export type Gloss = { at: number; context: string; original: string };

/** 取过的经节不再取第二次。
 *
 * 整页只有一段经文，所以实际上只取一次。缓存活在模块作用域，翻到别的讲道再翻
 * 回来也不重打接口。
 */
const cache = new Map<string, Promise<{ reference: string; passages: Passages }>>();

function load(slug: string) {
  const hit = cache.get(slug);
  if (hit) return hit;
  const pending = (async () => {
    const basic = await fetch(`/api/scripture/basic/${slug}`).then((r) => (r.ok ? r.json() : null));
    return {
      reference: basic?.reference ?? "",
      passages: (basic?.passages ?? {}) as Passages,
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

/** 他注解的是经文里的哪个字。
 *
 * 后端给的是括号前那串中文（「我的教會建造在這磐石」），这里取最短的、且出现在
 * 经文里的后缀——「磐石」。取不到就是他在讲经文之外的东西（哈拉卡、通用希臘文、
 * psyche），不显示。
 */
function verseWord(context: string, verse: string) {
  for (let size = 2; size <= Math.min(context.length, 5); size += 1) {
    const tail = context.slice(-size);
    if (verse.includes(tail)) return tail;
  }
  return "";
}

export default function ScriptureSlide({
  slug,
  title,
  gloss,
  cited,
}: {
  slug: string;
  /** 教授此刻立的那个判断。幻灯的抬头。 */
  title: string;
  /** 他此刻正在讲的那个字。没有就不显示原文。 */
  gloss?: Gloss;
  /** 他此刻翻到的别处经文，中文写法（「弗 4:11」）。 */
  cited?: string;
}) {
  const [shown, setShown] = useState<{ reference: string; passages: Passages } | null>(null);

  useEffect(() => {
    // 去重交给 `cache`，不要在这里用 ref 记「上次取的是哪条」：开发模式下
    // StrictMode 会把 effect 跑两次，第一次记下并发请求、清理函数把 `alive`
    // 置 false 丢掉结果，第二次又因为跟上次一样直接返回，幻灯永远不出现。
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
  const verse = plain(shown.passages.zh);
  if (!verse) return null;

  const word = gloss ? verseWord(gloss.context, verse) : "";
  const cut = word ? verse.indexOf(word) : -1;

  return (
    <div className="flex flex-col gap-3 rounded-lg bg-slate-900 px-5 py-4 text-slate-100">
      {/* 幻灯的抬头是他此刻立的判断，不是经节号。
          经节号整页只有一个（页面标题就是「王教授講太 16:18-19」），在每张幻灯
          上再报一次是废话；判断才是这一分钟和下一分钟的区别。 */}
      <p className="text-[0.95rem] font-semibold leading-snug text-amber-300">{title}</p>

      {/* 他讲到经文里的哪个字，就把那个字标出来。原来铺整节的希腊原文——读者不看
          希腊文，铺开只是一堵墙；他在课上真正做的是挑几个字讲。 */}
      <p className="text-[0.9rem] leading-relaxed text-slate-300">
        {cut < 0 ? (
          verse
        ) : (
          <>
            {verse.slice(0, cut)}
            <mark className="rounded bg-amber-300/20 px-0.5 text-amber-200">{word}</mark>
            {verse.slice(cut + word.length)}
          </>
        )}
      </p>

      {/* 底下这一行两件事共用，实际上互斥：他要么在拆这节经文里的一个字，要么
          翻到别处去了。都没有就不占地方。 */}
      {word && gloss ? (
        <p className="border-t border-slate-700 pt-3 font-mono text-[0.82rem] text-slate-400">
          <span className="text-amber-200">{word}</span>
          <span className="px-2 text-slate-600">·</span>
          {gloss.original}
        </p>
      ) : cited ? (
        <p className="border-t border-slate-700 pt-3 font-mono text-[0.7rem] text-slate-500">
          他此刻在念 {cited}
        </p>
      ) : null}
    </div>
  );
}
