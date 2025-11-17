import Link from "next/link";
import { notFound } from "next/navigation";
import { getServerSession } from "next-auth";
import { FullArticleDetail } from "@/app/types/full-article";
import { FullArticleReader } from "@/app/components/full-article/FullArticleReader";
import { authConfig } from "@/app/utils/auth";
import { buildArticleSections } from "@/app/components/full-article/section-utils";
import { FullArticleUtilities } from "@/app/components/full-article/FullArticleUtilities";

const ARTICLE_BACKEND_BASE =
  process.env.FULL_ARTICLE_SERVICE_URL ||
  process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
  "http://127.0.0.1:8555";

const SERMON_BACKEND_BASE =
  process.env.SC_API_SERVICE_URL ||
  process.env.FULL_ARTICLE_SERVICE_URL ||
  process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
  "http://127.0.0.1:8555";

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

  const articleSections = buildArticleSections(article.articleMarkdown || "");
  const sourceSermons = (article.sourceSermonIds ?? []).map((id) => id.trim()).filter((id) => id.length > 0);
  const session = await getServerSession(authConfig);
  const sermonTitleMap =
    session && sourceSermons.length > 0 ? await fetchSermonTitleMap(sourceSermons, { disableCache }) : {};
  const sourceSermonItems = sourceSermons.map((id) => ({ id, title: sermonTitleMap[id] ?? id }));
  const hasSourceSermons = sourceSermonItems.length > 0;
  const showSourceSermons = Boolean(session) && hasSourceSermons;
  const showChapterNavigation = true;

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
              markdown={article.articleMarkdown || ""}
              articleTitle={article.name || article.slug}
              summaryMarkdown={article.summaryMarkdown}
              topAnchorId={articleTopAnchorId}
            />
          </main>

          {(showSourceSermons || showChapterNavigation) && (
            <aside className="lg:col-span-1 mt-12 lg:mt-0 lg:sticky lg:top-24 self-start">
              <div className="space-y-6">
                <FullArticleUtilities articleTitle={article.name || article.slug} articleMarkdown={article.articleMarkdown} />
                {showChapterNavigation && (
                  <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
                    <h2 className="text-xl font-semibold text-gray-900">章節導覽</h2>
                    <p className="mt-1 text-sm text-gray-600">快速導覽各段內容。</p>
                    {articleSections.length === 0 ? (
                      <p className="mt-4 text-sm text-gray-500">目前沒有章節可供導覽。</p>
                    ) : (
                      <ul className="mt-4 space-y-2">
                        {articleSections.map((section) => {
                          const plainTitle = section.title.replace(/\*\*/g, "").trim();
                          return (
                            <li key={section.id}>
                              <a
                                href={`#${section.id}`}
                                className="flex items-center justify-between rounded-md border border-gray-100 px-3 py-2 text-sm text-gray-700 transition hover:border-blue-200 hover:text-blue-700"
                              >
                                <span className="truncate">{plainTitle}</span>
                              </a>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                ) }
                {showSourceSermons && (
                  <div className="bg-gray-50 border border-gray-200 rounded-lg p-6">
                    <h2 className="text-xl font-semibold text-gray-900">來源講道</h2>
                    <p className="mt-1 text-sm text-gray-600">
                      本文由以下講道彙整而成。
                    </p>
                    <ul className="mt-4 space-y-3">
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
                ) }

              </div>
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}
