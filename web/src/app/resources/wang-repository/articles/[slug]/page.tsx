import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PublicArticleReader } from "./public-article-reader";
import type { PublicWangArticle } from "../article-types";

const ARTICLE_BACKEND_BASE = (
  process.env.SC_API_SERVICE_URL
  || process.env.FULL_ARTICLE_SERVICE_URL
  || "http://127.0.0.1:8222"
).replace(/\/$/, "");

async function fetchArticle(slug: string): Promise<PublicWangArticle | null> {
  const response = await fetch(
    `${ARTICLE_BACKEND_BASE}/public/wang-articles/${encodeURIComponent(slug)}`,
    { cache: "no-store" },
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("文章服務暫時無法使用");
  return response.json();
}

export async function generateMetadata(props: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const params = await props.params;
  const article = await fetchArticle(params.slug);
  if (!article) return { title: "找不到文章" };
  const description = `從馬太福音 ${article.passage.replace(/^太/, "")} 閱讀認信、磐石、教會與天國鑰匙，並聆聽王守仁教授相關原聲講解。`;
  return {
    title: `${article.title} | 王守仁教授聖經講論文庫`,
    description,
    alternates: { canonical: `/resources/wang-repository/articles/${article.slug}` },
    openGraph: {
      type: "article",
      locale: "zh_TW",
      title: article.title,
      description,
    },
  };
}

export default async function PublicWangArticlePage(props: { params: Promise<{ slug: string }> }) {
  const params = await props.params;
  const article = await fetchArticle(params.slug);
  if (!article) notFound();
  const path = `/resources/wang-repository/articles/${article.slug}`;
  const structuredData = {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: article.title,
    description: `馬太福音 ${article.passage.replace(/^太/, "")} 釋經文章與原聲講解。`,
    inLanguage: "zh-Hant",
    mainEntityOfPage: path,
    about: {
      "@type": "Thing",
      name: `馬太福音 ${article.passage.replace(/^太/, "")}`,
    },
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData).replace(/</g, "\\u003c") }}
      />
      <PublicArticleReader article={article} />
    </>
  );
}
