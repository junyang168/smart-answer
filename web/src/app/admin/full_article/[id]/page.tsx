import { notFound } from "next/navigation";
import { FullArticleEditor } from "@/app/components/admin/full-article/FullArticleEditor";
import { fetchFullArticle } from "@/app/admin/full_article/api";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function FullArticleDetailPage(props: PageProps) {
  const params = await props.params;
  try {
    const article = await fetchFullArticle(params.id);
    return <FullArticleEditor initialArticle={article} />;
  } catch (error) {
    console.error(`Failed to load full article ${params.id}`, error);
    notFound();
  }
}
