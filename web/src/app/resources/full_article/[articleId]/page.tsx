import { notFound } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FullArticleDetail } from "@/app/types/full-article";

async function fetchArticle(articleId: string): Promise<FullArticleDetail | null> {
  const base =
    process.env.FULL_ARTICLE_SERVICE_URL ||
    process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
    "http://127.0.0.1:8555";

  const url = new URL(`/admin/full-articles/${encodeURIComponent(articleId)}`, base);
  const response = await fetch(url.toString(), {
    next: { revalidate: 300 },
  });

  if (!response.ok) {
    return null;
  }

  const data = (await response.json()) as FullArticleDetail;
  return data;
}

export default async function FullArticleViewer({
  params,
}: {
  params: { articleId: string };
}) {
  const article = await fetchArticle(params.articleId);
  if (!article) {
    notFound();
  }

  return (
    <div className="bg-white">
      <div className="mx-auto max-w-4xl px-6 py-12">
        <header className="mb-8 space-y-2">
          <h1 className="text-3xl font-bold text-gray-900">{article.name || article.slug}</h1>
          {article.subtitle && <p className="text-lg text-gray-600">{article.subtitle}</p>}
          <p className="text-sm text-gray-500">
            最近更新：{new Date(article.updated_at).toLocaleString("zh-TW")}
          </p>
        </header>
        <article className="prose prose-lg max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{article.articleMarkdown || ""}</ReactMarkdown>
        </article>
      </div>
    </div>
  );
}
