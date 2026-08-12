import Link from 'next/link';

import { Sermon } from '@/app/interfaces/article';
import { getBookOrderIndex } from '@/app/utils/bible-order';

type ScriptureGroup = {
  book: string;
  chapters: Array<{ chapter: number; sermons: Sermon[]; relatedSermons: Sermon[] }>;
};

const sermonPassageOrder = (sermon: Sermon) => {
  const passage = sermon.catalog_primary_passage;
  return [passage?.verse_start ?? 0, passage?.verse_end ?? passage?.verse_start ?? 0, sermon.title] as const;
};

export const ScriptureSermonCatalog = ({ sermons }: { sermons: Sermon[] }) => {
  const byBook = new Map<string, Map<number, Sermon[]>>();
  const relatedByBook = new Map<string, Map<number, Sermon[]>>();

  for (const sermon of sermons) {
    const passage = sermon.catalog_primary_passage;
    if (!passage?.book || !passage.chapter) continue;
    const chapters = byBook.get(passage.book) ?? new Map<number, Sermon[]>();
    const chapterSermons = chapters.get(passage.chapter) ?? [];
    chapterSermons.push(sermon);
    chapters.set(passage.chapter, chapterSermons);
    byBook.set(passage.book, chapters);

    for (const relatedPassage of sermon.substantial_passages ?? []) {
      const relatedBook = relatedPassage.book;
      const relatedChapter = relatedPassage.chapter;
      if (!relatedBook || !relatedChapter) continue;
      if (relatedBook === passage.book && relatedChapter === passage.chapter) continue;
      const relatedChapters = relatedByBook.get(relatedBook) ?? new Map<number, Sermon[]>();
      const relatedSermons = relatedChapters.get(relatedChapter) ?? [];
      if (!relatedSermons.some(item => item.id === sermon.id)) relatedSermons.push(sermon);
      relatedChapters.set(relatedChapter, relatedSermons);
      relatedByBook.set(relatedBook, relatedChapters);
    }
  }

  const allBooks = new Set([...byBook.keys(), ...relatedByBook.keys()]);
  const groups: ScriptureGroup[] = [...allBooks]
    .sort((bookA, bookB) => getBookOrderIndex(bookA) - getBookOrderIndex(bookB))
    .map(book => {
      const chapterMap = byBook.get(book) ?? new Map<number, Sermon[]>();
      const relatedChapterMap = relatedByBook.get(book) ?? new Map<number, Sermon[]>();
      const allChapters = new Set([...chapterMap.keys(), ...relatedChapterMap.keys()]);
      return {
        book,
        chapters: [...allChapters]
          .sort((chapterA, chapterB) => chapterA - chapterB)
          .map(chapter => ({
            chapter,
            sermons: (chapterMap.get(chapter) ?? []).sort((a, b) => {
              const [aStart, aEnd, aTitle] = sermonPassageOrder(a);
              const [bStart, bEnd, bTitle] = sermonPassageOrder(b);
              return aStart - bStart || aEnd - bEnd || aTitle.localeCompare(bTitle, 'zh-Hant');
            }),
            relatedSermons: relatedChapterMap.get(chapter) ?? [],
          })),
      };
    });

  if (groups.length === 0) {
    return <div className="rounded-xl bg-white py-16 text-center shadow-sm">沒有可編入聖經目錄的釋經講道</div>;
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {groups.map(group => {
          const primarySermonIds = new Set(group.chapters.flatMap(chapter => chapter.sermons.map(sermon => sermon.id)));
          const relatedSermonIds = new Set(group.chapters.flatMap(chapter => chapter.relatedSermons.map(sermon => sermon.id)));
          return (
            <details
              key={group.book}
              className="group overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm open:col-span-full open:border-sky-300 open:shadow-md"
            >
          <summary className="flex cursor-pointer list-none items-center justify-between gap-5 px-6 py-6 marker:hidden">
            <div>
              <h2 className="text-3xl font-bold text-slate-900">{group.book}</h2>
              <p className="mt-2 text-sm text-slate-500">
                涵蓋 {group.chapters.length} 章 · {primarySermonIds.size} 篇講道
                {relatedSermonIds.size > 0 ? ` · ${relatedSermonIds.size} 篇相關講論` : ''}
              </p>
            </div>
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xl font-bold text-slate-600 transition group-open:rotate-180 group-open:bg-sky-100 group-open:text-sky-700">
              ↓
            </span>
          </summary>
          <div className="space-y-10 border-t border-slate-200 bg-slate-50 px-6 py-8">
            {group.chapters.map(chapter => (
              <section key={`${group.book}-${chapter.chapter}`}>
                <h3 className="mb-4 text-2xl font-bold text-slate-700">第 {chapter.chapter} 章</h3>
                <div className="space-y-4">
                  {chapter.sermons.map(sermon => (
                    <article
                      key={sermon.id}
                      className="rounded-xl border border-slate-200 bg-white px-6 py-5 shadow-sm transition hover:border-sky-300 hover:shadow-md"
                    >
                      <div className="flex flex-wrap gap-2">
                        <span className="rounded-md bg-sky-100 px-3 py-1 text-sm font-bold text-sky-800">
                          {sermon.catalog_primary_passage?.display}
                        </span>
                        {(sermon.substantial_passages ?? []).map(ref => (
                          <span key={ref.osis} className="rounded-md bg-amber-50 px-3 py-1 text-sm font-semibold text-amber-800">
                            重點展開：{ref.display}
                          </span>
                        ))}
                        {(sermon.supporting_passages ?? [])
                          .slice(0, Math.max(0, 2 - (sermon.substantial_passages?.length ?? 0)))
                          .map(ref => (
                            <span key={ref.osis} className="rounded-md bg-slate-100 px-3 py-1 text-sm font-semibold text-slate-600">
                              {ref.display}
                            </span>
                          ))}
                      </div>
                      <Link href={`/resources/sermons/${encodeURIComponent(sermon.id)}`} className="group block">
                        <h4 className="mt-4 text-xl font-bold text-slate-900 group-hover:text-sky-700">{sermon.title}</h4>
                      </Link>
                      <div className="mt-3 flex flex-col gap-3 border-t border-slate-100 pt-3 text-sm text-slate-500 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                          {sermon.series_title ? (
                            <>
                              <span className="font-semibold text-slate-700">{sermon.series_title}</span>
                              {sermon.series_order ? ` · 第 ${sermon.series_order} 講` : ''}
                              {sermon.date ? ` · ${sermon.date}` : ''}
                            </>
                          ) : sermon.date}
                        </div>
                        <div className="flex flex-wrap gap-3 font-semibold">
                          <Link href={`/resources/sermons/${encodeURIComponent(sermon.id)}`} className="text-sky-700 hover:underline">
                            觀看本講
                          </Link>
                          {sermon.series_id ? (
                            <Link
                              href={`/resources/series/${encodeURIComponent(sermon.series_id)}?sermon=${encodeURIComponent(sermon.id)}`}
                              className="text-indigo-700 hover:underline"
                            >
                              查看完整系列
                            </Link>
                          ) : null}
                        </div>
                      </div>
                    </article>
                  ))}
                  {chapter.relatedSermons.length > 0 && (
                    <div className="rounded-xl border border-dashed border-amber-300 bg-amber-50 px-6 py-4">
                      <div className="text-sm font-bold text-amber-900">相關講論</div>
                      <div className="mt-2 space-y-2">
                        {chapter.relatedSermons.map(sermon => (
                          <Link
                            key={`related-${sermon.id}`}
                            href={`/resources/sermons/${sermon.id}`}
                            className="block font-semibold text-amber-950 hover:text-sky-700"
                          >
                            {sermon.title}
                            {sermon.catalog_primary_passage?.display
                              ? `（主要編於 ${sermon.catalog_primary_passage.display}）`
                              : ''}
                          </Link>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </section>
            ))}
          </div>
            </details>
          );
        })}
      </div>
    </div>
  );
};
