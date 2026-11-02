import Link from "next/link";
import { TranscriptReviewEntry } from "@/app/components/admin/surmons/TranscriptProofreader";

export default async function BetaEditor({
  params,
}: {
  params: Promise<{ item: string }>;
}) {
  const routeParams = await params;
  const item = decodeURIComponent(routeParams.item);
  return (
    <>
      <div className="mb-3 flex items-center justify-between text-sm text-slate-600">
        <span>圖文審核 Beta · 測試同工專用</span>
        <Link
          className="text-blue-700 underline"
          href={`/admin/transcript-beta/${encodeURIComponent(item)}/published`}
        >
          查看測試發佈版
        </Link>
      </div>
      <TranscriptReviewEntry
        item={item}
        apiBase="/api/sc_api/beta"
        legacy={<p>此講道尚未加入圖文審核 beta。</p>}
      />
    </>
  );
}
