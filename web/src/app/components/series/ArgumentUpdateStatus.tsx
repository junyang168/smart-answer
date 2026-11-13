export type ArgumentUpdateState = "completed" | "stale" | null;

export function ArgumentUpdateStatus({ status }: { status?: ArgumentUpdateState }) {
  if (status === "completed") return <span className="text-emerald-800">已完成</span>;
  if (status === "stale") return <span className="text-amber-800">原文已變更</span>;
  return <span className="text-slate-500">未有完成紀錄</span>;
}
