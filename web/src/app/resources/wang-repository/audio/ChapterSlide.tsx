"use client";

import { useEffect, useState } from "react";
import { load, plain } from "./ScriptureSlide";

/**
 * 按章页面的投影片：模型读保留的逐字稿写的要点，和教授此刻为止读到的经文。
 *
 * 负责人 2026-11-03：投影片要像查经 PPT，呈现他讲的内容，而不是 Claim 的论证结
 * 构；**他讲到、读到的经文必须显示在投影片上。** 要点是编辑整理，不冒充原话；
 * 经文是和合本全文，按出处去取，不经模型。
 */

export type ChapterSlideData = {
  at: number;
  seconds: number;
  title: string;
  points: string[];
  verses: string[];
  scriptures: { at: number; scripture: string; label: string }[];
};

function Verse({ slug, label, latest }: { slug: string; label: string; latest: boolean }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    load(slug).then((data) => {
      if (alive) setText(plain(data.passages.zh));
    });
    return () => {
      alive = false;
    };
  }, [slug]);
  return (
    <div className={`rounded-lg px-4 py-3 ${latest ? "bg-slate-900 ring-1 ring-amber-300/40" : "bg-slate-900/50"}`}>
      <p className="mb-1 font-mono text-[0.7rem] text-amber-300/80">{label}</p>
      <p className={`font-serif leading-8 ${latest ? "text-[1.05rem] text-slate-100 sm:text-lg" : "text-[0.95rem] text-slate-400"}`}>
        {text ?? "……"}
      </p>
    </div>
  );
}

export default function ChapterSlide({ slide, seconds }: { slide: ChapterSlideData; seconds: number }) {
  // 他读到了才出现；同一张里先读的留着，新读的放在最上面、亮着。
  const read = slide.scriptures.filter((item) => item.at <= seconds + 1);
  const shown = [...read].reverse();
  return (
    <section className="flex min-h-[20rem] flex-col gap-5 rounded-xl bg-slate-950 px-5 py-5 text-slate-100 shadow-inner sm:px-7 sm:py-6">
      <header className="border-b border-slate-800 pb-4">
        <p className="mb-1 text-[0.65rem] font-bold uppercase tracking-[0.18em] text-amber-400">
          要點 · 編輯整理
        </p>
        <h2 className="font-serif text-xl font-semibold leading-snug text-white sm:text-2xl">{slide.title}</h2>
        <ul className="mt-3 space-y-2 text-[1rem] leading-relaxed text-slate-200">
          {slide.points.map((point) => (
            <li key={point} className="flex gap-2">
              <span aria-hidden className="mt-[0.65rem] h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400/70" />
              <span>{point}</span>
            </li>
          ))}
        </ul>
      </header>
      {shown.length > 0 && (
        <div className="flex flex-col gap-3">
          {shown.map((item, index) => (
            <Verse key={`${item.at}-${item.scripture}`} slug={item.scripture} label={item.label} latest={index === 0} />
          ))}
        </div>
      )}
    </section>
  );
}
