"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type RepositoryView = "bible" | "topic";

type BibleReference = {
  osis: string;
  display: string;
};

type RepositoryUnit = {
  unit_id: string;
  title: string;
  primary_bible_refs?: BibleReference[];
};

type BibleUnitCard = {
  unit: RepositoryUnit;
  indexReferences: BibleReference[];
  anchor: ParsedBibleReference;
};

type ParsedBibleReference = {
  book: string;
  bookOrder: number;
  bookLabel: string;
  chapter: number;
  verse: number;
  endChapter: number;
  endVerse: number;
};

type BibleChapterGroup = {
  chapter: number;
  units: BibleUnitCard[];
};

type BibleBookGroup = {
  book: string;
  bookOrder: number;
  bookLabel: string;
  chapters: BibleChapterGroup[];
};

const BIBLE_BOOKS: Array<[string, string]> = [
  ["Gen", "創世記"], ["Exod", "出埃及記"], ["Lev", "利未記"], ["Num", "民數記"], ["Deut", "申命記"],
  ["Josh", "約書亞記"], ["Judg", "士師記"], ["Ruth", "路得記"], ["1Sam", "撒母耳記上"], ["2Sam", "撒母耳記下"],
  ["1Kgs", "列王紀上"], ["2Kgs", "列王紀下"], ["1Chr", "歷代志上"], ["2Chr", "歷代志下"], ["Ezra", "以斯拉記"],
  ["Neh", "尼希米記"], ["Esth", "以斯帖記"], ["Job", "約伯記"], ["Ps", "詩篇"], ["Prov", "箴言"],
  ["Eccl", "傳道書"], ["Song", "雅歌"], ["Isa", "以賽亞書"], ["Jer", "耶利米書"], ["Lam", "耶利米哀歌"],
  ["Ezek", "以西結書"], ["Dan", "但以理書"], ["Hos", "何西阿書"], ["Joel", "約珥書"], ["Amos", "阿摩司書"],
  ["Obad", "俄巴底亞書"], ["Jonah", "約拿書"], ["Mic", "彌迦書"], ["Nah", "那鴻書"], ["Hab", "哈巴谷書"],
  ["Zeph", "西番雅書"], ["Hag", "哈該書"], ["Zech", "撒迦利亞書"], ["Mal", "瑪拉基書"],
  ["Matt", "馬太福音"], ["Mark", "馬可福音"], ["Luke", "路加福音"], ["John", "約翰福音"], ["Acts", "使徒行傳"],
  ["Rom", "羅馬書"], ["1Cor", "哥林多前書"], ["2Cor", "哥林多後書"], ["Gal", "加拉太書"], ["Eph", "以弗所書"],
  ["Phil", "腓立比書"], ["Col", "歌羅西書"], ["1Thess", "帖撒羅尼迦前書"], ["2Thess", "帖撒羅尼迦後書"],
  ["1Tim", "提摩太前書"], ["2Tim", "提摩太後書"], ["Titus", "提多書"], ["Phlm", "腓利門書"], ["Heb", "希伯來書"],
  ["Jas", "雅各書"], ["1Pet", "彼得前書"], ["2Pet", "彼得後書"], ["1John", "約翰一書"], ["2John", "約翰二書"],
  ["3John", "約翰三書"], ["Jude", "猶大書"], ["Rev", "啟示錄"],
];

const BIBLE_BOOK_META = new Map(
  BIBLE_BOOKS.map(([osis, label], index) => [osis, { order: index, label }]),
);

function parseBibleReference(reference: BibleReference): ParsedBibleReference {
  const match = reference.osis.match(/^([1-3]?[A-Za-z]+)\.(\d+)(?:\.(\d+))?(?:-([1-3]?[A-Za-z]+)\.(\d+)(?:\.(\d+))?)?$/);
  const book = match?.[1] ?? reference.osis;
  const chapter = Number(match?.[2] ?? 0);
  const verse = Number(match?.[3] ?? 0);
  const endChapter = Number(match?.[5] ?? chapter);
  const endVerse = Number(match?.[6] ?? verse);
  const metadata = BIBLE_BOOK_META.get(book);
  return {
    book,
    bookOrder: metadata?.order ?? Number.MAX_SAFE_INTEGER,
    bookLabel: metadata?.label ?? book,
    chapter,
    verse,
    endChapter,
    endVerse,
  };
}

function uniqueBibleUnits(references: Record<string, RepositoryUnit[]>): BibleUnitCard[] {
  const cards = new Map<string, BibleUnitCard>();
  for (const [osis, units] of Object.entries(references)) {
    for (const unit of units) {
      const existing = cards.get(unit.unit_id);
      if (existing) {
        if (!existing.indexReferences.some((reference) => reference.osis === osis)) {
          existing.indexReferences.push({ osis, display: osis });
        }
        continue;
      }
      const primaryReferences = unit.primary_bible_refs?.length
        ? unit.primary_bible_refs
        : [{ osis, display: osis }];
      cards.set(unit.unit_id, {
        unit,
        indexReferences: [...primaryReferences],
        anchor: parseBibleReference(primaryReferences[0]),
      });
    }
  }
  return [...cards.values()];
}

function groupBibleUnits(cards: BibleUnitCard[]): BibleBookGroup[] {
  const sorted = [...cards].sort((left, right) =>
    left.anchor.bookOrder - right.anchor.bookOrder
    || left.anchor.chapter - right.anchor.chapter
    || left.anchor.verse - right.anchor.verse
    || left.anchor.endChapter - right.anchor.endChapter
    || left.anchor.endVerse - right.anchor.endVerse
    || left.unit.title.localeCompare(right.unit.title, "zh-Hant"),
  );
  const books = new Map<string, BibleBookGroup>();
  for (const card of sorted) {
    let book = books.get(card.anchor.book);
    if (!book) {
      book = {
        book: card.anchor.book,
        bookOrder: card.anchor.bookOrder,
        bookLabel: card.anchor.bookLabel,
        chapters: [],
      };
      books.set(card.anchor.book, book);
    }
    let chapter = book.chapters.find((item) => item.chapter === card.anchor.chapter);
    if (!chapter) {
      chapter = { chapter: card.anchor.chapter, units: [] };
      book.chapters.push(chapter);
    }
    chapter.units.push(card);
  }
  return [...books.values()];
}

export default function WangRepositoryPage() {
  const [view, setView] = useState<RepositoryView>("bible");
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    setData(null);
    fetch(`/api/canonical-repository/${view === "bible" ? "bible-index" : "topic-index"}`, {
      cache: "no-store",
    })
      .then((response) => response.json())
      .then(setData);
  }, [view]);

  const bibleUnits = useMemo(
    () => uniqueBibleUnits(data?.references ?? {}),
    [data?.references],
  );
  const bibleBooks = useMemo(() => groupBibleUnits(bibleUnits), [bibleUnits]);
  const unavailable = data?.available === false;

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="text-sm font-semibold text-sky-700">達拉斯聖道教會文獻整理計畫</p>
      <h1 className="mt-1 text-4xl font-bold">王守仁教授釋經與專題講論文庫</h1>
      <p className="mt-3 max-w-3xl text-slate-600">
        按聖經經卷與講論專題，整理王守仁教授在不同時期、不同場合的釋經內容。
      </p>
      <div className="mt-6 flex gap-3">
        <button
          onClick={() => setView("bible")}
          className={`rounded-lg px-4 py-2 font-semibold ${view === "bible" ? "bg-sky-600 text-white" : "bg-slate-100"}`}
        >
          聖經目錄
        </button>
        <button
          onClick={() => setView("topic")}
          className={`rounded-lg px-4 py-2 font-semibold ${view === "topic" ? "bg-sky-600 text-white" : "bg-slate-100"}`}
        >
          主題目錄
        </button>
      </div>

      {!data ? (
        <p className="py-12">讀取中…</p>
      ) : unavailable ? (
        <div className="mt-8 rounded-xl border border-amber-200 bg-amber-50 p-6">
          <h2 className="font-bold text-amber-900">文庫尚未正式發布</h2>
          <p className="mt-2 text-amber-800">候選單元仍在人工審閱；公開頁不會顯示未批准內容。</p>
        </div>
      ) : view === "bible" ? (
        <div className="mt-8 space-y-10">
          {bibleBooks.map((book) => (
            <section key={book.book}>
              <div className="border-b-2 border-sky-600 pb-3">
                <p className="text-sm font-semibold uppercase tracking-wide text-sky-700">{book.book}</p>
                <h2 className="text-3xl font-bold text-slate-900">{book.bookLabel}</h2>
              </div>
              <div className="mt-5 space-y-7">
                {book.chapters.map((chapter) => (
                  <section key={`${book.book}-${chapter.chapter}`}>
                    <h3 className="text-xl font-bold text-slate-700">第 {chapter.chapter} 章</h3>
                    <div className="mt-3 space-y-3">
                      {chapter.units.map(({ unit, indexReferences }) => (
                        <article key={unit.unit_id} className="rounded-xl border bg-white p-5">
                          <div className="flex flex-wrap gap-2">
                            {indexReferences.map((reference, index) => (
                              <span
                                key={reference.osis}
                                className={`rounded px-2 py-1 text-sm font-semibold ${index === 0 ? "bg-sky-100 text-sky-900" : "bg-slate-100 text-slate-600"}`}
                              >
                                {reference.display}
                              </span>
                            ))}
                          </div>
                          <Link
                            href={`/resources/wang-repository/${unit.unit_id}`}
                            className="mt-3 block font-semibold hover:underline"
                          >
                            {unit.title}
                          </Link>
                        </article>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="mt-8 space-y-5">
          {(data.topics ?? []).map((topic: any) => (
            <section key={topic.topic_id} className="rounded-xl border bg-white p-5">
              <h2 className="font-bold">{topic.path.join(" › ")}</h2>
              {topic.units.map((unit: RepositoryUnit) => (
                <Link
                  key={unit.unit_id}
                  href={`/resources/wang-repository/${unit.unit_id}`}
                  className="mt-3 block text-indigo-700 hover:underline"
                >
                  {unit.title}
                </Link>
              ))}
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
