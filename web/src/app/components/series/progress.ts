import { Sermon } from "@/app/interfaces/article";

/** 講道的編輯狀態：ready 待認領，assigned 已認領、編輯中，published 已發佈。 */
export const STATUS_LABEL: Record<string, string> = {
  published: "已發佈",
  assigned: "編輯中",
  ready: "待認領",
};

export const STATUS_ORDER = ["published", "assigned", "ready"] as const;

export const STATUS_CLASS: Record<string, string> = {
  published: "bg-emerald-100 text-emerald-800",
  assigned: "bg-amber-100 text-amber-900",
  ready: "bg-stone-100 text-stone-700",
};

export function countByStatus(sermons: Sermon[]): Record<string, number> {
  const counts: Record<string, number> = { published: 0, assigned: 0, ready: 0 };
  for (const sermon of sermons) counts[sermon.status] = (counts[sermon.status] ?? 0) + 1;
  return counts;
}

/**
 * 講道日期。達拉斯聖道教會的錄音名為「S YYMMDD …」，名字裡的日期才準；
 * 記錄的 deliver_date 有些是錯的（如 S 200322 記成 2023-05-15）。
 */
export function deliveredOn(sermon: Sermon & { deliver_date?: string | null }): string {
  const match = /^S (\d{2})(\d{2})(\d{2})/.exec(sermon.item ?? "");
  if (match) return `20${match[1]}-${match[2]}-${match[3]}`;
  return sermon.deliver_date || "";
}

export function day(value?: string | null): string {
  return value ? value.slice(0, 10) : "";
}
