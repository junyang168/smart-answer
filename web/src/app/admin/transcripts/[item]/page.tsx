import { notFound } from "next/navigation";
import { TranscriptReviewEntry } from "@/app/components/admin/surmons/TranscriptProofreader";

export default async function TranscriptEditorPage({
  params,
}: {
  params: Promise<{ item: string }>;
}) {
  if (process.env.TRANSCRIPT_EDITOR_CUTOVER !== "1") notFound();
  const { item } = await params;
  return (
    <TranscriptReviewEntry
      item={decodeURIComponent(item)}
      legacy={<p>這篇逐字稿尚未遷移。</p>}
    />
  );
}
