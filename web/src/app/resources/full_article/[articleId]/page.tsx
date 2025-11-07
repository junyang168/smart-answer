import Link from "next/link";
import { notFound } from "next/navigation";
import { FullArticleDetail } from "@/app/types/full-article";
import { ScriptureMarkdown } from "@/app/components/full-article/ScriptureMarkdown";

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

  const sourceSermons = (article.sourceSermonIds ?? []).map((id) => id.trim()).filter((id) => id.length > 0);
  const sermonTitleMap = await fetchSermonTitleMap(sourceSermons, { disableCache });
  const sourceSermonItems = sourceSermons.map((id) => ({ id, title: sermonTitleMap[id] ?? id }));
  const hasSourceSermons = sourceSermonItems.length > 0;

  return (
    <div className="bg-white">
      <div className="container mx-auto px-6 py-12">
        <div className="grid grid-cols-1 lg:grid-cols-3 lg:gap-12">
          <main className="lg:col-span-2">
            <header className="mb-8 space-y-2">
              <p className="text-sm text-gray-500">AI 輔助查經 / 全文文章</p>
              <h1 className="text-3xl lg:text-4xl font-bold text-gray-900">{article.name || article.slug}</h1>
              {article.subtitle && <p className="text-lg text-gray-600">{article.subtitle}</p>}
              <p className="text-sm text-gray-500">
                最近更新：{new Date(article.updated_at).toLocaleString("zh-TW")}
              </p>
            </header>

            {article.summaryMarkdown && (
              <section className="bg-slate-50 border border-slate-200 rounded-lg p-6 mb-8">
                <div className="flex items-center mb-3">
                  <div className="w-2 h-2 rounded-full bg-blue-500 mr-3" />
                  <h2 className="text-xl font-semibold text-slate-800">內容摘要</h2>
                </div>
                <div className="prose prose-sm max-w-none text-slate-800">
                  <ScriptureMarkdown markdown={article.summaryMarkdown} />
                </div>
              </section>
            )}

            <article className="prose lg:prose-lg max-w-none">
              <ScriptureMarkdown markdown={article.articleMarkdown || ""} />
            </article>
          </main>

          {hasSourceSermons && (
            <aside className="lg:col-span-1 mt-12 lg:mt-0 lg:sticky lg:top-24 self-start">
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
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}
