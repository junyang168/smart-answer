import Link from "next/link";
import type { SourceCount } from "./navigation";

export type ChapterPage = {
  kind: "chapter" | "passages";
  source: string;
  title?: string;
  media?: "audio" | "video";
  ai_voice?: boolean;
};

export type BookChapter = {
  chapter: number;
  sources: SourceCount[];
  page: ChapterPage | null;
  pages?: ChapterPage[];
  articles: { title: string; href: string }[];
};

type Talk = { title: string; href: string; source: string; scripture: string | null };
type Series = { id: string; source: string; chapters?: number[]; items?: Talk[] };

export type MatthewNavigation = {
  series: Series[];
  related: Series[];
  talks: Talk[];
};

const seriesDetails: Record<string, { title: string; format: string; description: string }> = {
  nysc: {
    title: "紐約靈命進深會马太福音释经",
    format: "教授原聲",
    description: "以講道方式講解馬太福音，穿插生活實例和現場問答。",
  },
  notes: {
    title: "馬太福音釋經課",
    format: "釋經筆記・AI 朗讀",
    description: "按章講解經文，細看篇章結構、原文字義和經文之間的關聯。",
  },
  "matthew-2007": {
    title: "2007 年《馬太福音 24–28 釋經》",
    format: "教授原聲",
    description: "從警醒等候主再來，到耶穌受難、空墳墓與復活。",
  },
};

const sourceNames: Record<string, string> = {
  紐約靈命進深會: seriesDetails.nysc.title,
  教授釋經筆記: seriesDetails.notes.title,
};
const action = "inline-flex min-h-11 w-fit items-center rounded-lg border border-stone-300 bg-white px-3 text-sm font-medium text-stone-900 hover:border-amber-800 hover:text-amber-900";

function chapterRanges(chapters: number[]) {
  const ranges: { first: number; last: number }[] = [];
  for (const chapter of chapters) {
    const prior = ranges.at(-1);
    if (prior && prior.last + 1 === chapter) prior.last = chapter;
    else ranges.push({ first: chapter, last: chapter });
  }
  return ranges.map(({ first, last }) => first === last ? first : `${first}–${last}`).join("、");
}

function Talks({ items, ordered = false }: { items: Talk[]; ordered?: boolean }) {
  const List = ordered ? "ol" : "ul";
  return (
    <List className="divide-y divide-stone-100">
      {items.map((item, i) => (
        <li key={item.href}>
          <Link href={item.href} prefetch={false} className="flex min-h-11 flex-col gap-1 px-4 py-3 hover:bg-stone-50">
            <span className="font-semibold">{ordered ? `第 ${i + 1} 講 · ` : ""}{item.title}</span>
            <span className="text-sm text-stone-600">
              {[item.scripture, !ordered && item.source].filter(Boolean).join(" · ")}
            </span>
          </Link>
        </li>
      ))}
    </List>
  );
}

export function MatthewBook({
  path, source, view, navigation, chapters,
}: {
  path: string;
  source: string;
  view: string;
  navigation: MatthewNavigation;
  chapters: BookChapter[];
}) {
  const selected = navigation.series.find((item) => (
    item.id === "matthew-2007" ? view === item.id : Boolean(source) && item.source === source
  ));
  const details = selected && seriesDetails[selected.id];
  const isHome = !source && !selected && view !== "chapters";
  const displayed = chapters.filter((chapter) => !source || chapter.sources.some((item) => item.name === source));

  return (
    <>
      <header className="space-y-2">
        <h1 className="font-serif text-4xl font-bold leading-tight text-stone-950">馬太福音</h1>
        {!isHome && (
          <Link href={path} className="inline-flex min-h-11 items-center text-sm font-medium text-amber-900 hover:underline">
            ← 返回馬太福音系列
          </Link>
        )}
      </header>

      {isHome ? (
        <>
          <section aria-label="釋經系列" className="grid gap-4 sm:grid-cols-2">
            {navigation.series.map((series) => {
              const info = seriesDetails[series.id];
              return (
                <article key={series.id} className="flex flex-col items-start gap-3 rounded-2xl border border-stone-200 bg-white p-5">
                  <h2 className="font-serif text-xl font-bold text-stone-950">{info.title}</h2>
                  <p className="text-sm font-medium text-amber-900">{info.format}</p>
                  <p className="text-stone-700">{info.description}</p>
                  <p className="text-sm text-stone-600">
                    {series.chapters ? `目前可查第 ${chapterRanges(series.chapters)} 章` : `共 ${series.items?.length} 講`}
                  </p>
                  <Link
                    href={series.chapters ? `${path}?source=${encodeURIComponent(series.source)}` : `${path}?view=${series.id}`}
                    aria-label={`${info.title}，${series.chapters ? "查看章節" : "查看講次"}`}
                    className={`${action} mt-auto`}
                  >
                    {series.chapters ? "查看章節" : "查看講次"}
                  </Link>
                </article>
              );
            })}
          </section>
          <Link href={`${path}?view=chapters`} className="inline-flex min-h-11 w-fit items-center text-sm font-medium text-amber-900 underline underline-offset-4 hover:text-stone-900">
            直接找某一章
          </Link>
          {(navigation.related.length > 0 || navigation.talks.length > 0) && (
            <section className="space-y-3" aria-labelledby="related-talks">
              <h2 id="related-talks" className="font-serif text-2xl font-bold">相關講道</h2>
              {navigation.related.map((group) => (
                <details key={group.id} className="rounded-xl border border-stone-200 bg-white">
                  <summary className="min-h-11 cursor-pointer px-4 py-3 font-semibold">
                    基督再臨 · 達拉斯聖道教會
                    <span className="ml-2 text-sm font-normal text-stone-600">{group.items?.length} 講</span>
                  </summary>
                  <Talks items={group.items ?? []} ordered />
                </details>
              ))}
              {navigation.talks.length > 0 && (
                <details className="rounded-xl border border-stone-200 bg-white">
                  <summary className="min-h-11 cursor-pointer px-4 py-3 font-semibold">其他相關講道</summary>
                  <Talks items={navigation.talks} />
                </details>
              )}
            </section>
          )}
        </>
      ) : (
        <section aria-labelledby="matthew-selection" className="space-y-4">
          <h2 id="matthew-selection" className="font-serif text-2xl font-bold">{details?.title ?? (source || "按章查找")}</h2>
          {details && <p className="text-stone-700">{details.format} · {details.description}</p>}
          {selected?.items ? (
            <>
              <p className="text-sm text-stone-600">教會名稱未記錄</p>
              <div className="rounded-2xl border border-stone-200 bg-white"><Talks items={selected.items} ordered /></div>
            </>
          ) : (
            <ol className="divide-y divide-stone-100 overflow-hidden rounded-2xl border border-stone-200 bg-white">
              {displayed.map((chapter) => (
                <li key={chapter.chapter} className={`px-4 py-3 sm:px-5 ${source
                  ? "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1"
                  : "space-y-3"}`}>
                  <h3 className="font-serif text-lg font-bold">第 {chapter.chapter} 章</h3>
                  {chapter.sources.filter((item) => !source || item.name === source).map((item) => {
                    const page = chapter.pages?.find((candidate) => candidate.source === item.name);
                    const href = `${path}/${chapter.chapter}?source=${encodeURIComponent(item.name)}`;
                    const notes = item.name === "教授釋經筆記";
                    return (
                      <div key={item.name} className={source ? "contents" : "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1"}>
                        {!source && <p className="font-medium">{sourceNames[item.name] ?? item.name}</p>}
                        <div className="col-start-2 row-start-1 flex flex-wrap justify-end gap-2 whitespace-nowrap">
                          {page && <Link href={href} className={action} prefetch={false}>
                            {page.ai_voice ? "AI 朗讀" : page.kind === "passages" ? "按段落聽原聲" : page.media === "video" ? "逐節觀看" : "聆聽原聲"}
                          </Link>}
                          {notes && item.count > 0 && <Link href={`${href}&view=notes`} className={action} prefetch={false}>閱讀筆記</Link>}
                          {!notes && !page && <Link href={href} className={action} prefetch={false}>查看講道</Link>}
                        </div>
                        {page?.title && <p className="col-span-full text-sm text-stone-600">{page.title}</p>}
                      </div>
                    );
                  })}
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </>
  );
}
