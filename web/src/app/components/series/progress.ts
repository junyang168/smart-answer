import { Sermon } from "@/app/interfaces/article";

/** 講道的編輯狀態：ready 待認領，assigned 已認領、編輯中，published 已發佈。 */
export const STATUS_LABEL: Record<string, string> = {
  processing: "待完整處理",
  published: "已發佈",
  assigned: "編輯中",
  ready: "待認領",
};

export const STATUS_ORDER = ["published", "assigned", "ready", "processing"] as const;

export const STATUS_CLASS: Record<string, string> = {
  processing: "bg-sky-100 text-sky-900",
  published: "bg-emerald-100 text-emerald-800",
  assigned: "bg-amber-100 text-amber-900",
  ready: "bg-stone-100 text-stone-700",
};

export function countByStatus(sermons: Sermon[]): Record<string, number> {
  const counts: Record<string, number> = { published: 0, assigned: 0, ready: 0, processing: 0 };
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

/**
 * 同工認得的系列名稱。系列 id 是來源名（如「2011 NYSC 專題：馬太福音釋經（二）」），
 * 同工按它找講道；title 多是給讀者的題目。只有當 title 本身就是更正過的來源名
 * （以年份開頭）時用 title：（四）的 id 寫成 2016，實為 2014（負責人 2026-11-06），
 * 而 id 也是講道錄音、Claim 來源的識別碼，不能改。
 */
export function seriesName(series: { id: string; title?: string | null }): { name: string; subtitle: string } {
  const title = series.title ?? "";
  if (title && title !== series.id && /^\d{4}/.test(title)) return { name: title, subtitle: "" };
  return { name: series.id, subtitle: title && title !== series.id ? title : "" };
}
