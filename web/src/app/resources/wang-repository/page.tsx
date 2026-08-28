"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type RepositoryView = "bible" | "topic";
/** 看的是哪一种内容。
 *
 * 跟 `RepositoryView` 是两回事：聖經和主題是**分类的轴**，綜合文章和教授原聲是
 * **内容的种类**。同一段经文两样都有，各占一个 tab，不上下堆着。
 */
type RepositoryKind = "article" | "audio";

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
      className="group flex flex-wrap items-baseline gap-x-4 gap-y-1 px-5 py-3.5 hover:bg-stone-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-amber-700"
    >
      <span className="font-mono text-sm font-semibold text-amber-800">{article.passage}</span>
      <span className="flex-1 font-semibold text-stone-900 group-hover:text-amber-900">
        {article.title}
      </span>
      <span className="text-sm text-stone-500 group-hover:text-amber-800">
        閱讀 <span aria-hidden="true">→</span>
      </span>
    </Link>
  );
}

/** 一章底下的一组内容——綜合文章或教授原聲。
 *
 * 两组同一个形状。它们的信息结构一样：经节、标题、一行说明。差别由上面的标签
 * 说清楚，不必再靠形状区分。
 *
 * 用行不用卡：一章将来会有更多篇文章、更多段原声，行排得下，卡片一章六个就开
 * 始抢戏。
 */
function Rows({ children }: { children: React.ReactNode }) {
  return (
    <ul className="mt-2 flex flex-col divide-y divide-stone-100 overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm">
      {children}
    </ul>
  );
}

/** 一段可听的原声。 */
function AudioLink({ passage }: { passage: AudioPassage }) {
  return (
    <Link
      href={passage.href}
      className="group flex flex-wrap items-baseline gap-x-4 gap-y-1 px-5 py-3.5 hover:bg-stone-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-amber-700"
    >
      <span className="font-mono text-sm font-semibold text-amber-800">{passage.label}</span>
      <span className="flex-1 font-semibold text-stone-900 group-hover:text-amber-900">
        {passage.title || passage.label}
      </span>
      <span className="text-sm text-stone-500">
        {passage.sermons} 篇 · {Math.round(passage.seconds / 60)} 分 · {passage.topics} 個講題
      </span>
    </Link>
  );
}

export default function WangRepositoryPage() {
  const [view, setView] = useState<RepositoryView>("bible");
  const [kind, setKind] = useState<RepositoryKind>("article");
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

  // 章节树按当前看的内容建。
  //
  // 原来只从文章建，于是只有原声还没有文章的段落（太16:21-23、16:24-27）整章都
  // 不出现——而原声可以先于文章上线，正是这一层要显示的东西。
  const bibleBooks = useMemo(
    () =>
      kind === "article"
        ? groupArticlesByBible(articles)
        : groupArticlesByBible(
            audio.map((passage) => ({
              slug: passage.passage,
              title: passage.title || passage.label,
              passage: passage.label,
              scripture: passage.scripture,
              topics: [],
              href: passage.href,
            })),
          ),
    [articles, audio, kind],
  );
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
        <h1 className="font-serif text-4xl font-bold leading-tight text-stone-950 sm:text-5xl">
          王守仁教授聖經講論文庫
        </h1>
        <p className="mt-4 max-w-2xl text-lg leading-8 text-stone-600">
          教授講道的原聲，與從中整理出的釋經和專題文章。
        </p>

        {/* 先問「讀還是聽」，再問「怎麼排」。
         *
         * 读者进来要答三个问题：读还是听、怎么排、哪段经文。原来把「哪个目录」
         * 做成实心药丸摆在最上，把「读还是听」做成最轻的下划线摆在下面——最常
         * 切的那个做得最轻，两排控件叠着又看不出关系。
         *
         * 「信仰問答」原来混在两个「目录」旁边，长得一模一样，读者会当成第三种
         * 目录；它其实是去别的页面，挪到下面单独放。
         */}
        <nav className="mt-8 flex flex-wrap gap-3" aria-label="內容種類">
          {([
            ["article", "綜合文章"],
            ["audio", "教授原聲"],
          ] as const).map(([value, text]) => (
            <button
              key={value}
              type="button"
              aria-pressed={kind === value}
              onClick={() => setKind(value)}
              className={`rounded-full px-5 py-2.5 font-semibold transition ${
                kind === value
                  ? "bg-stone-900 text-white"
                  : "bg-white text-stone-700 shadow-sm hover:bg-stone-100"
              }`}
            >
              {text}
            </button>
          ))}
        </nav>

        <nav className="mt-4 flex flex-wrap items-baseline gap-5" aria-label="排列方式">
          <span className="text-sm text-stone-500">排列方式</span>
          {([
            ["bible", "按經卷章節"],
            ["topic", "按講論主題"],
          ] as const).map(([value, text]) => (
            <button
              key={value}
              type="button"
              aria-pressed={view === value}
              onClick={() => setView(value)}
              className={`text-sm font-semibold underline-offset-4 transition ${
                view === value
                  ? "text-amber-900 underline decoration-amber-700 decoration-2"
                  : "text-stone-500 hover:text-stone-800"
              }`}
            >
              {text}
            </button>
          ))}
        </nav>

        {loading ? (
          <p className="py-16 text-stone-600" role="status">正在讀取文章目錄…</p>
        ) : error ? (
          <div className="mt-12 rounded-2xl border border-red-200 bg-red-50 p-6 text-red-900" role="alert">{error}</div>
        ) : (kind === "article" ? articles.length : audio.length) === 0 ? (
          <div className="mt-12 rounded-2xl border border-amber-200 bg-amber-50 p-6 text-amber-950">
            {kind === "article" ? "目前尚無正式發布的文章。" : "目前尚無可聽的原聲。"}
          </div>
        ) : view === "bible" ? (
          <div className="mt-12 space-y-14">
            {bibleBooks.map((book) => (
              <section key={book.book} aria-labelledby={`book-${book.book}`}>
                <h2 id={`book-${book.book}`} className="border-b border-stone-300 pb-4 font-serif text-3xl font-bold text-stone-950">
                  {book.bookLabel}
                </h2>
                <div className="mt-7 space-y-9">
                  {book.chapters.map((chapter) => (
                    <section key={`${book.book}-${chapter.chapter}`} aria-labelledby={`${book.book}-${chapter.chapter}`}>
                      <h3 id={`${book.book}-${chapter.chapter}`} className="text-lg font-bold text-stone-700">
                        第 {chapter.chapter} 章
                      </h3>
                      {/* 落地页开头写着「每篇文章都可完整閱讀，也可隨時切換聆聽
                          相關原聲講解」，在这之前从这里通不到任何原声。有的段落
                          只有原声还没有文章——原声可以先于文章上线。 */}
                      <Rows>
                        {kind === "article"
                          ? chapter.articles.map((article) => (
                              <li key={article.slug}>
                                <ArticleLink article={article} />
                              </li>
                            ))
                          : (audioByChapter.get(`${book.book}-${chapter.chapter}`) ?? []).map(
                              (passage) => (
                                <li key={passage.passage}>
                                  <AudioLink passage={passage} />
                                </li>
                              ),
                            )}
                      </Rows>
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
                <Rows>
                  {topicArticles.map((article) => (
                    <li key={article.slug}>
                      <ArticleLink article={article} />
                    </li>
                  ))}
                </Rows>
              </section>
            ))}
          </div>
        ) : (
          <div className="mt-12 rounded-2xl border border-amber-200 bg-amber-50 p-6 text-amber-950">
            已發布文章尚未設定公開主題。
          </div>
        )}
        <p className="mt-14 border-t border-stone-200 pt-6 text-sm text-stone-500">
          找不到想問的？
          <Link href="/resources/qa" className="ml-2 font-semibold text-amber-800 hover:text-amber-900">
            信仰問答 <span aria-hidden="true">→</span>
          </Link>
        </p>
      </div>
    </main>
  );
}
