"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type RepositoryView = "bible" | "topic";

type ScriptureReference = {
  book: string;
  book_label: string;
  chapter: number;
  verse_start: number;
  end_chapter: number;
  verse_end: number;
  display: string;
};

type PublicArticleSummary = {
  slug: string;
  title: string;
  passage: string;
  scripture: ScriptureReference | null;
  topics: string[];
  href: string;
};

type BibleChapterGroup = {
  chapter: number;
  articles: PublicArticleSummary[];
};

/** 一段有原声的经文。 */
type AudioPassage = {
  passage: string;
  label: string;
  scripture: ScriptureReference;
  sermons: number;
  seconds: number;
  topics: number;
  href: string;
  title: string;
};

type BibleBookGroup = {
  book: string;
  bookLabel: string;
  chapters: BibleChapterGroup[];
};

const BIBLE_BOOK_ORDER = [
  "Gen", "Exod", "Lev", "Num", "Deut", "Josh", "Judg", "Ruth", "1Sam", "2Sam",
  "1Kgs", "2Kgs", "1Chr", "2Chr", "Ezra", "Neh", "Esth", "Job", "Ps", "Prov",
  "Eccl", "Song", "Isa", "Jer", "Lam", "Ezek", "Dan", "Hos", "Joel", "Amos",
  "Obad", "Jonah", "Mic", "Nah", "Hab", "Zeph", "Hag", "Zech", "Mal", "Matt",
  "Mark", "Luke", "John", "Acts", "Rom", "1Cor", "2Cor", "Gal", "Eph", "Phil",
  "Col", "1Thess", "2Thess", "1Tim", "2Tim", "Titus", "Phlm", "Heb", "Jas", "1Pet",
  "2Pet", "1John", "2John", "3John", "Jude", "Rev",
];

const BIBLE_BOOK_RANK = new Map(BIBLE_BOOK_ORDER.map((book, index) => [book, index]));

function groupArticlesByBible(articles: PublicArticleSummary[]): BibleBookGroup[] {
  const books = new Map<string, BibleBookGroup>();
  const sorted = articles
    .filter((article) => article.scripture)
    .sort((left, right) => {
      const leftRef = left.scripture!;
      const rightRef = right.scripture!;
      return (BIBLE_BOOK_RANK.get(leftRef.book) ?? Number.MAX_SAFE_INTEGER)
        - (BIBLE_BOOK_RANK.get(rightRef.book) ?? Number.MAX_SAFE_INTEGER)
        || leftRef.chapter - rightRef.chapter
        || leftRef.verse_start - rightRef.verse_start
        || leftRef.end_chapter - rightRef.end_chapter
        || leftRef.verse_end - rightRef.verse_end;
    });

  for (const article of sorted) {
    const reference = article.scripture!;
    let book = books.get(reference.book);
    if (!book) {
      book = { book: reference.book, bookLabel: reference.book_label, chapters: [] };
      books.set(reference.book, book);
    }
    let chapter = book.chapters.find((item) => item.chapter === reference.chapter);
    if (!chapter) {
      chapter = { chapter: reference.chapter, articles: [] };
      book.chapters.push(chapter);
    }
    chapter.articles.push(article);
  }
  return [...books.values()];
}

function groupArticlesByTopic(articles: PublicArticleSummary[]) {
  const topics = new Map<string, PublicArticleSummary[]>();
  for (const article of articles) {
    for (const topic of article.topics ?? []) {
      const group = topics.get(topic) ?? [];
      group.push(article);
      topics.set(topic, group);
    }
  }
  return [...topics.entries()].sort(([left], [right]) => left.localeCompare(right, "zh-Hant"));
}

function ArticleLink({ article }: { article: PublicArticleSummary }) {
  return (
    <Link
      href={article.href}
      className="group block rounded-2xl border border-stone-200 bg-white px-5 py-5 shadow-sm transition hover:-translate-y-0.5 hover:border-amber-300 hover:shadow-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-amber-700"
    >
      <span className="text-sm font-bold text-amber-800">{article.passage}</span>
      <h4 className="mt-2 text-lg font-bold leading-7 text-stone-950 group-hover:text-amber-900">
        {article.title}
      </h4>
      <span className="mt-3 inline-block text-sm font-semibold text-stone-500 group-hover:text-amber-800">
        閱讀文章 <span aria-hidden="true">→</span>
      </span>
    </Link>
  );
}

/** 一章底下可听的几段原声。
 *
 * 聖經和主題是分类的两个轴，教授的原声和我们写的文章都沿这两个轴分——原声不是
 * 第三个轴，是同一章底下的另一种内容。所以它跟文章排在一起，只是分成两组、各
 * 带标签。
 *
 * 也不做成跟文章一样的卡片：原来两种卡并排，四张深色压着两张浅色，一章底下六
 * 个方块抢戏，读者分不清哪个是读的、哪个是听的。文章是一篇一篇的，原声是一段
 * 一段的，形状不同才看得出是两回事。
 */
function AudioList({ passages }: { passages: AudioPassage[] }) {
  if (passages.length === 0) return null;
  return (
    <div className="rounded-2xl border border-stone-200 bg-white shadow-sm">
      <ul className="flex flex-col divide-y divide-stone-100">
        {passages.map((passage) => (
          <li key={passage.passage}>
            <Link
              href={passage.href}
              className="group flex flex-wrap items-baseline gap-x-4 gap-y-1 px-5 py-3 hover:bg-stone-50"
            >
              <span className="font-mono text-sm font-semibold text-amber-800">
                {passage.label}
              </span>
              <span className="flex-1 font-semibold text-stone-900 group-hover:text-amber-900">
                {passage.title || passage.label}
              </span>
              <span className="text-sm text-stone-500">
                {passage.sermons} 篇 · {Math.round(passage.seconds / 60)} 分 · {passage.topics} 個講題
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function WangRepositoryPage() {
  const [view, setView] = useState<RepositoryView>("bible");
  const [articles, setArticles] = useState<PublicArticleSummary[]>([]);
  const [audio, setAudio] = useState<AudioPassage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/public/wang-articles", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("暫時無法讀取文章目錄。");
        return response.json();
      })
      .then((payload) => setArticles(payload.articles ?? []))
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "暫時無法讀取文章目錄。");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  // 原声单独取一次。它跟文章是两个来源——有的段落只有原声还没有文章，原声可以
  // 先于文章上线。取不到就当没有，落地页照常显示文章，不该因为原声挂了而整页
  // 报错。
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/public/original-audio/passages", { cache: "no-store", signal: controller.signal })
      .then((response) => (response.ok ? response.json() : { passages: [] }))
      .then((payload) => setAudio(payload.passages ?? []))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  const bibleBooks = useMemo(() => groupArticlesByBible(articles), [articles]);
  const audioByChapter = useMemo(() => {
    const map = new Map<string, AudioPassage[]>();
    for (const passage of audio) {
      const key = `${passage.scripture.book}-${passage.scripture.chapter}`;
      map.set(key, [...(map.get(key) ?? []), passage]);
    }
    return map;
  }, [audio]);
  const topicGroups = useMemo(() => groupArticlesByTopic(articles), [articles]);

  return (
    <main className="min-h-screen bg-stone-50">
      <div className="mx-auto max-w-5xl px-5 py-12 sm:px-8 sm:py-16">
        <p className="text-sm font-semibold tracking-wide text-amber-800">達拉斯聖道教會文獻整理計畫</p>
        <h1 className="mt-2 font-serif text-4xl font-bold leading-tight text-stone-950 sm:text-5xl">
          王守仁教授聖經講論文庫
        </h1>
        <p className="mt-4 max-w-3xl text-lg leading-8 text-stone-600">
          按聖經經卷、章節或講論主題尋找正式發布的釋經文章。每篇文章都可完整閱讀，也可隨時切換聆聽相關原聲講解。
        </p>

        <nav className="mt-8 flex flex-wrap gap-3" aria-label="文庫探索方式">
          <button
            type="button"
            aria-pressed={view === "bible"}
            onClick={() => setView("bible")}
            className={`rounded-full px-5 py-2.5 font-semibold transition ${view === "bible" ? "bg-stone-900 text-white" : "bg-white text-stone-700 shadow-sm hover:bg-stone-100"}`}
          >
            聖經目錄
          </button>
          <button
            type="button"
            aria-pressed={view === "topic"}
            onClick={() => setView("topic")}
            className={`rounded-full px-5 py-2.5 font-semibold transition ${view === "topic" ? "bg-stone-900 text-white" : "bg-white text-stone-700 shadow-sm hover:bg-stone-100"}`}
          >
            主題目錄
          </button>
          <Link href="/resources/qa" className="rounded-full bg-white px-5 py-2.5 font-semibold text-stone-700 shadow-sm hover:bg-stone-100">
            信仰問答
          </Link>
        </nav>

        {loading ? (
          <p className="py-16 text-stone-600" role="status">正在讀取文章目錄…</p>
        ) : error ? (
          <div className="mt-12 rounded-2xl border border-red-200 bg-red-50 p-6 text-red-900" role="alert">{error}</div>
        ) : articles.length === 0 ? (
          <div className="mt-12 rounded-2xl border border-amber-200 bg-amber-50 p-6 text-amber-950">
            目前尚無正式發布的文章。
          </div>
        ) : view === "bible" ? (
          <div className="mt-12 space-y-14">
            {bibleBooks.map((book) => (
              <section key={book.book} aria-labelledby={`book-${book.book}`}>
                <p className="text-sm font-bold uppercase tracking-[0.16em] text-amber-800">{book.book}</p>
                <h2 id={`book-${book.book}`} className="mt-1 border-b border-stone-300 pb-4 font-serif text-3xl font-bold text-stone-950">
                  {book.bookLabel}
                </h2>
                <div className="mt-7 space-y-9">
                  {book.chapters.map((chapter) => (
                    <section key={`${book.book}-${chapter.chapter}`} aria-labelledby={`${book.book}-${chapter.chapter}`}>
                      <h3 id={`${book.book}-${chapter.chapter}`} className="text-xl font-bold text-stone-700">第 {chapter.chapter} 章</h3>
                      {chapter.articles.length > 0 && (
                        <>
                          <p className="mt-4 text-sm font-bold text-amber-800">文章</p>
                          <div className="mt-2 grid gap-4 sm:grid-cols-2">
                            {chapter.articles.map((article) => <ArticleLink key={article.slug} article={article} />)}
                          </div>
                        </>
                      )}
                      {(audioByChapter.get(`${book.book}-${chapter.chapter}`) ?? []).length > 0 && (
                        <>
                          {/* 落地页开头写着「每篇文章都可完整閱讀，也可隨時切換
                              聆聽相關原聲講解」，在这之前从这里通不到任何原声。
                              有的段落只有原声还没有文章——原声可以先于文章上线。 */}
                          <p className="mt-6 text-sm font-bold text-amber-800">原聲</p>
                          <div className="mt-2">
                            <AudioList
                              passages={audioByChapter.get(`${book.book}-${chapter.chapter}`) ?? []}
                            />
                          </div>
                        </>
                      )}
                    </section>
                  ))}
                </div>
              </section>
            ))}
          </div>
        ) : topicGroups.length > 0 ? (
          <div className="mt-12 space-y-10">
            {topicGroups.map(([topic, topicArticles]) => (
              <section key={topic} aria-labelledby={`topic-${topic}`}>
                <h2 id={`topic-${topic}`} className="border-b border-stone-300 pb-3 font-serif text-2xl font-bold text-stone-950">{topic}</h2>
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  {topicArticles.map((article) => <ArticleLink key={article.slug} article={article} />)}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <div className="mt-12 rounded-2xl border border-amber-200 bg-amber-50 p-6 text-amber-950">
            已發布文章尚未設定公開主題。
          </div>
        )}
      </div>
    </main>
  );
}
