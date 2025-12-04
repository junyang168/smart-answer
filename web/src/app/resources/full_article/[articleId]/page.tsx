import Link from "next/link";
import { notFound } from "next/navigation";
import { FullArticleDetail } from "@/app/types/full-article";
import { FullArticleReader } from "@/app/components/full-article/FullArticleReader";
import { getSessionWithDevFallback } from "@/app/utils/auth";
import { buildArticleSections } from "@/app/components/full-article/section-utils";
import { FullArticleUtilities } from "@/app/components/full-article/FullArticleUtilities";
import { ArticleTOC } from "@/app/components/full-article/ArticleTOC";

const ARTICLE_BACKEND_BASE =
  process.env.FULL_ARTICLE_SERVICE_URL ||
  process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
  "http://127.0.0.1:8222";

const SERMON_BACKEND_BASE =
  process.env.SC_API_SERVICE_URL ||
  process.env.FULL_ARTICLE_SERVICE_URL ||
  process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
  "http://127.0.0.1:8222";

const SERMON_USER_ID = process.env.SC_API_DEFAULT_USER_ID || "junyang168@gmail.com";

async function fetchArticle(articleId: string, options?: { disableCache?: boolean }): Promise<FullArticleDetail | null> {
  const url = new URL(`/admin/full-articles/${encodeURIComponent(articleId)}`, ARTICLE_BACKEND_BASE);
  const fetchOptions: RequestInit & { next?: { revalidate: number } } = options?.disableCache
    ? { cache: "no-store" }
    : { next: { revalidate: 300 } };
  const response = await fetch(url.toString(), fetchOptions);

  if (!response.ok) {
    return null;
  }

  const data = (await response.json()) as FullArticleDetail;
  return data;
}

interface SermonListEntry {
  item?: string;
  title?: string;
}

async function fetchSermonTitleMap(
  sermonIds: string[],
  options?: { disableCache?: boolean },
): Promise<Record<string, string>> {
  if (sermonIds.length === 0) {
    return {};
  }

  const url = new URL(`/sc_api/sermons/${encodeURIComponent(SERMON_USER_ID)}`, SERMON_BACKEND_BASE);
  const fetchOptions: RequestInit & { next?: { revalidate: number } } = options?.disableCache
    ? { cache: "no-store" }
    : { next: { revalidate: 300 } };
  const response = await fetch(url.toString(), fetchOptions);

  if (!response.ok) {
    return {};
  }

  const rawList = (await response.json()) as SermonListEntry[];
  const requested = new Set(sermonIds);
  const map: Record<string, string> = {};
  for (const entry of rawList) {
    const key = entry.item?.trim();
    if (!key || !requested.has(key)) {
      continue;
    }
    const title = entry.title?.trim();
    map[key] = title && title.length > 0 ? title : key;
  }
  return map;
}

export default async function FullArticleViewer({
  params,
  searchParams,
}: {
  params: { articleId: string };
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  const disableCache = typeof searchParams?.nocache !== "undefined";
  const article = await fetchArticle(params.articleId, { disableCache });
  if (!article) {
    notFound();
  }

  const isMaterial = article.articleType === "講稿素材";
  const displayMarkdown = isMaterial ? article.scriptMarkdown : (article.articleMarkdown || "");
  const articleSections = buildArticleSections(displayMarkdown);
  const sourceSermons = (article.sourceSermonIds ?? []).map((id) => id.trim()).filter((id) => id.length > 0);
  const sourceFullArticleIds = (article.sourceFullArticleIds ?? []).map((id) => id.trim()).filter((id) => id.length > 0);

  const session = await getSessionWithDevFallback();

  // Fetch sermon titles for direct source sermons
  const sermonTitleMap =
    session && sourceSermons.length > 0 ? await fetchSermonTitleMap(sourceSermons, { disableCache }) : {};
  const sourceSermonItems = sourceSermons.map((id) => ({ id, title: sermonTitleMap[id] ?? id }));

  // Fetch source full articles
  const sourceFullArticles: Array<FullArticleDetail & { sermonItems: Array<{ id: string; title: string }> }> = [];
  if (session && sourceFullArticleIds.length > 0) {
    for (const articleId of sourceFullArticleIds) {
      const sourceArticle = await fetchArticle(articleId, { disableCache });
      if (sourceArticle && sourceArticle.articleType === '講稿素材') {
        const articleSermons = (sourceArticle.sourceSermonIds ?? []).map((id) => id.trim()).filter((id) => id.length > 0);
        const articleSermonTitleMap = articleSermons.length > 0 ? await fetchSermonTitleMap(articleSermons, { disableCache }) : {};
        const sermonItems = articleSermons.map((id) => ({ id, title: articleSermonTitleMap[id] ?? id }));
        sourceFullArticles.push({ ...sourceArticle, sermonItems });
      }
    }
  }

  const hasSourceSermons = sourceSermonItems.length > 0;
  const hasSourceArticles = sourceFullArticles.length > 0;
  const showSourceSection = Boolean(session) && (hasSourceSermons || hasSourceArticles);
  const showChapterNavigation = articleSections.length > 1;

  const articleTopAnchorId = "full-article-top";

  return (
    <div className="bg-white">
      <div className="container mx-auto px-6 py-12">
        <div className="grid grid-cols-1 lg:grid-cols-3 lg:gap-12">
          <main className="lg:col-span-2">
            <header id={articleTopAnchorId} className="mb-8 space-y-2">
              <p className="text-sm text-gray-500">AI 輔助查經 / 全文文章</p>
              <h1 className="text-3xl lg:text-4xl font-bold text-gray-900">{article.name || article.slug}</h1>
              {article.subtitle && <p className="text-lg text-gray-600">{article.subtitle}</p>}
              <p className="text-sm text-gray-500">
                最近更新：{new Date(article.updated_at).toLocaleString("zh-TW")}
              </p>
            </header>

            <FullArticleReader
              markdown={displayMarkdown}
              articleTitle={article.name || article.slug}
              summaryMarkdown={article.summaryMarkdown}
              topAnchorId={articleTopAnchorId}
            />
          </main>

          {(showSourceSection || showChapterNavigation) && (
            <aside className="lg:col-span-1 mt-12 lg:mt-0 lg:sticky lg:top-24 self-start">
              <div className="space-y-6">
                <FullArticleUtilities articleTitle={article.name || article.slug} articleMarkdown={displayMarkdown} />
                {showChapterNavigation && (
                  <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
                    <h2 className="text-xl font-semibold text-gray-900">章節導覽</h2>
                    {articleSections.length === 0 ? (
                      <p className="mt-4 text-sm text-gray-500">目前沒有章節可供導覽。</p>
                    ) : (
                      <ArticleTOC sections={articleSections} />
                    )}
                  </div>
                )}
                {showSourceSection && (
                  <div className="bg-gray-50 border border-gray-200 rounded-lg p-6">
                    <h2 className="text-xl font-semibold text-gray-900">講道來源</h2>
                    <p className="mt-1 text-sm text-gray-600">
                      本文由以下講道彙整而成。
                    </p>

                    {/* Direct source sermons */}
                    {hasSourceSermons && (
                      <div className="mt-4">
                        <ul className="space-y-3">
                          {sourceSermonItems.map((item) => (
                            <li key={item.id}>
                              <Link
                                href={`/resources/sermons/${encodeURIComponent(item.id)}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center justify-between rounded-lg border border-blue-100 bg-white px-3 py-2 text-sm font-medium text-blue-700 transition hover:bg-blue-50"
                              >
                                <div className="min-w-0 flex-1">
                                  <p className="truncate font-semibold text-gray-900">{item.title}</p>
                                </div>
                                <span className="text-sm">↗</span>
                              </Link>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {/* Source material articles with nested sermons */}
                    {hasSourceArticles && (
                      <div className="mt-6">
                        <h3 className="text-sm font-semibold text-gray-700 mb-3">講稿素材文章</h3>
                        <ul className="space-y-4">
                          {sourceFullArticles.map((sourceArticle) => (
                            <li key={sourceArticle.id} className="border-l-2 border-amber-300 pl-3">
                              <Link
                                href={`/resources/full_article/${encodeURIComponent(sourceArticle.id)}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="block rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-sm font-medium transition hover:bg-amber-100"
                              >
                                <div className="flex items-center justify-between">
                                  <p className="font-semibold text-gray-900">{sourceArticle.name || sourceArticle.slug}</p>
                                  <span className="text-sm text-amber-700">↗</span>
                                </div>
                              </Link>

                              {/* Nested sermons for this material article */}
                              {sourceArticle.sermonItems.length > 0 && (
                                <ul className="mt-2 ml-3 space-y-2">
                                  {sourceArticle.sermonItems.map((sermon) => (
                                    <li key={sermon.id}>
                                      <Link
                                        href={`/resources/sermons/${encodeURIComponent(sermon.id)}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="flex items-center justify-between rounded-lg border border-blue-100 bg-white px-2 py-1.5 text-xs transition hover:bg-blue-50"
                                      >
                                        <div className="min-w-0 flex-1">
                                          <p className="truncate text-gray-700">{sermon.title}</p>
                                        </div>
                                        <span className="text-xs text-blue-600">↗</span>
                                      </Link>
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

              </div>
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}
