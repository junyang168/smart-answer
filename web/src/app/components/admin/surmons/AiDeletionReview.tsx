"use client";
import { type AiDeletion } from "./review-ai-deletions";
const names: Record<string, string> = { speech_restart: "口誤重起", unrelated_aside: "無關旁枝", technical_setup: "設備操作", logistics: "現場安排" };
export function AiDeletionReview({ items, disabled, onRestore, onLocate }: {
  items: AiDeletion[]; disabled: boolean;
  onRestore: (item: AiDeletion) => void; onLocate: (id: string) => void;
}) {
  return <section className="my-4 rounded-xl border bg-white p-4" aria-label="AI 軟刪除審查">
    <h2 className="font-semibold">AI 軟刪除 · {items.length} 處</h2>
    <p className="my-2 text-sm text-slate-500">只列仍與 AI 原始提議完全相符的刪除線；已恢復或改動的內容不列入。恢復後會自動儲存，仍需校對。</p>
    {items.length === 0 && <p>目前沒有可直接恢復的 AI 刪除。</p>}
    {items.map((item) => <article className="my-3 rounded border p-3" key={`${item.id}:${item.start}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>第 {item.id} 段 · {names[item.kind] || item.kind}</span>
        <div className="flex gap-2">
          <button className="rounded border px-3 py-1" onClick={() => onLocate(item.id)}>前往段落</button>
          <button className="rounded border px-3 py-1" disabled={disabled} onClick={() => onRestore(item)}>恢復原文</button>
        </div>
      </div>
      <p className="mt-2 text-sm">{item.reason}</p>
      <details className="mt-2"><summary className="cursor-pointer">展開原文與前後文</summary>
        {item.contextBefore && <p className="my-2 whitespace-pre-wrap text-sm text-slate-500">前段：{item.contextBefore}</p>}
        <p className="whitespace-pre-wrap">{item.text.slice(Math.max(0, item.start - 140), item.start)}<del className="bg-red-50 text-red-800">{item.old}</del>{item.text.slice(item.start + item.marker.length, item.start + item.marker.length + 140)}</p>
        {item.contextAfter && <p className="my-2 whitespace-pre-wrap text-sm text-slate-500">後段：{item.contextAfter}</p>}
      </details>
    </article>)}
  </section>;
}
