import { FullArticleList } from "@/app/components/admin/full-article/FullArticleList";
import { fetchFullArticleList } from "@/app/admin/full_article/api";
import { FullArticleSummary } from "@/app/types/full-article";

export const dynamic = "force-dynamic";



export default async function FullArticleAdminPage() {
  let articles: FullArticleSummary[] = [];
  try {
    articles = await fetchFullArticleList();
  } catch (error) {
    console.error("Failed to load full article list", error);
  }

  return <FullArticleList initialArticles={articles} />;
}
