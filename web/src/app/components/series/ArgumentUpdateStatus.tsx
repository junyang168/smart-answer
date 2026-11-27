import Link from "next/link";
import type { Sermon } from "@/app/interfaces/article";

export type ArgumentUpdateState = "completed" | "stale" | null;

export function ArgumentUpdateStatus({ status, progress }: { status?: ArgumentUpdateState; progress?: Sermon["argument_unit_progress"] }) {
  if (progress) {
    const label = progress.needs_revision > 0
      ? `需要修改 ${progress.needs_revision}/${progress.total} 單元`
      : progress.approved === progress.total
        ? `已通過驗收 ${progress.total}/${progress.total} 單元`
        : progress.loaded === progress.total
          ? `已入庫（候選）${progress.loaded}/${progress.total} 單元`
          : progress.generated === 0
            ? `待生成論證 0/${progress.total} 單元`
            : `已生成 ${progress.generated}/${progress.total} 單元`;
    return (
      <Link href={`/admin/wang/series/${encodeURIComponent(progress.series_id)}`}
        className={progress.needs_revision > 0 ? "text-rose-700 hover:underline" : "text-indigo-700 hover:underline"}>
        <span className="block">{label}</span>
        {progress.loaded < progress.total && progress.generated > 0 && (
          <span className="block text-xs text-stone-500">已入庫 {progress.loaded}/{progress.total} 單元</span>
        )}
      </Link>
    );
  }
  if (status === "completed") return <span className="text-emerald-800">已完成</span>;
  if (status === "stale") return <span className="text-amber-800">原文已變更</span>;
  return <span className="text-slate-500">未有完成紀錄</span>;
}
