"use client";

import { useEffect, useState } from "react";

/**
 * 教授此刻在讲的判断，和他正在解的那节经文。
 *
 * 这一页的讲道全是只有音频——点开就是一个光秃秃的播放器，一小时听下来屏幕上什
 * 么都没有。幻灯上放三样：根据 Claim 整理的讲论要点、他正在解的经文、以及逐字
 * 稿明确记下的原文讲解。
 *
 * 讲论要点是规范化后的 Claim，不冒充逐字引语；原文讲解则保留教授原话供展开核
 * 对。两种文字在视觉上必须分清楚。
 */

type Passages = { zh?: string; en?: string; el?: string; he?: string };
/** 逐字稿中明确出现的原文讲解，不包含系统推断的 lemma 或 morphology。 */
export type OriginalLanguageEvent = {
  at: number;
  context: string;
  original: string;
  greek: string;
  transcript_excerpt: string;
  transcript_span: { start: number; end: number };
  source_kind: "transcript_explicit";
};

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

/** 经文切成一段段，讲过的字标出来。
 *
 * 讲过的字都留着，不是后一个把前一个顶掉——他讲课就是一个个字累起来的：先说
 * 「你是彼得(Petrus)」，再说「這磐石(petra)」，两个字要摆在一起才看得出他在
 * 比什么。
 */
function slice(verse: string, words: string[]) {
  const spans: Array<[number, number]> = [];
  for (const word of words) {
    let from = verse.indexOf(word);
    while (from >= 0) {
      spans.push([from, from + word.length]);
      from = verse.indexOf(word, from + word.length);
    }
  }
  spans.sort((a, b) => a[0] - b[0]);
  const out: Array<{ text: string; marked: boolean }> = [];
  let cursor = 0;
  for (const [from, to] of spans) {
    if (from < cursor) continue;
    if (from > cursor) out.push({ text: verse.slice(cursor, from), marked: false });
    out.push({ text: verse.slice(from, to), marked: true });
    cursor = to;
  }
  if (cursor < verse.length) out.push({ text: verse.slice(cursor), marked: false });
  return out;
}

export default function ScriptureSlide({
  slug,
  title,
  originalLanguage,
  cited,
}: {
  slug: string;
  /** 由来源绑定 Claim 规范化出的讲论要点，不是逐字引语。 */
  title: string;
  /** 当前讲论要点里、到此刻为止教授明确讲过的原文。 */
  originalLanguage: OriginalLanguageEvent[];
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

  const said: Array<{ word: string; original: string }> = [];
  for (const item of originalLanguage) {
    const word = verseWord(item.context, verse);
    // 同一个字他会讲好几遍（「你是彼得」在（四）1 里说了三次），列一次就够。
    if (word && !said.some((x) => x.word === word)) {
      said.push({ word, original: item.original });
    }
  }
  const parts = slice(verse, said.map((x) => x.word));
  const language: OriginalLanguageEvent[] = [];
  for (const item of originalLanguage) {
    const key = `${item.greek}|${item.original}`.toLowerCase();
    if (!language.some((seen) => `${seen.greek}|${seen.original}`.toLowerCase() === key)) {
      language.push(item);
    }
  }

  return (
    <section className="flex min-h-[20rem] flex-col gap-5 rounded-xl bg-slate-950 px-5 py-5 text-slate-100 shadow-inner sm:px-7 sm:py-6">
      <header className="border-b border-slate-800 pb-4">
        <p className="mb-1 text-[0.65rem] font-bold uppercase tracking-[0.18em] text-amber-400">
          講論要點 · 根據來源 Claim 整理
        </p>
        <h2 className="font-serif text-xl font-semibold leading-snug text-white sm:text-2xl">
          {title}
        </h2>
      </header>

      {/* 他讲到经文里的哪个字，就把那个字标出来。原来铺整节的希腊原文——读者不看
          希腊文，铺开只是一堵墙；他在课上真正做的是挑几个字讲。 */}
      <div>
        <p className="mb-2 font-mono text-[0.7rem] text-slate-500">{shown.reference}</p>
        <p className="font-serif text-[1.05rem] leading-8 text-slate-200 sm:text-lg">
          {parts.map((part, index) =>
            part.marked ? (
              <mark
                key={index}
                className="rounded bg-amber-300/20 px-0.5 text-amber-100"
              >
                {part.text}
              </mark>
            ) : (
              <span key={index}>{part.text}</span>
            ),
          )}
        </p>
      </div>

      {language.length > 0 && (
        <div className="rounded-lg border border-amber-300/20 bg-amber-300/[0.06] p-4">
          <p className="mb-3 text-[0.68rem] font-bold uppercase tracking-[0.16em] text-amber-300">
            原文講解 · 逐字稿明載
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {language.map((item) => {
              const word = verseWord(item.context, verse);
              return (
                <article
                  key={`${item.at}-${item.greek}-${item.original}`}
                  className="rounded-md bg-slate-900/80 px-4 py-3"
                >
                  {word && <p className="mb-1 text-xs text-slate-500">經文中的「{word}」</p>}
                  <p className="font-serif text-xl text-amber-100">
                    {item.greek || item.original}
                  </p>
                  {item.greek && item.original !== item.greek && (
                    <p className="mt-1 font-mono text-xs text-slate-400">{item.original}</p>
                  )}
                  <details className="mt-3 text-xs leading-relaxed text-slate-400">
                    <summary className="cursor-pointer select-none text-slate-500 hover:text-slate-300">
                      查看教授原話
                    </summary>
                    <blockquote className="mt-2 border-l border-amber-300/30 pl-3">
                      {item.transcript_excerpt}
                    </blockquote>
                  </details>
                </article>
              );
            })}
          </div>
        </div>
      )}

      {/* 他翻到这段经文之外时说一声，不然幻灯上摆着马太、耳朵里听的是以弗所
          书，读者不知道为什么。 */}
      {cited && (
        <p className="font-mono text-[0.7rem] text-slate-500">他此刻在念 {cited}</p>
      )}
    </section>
  );
}
