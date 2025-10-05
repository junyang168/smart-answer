import Link from "next/link";
import { FullArticleSummary } from "@/app/types/full-article";

export const revalidate = 300;

async function fetchArticles(): Promise<FullArticleSummary[]> {
  const base =
    process.env.FULL_ARTICLE_SERVICE_URL ||
    process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
    "http://127.0.0.1:8555";

  const url = new URL("/admin/full-articles", base);
  const response = await fetch(url.toString(), { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load articles (${response.status})`);
  }
  return (await response.json()) as FullArticleSummary[];
}

const STATUS_LABEL: Record<string, string> = {
  generated: "已產生",
  final: "已定稿",
};

export default async function FullArticleListingPage() {
  const articles = await fetchArticles();
  const filtered = articles.filter((article) =>
    article.status === "generated" || article.status === "final",
  );

  return (
    <div className="bg-gray-50 py-12">
      <div className="mx-auto max-w-4xl px-6">
        <header className="mb-8 space-y-3">
          <h1 className="text-3xl font-bold text-gray-900">全文文章</h1>
          <p className="text-gray-600">
            查看由講道逐步整理出的全文文章。點選任一條目即可閱讀完整內容。
          </p>
        </header>

        {filtered.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center text-gray-500">
            目前尚未有可公開的全文文章。
          </div>
        ) : (
          <div className="space-y-4">
            {filtered.map((article) => (
              <Link
                key={article.id}
                href={`/resources/full_article/${article.id}`}
                className="block rounded-lg border border-gray-200 bg-white p-5 shadow-sm transition hover:border-blue-300 hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-xl font-semibold text-gray-900">
                      {article.name || article.slug}
                    </h2>
                    {article.subtitle && (
                      <p className="mt-1 text-sm text-gray-600">{article.subtitle}</p>
                    )}
                    <p className="mt-2 text-xs text-gray-500">
                      更新時間：{new Date(article.updated_at).toLocaleString("zh-TW")}
                    </p>
                  </div>
                  <span className="inline-flex h-7 items-center rounded-full bg-blue-50 px-3 text-sm font-medium text-blue-700">
                    {STATUS_LABEL[article.status] ?? article.status}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
