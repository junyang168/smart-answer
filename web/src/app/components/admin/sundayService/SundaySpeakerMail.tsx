"use client";

import { useCallback, useEffect, useState } from "react";

type Attachment = { id: string; mailbox: string; filename: string; from: string; subject: string; sent_at: string; date?: string; speaker?: string; status: string; reason?: string; sourceUrl: string };
type MailState = { configured: boolean; enabled?: boolean; mailboxes: { email: string; connected: boolean; status?: string; at?: string; error?: string }[]; files: Attachment[]; message_errors?: { key: string; mailbox: string; subject: string; error: string }[] };
const names: Record<string, string> = { imported: "已收存至主日 OneDrive 資料夾", duplicate: "相同原檔已收存", older_version: "舊版已保存，沿用較新講道檔", review: "需要你核對", error: "收存失敗", ready: "準備收存", publishing: "正在收存", needs_connection: "待連接 Gmail", needs_review: "請核對連線設定", ready_mail: "接收正常" };

export function SundaySpeakerMail({ workers }: { workers: string[] }) {
  const [state, setState] = useState<MailState | null>(null);
  const [denied, setDenied] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [choices, setChoices] = useState<Record<string, { date: string; speaker: string }>>({});
  const load = useCallback(async () => {
    const response = await fetch("/api/admin/sunday-assistant/gmail/state", { cache: "no-store" });
    if (response.status === 403) { setDenied(true); return; }
    if (!response.ok) throw new Error("無法讀取講員郵件接收狀態");
    setState(await response.json()); setDenied(false);
  }, []);
  useEffect(() => {
    const refresh = () => void load().catch(e => setError(e.message));
    refresh(); const timer = setInterval(refresh, 15000); return () => clearInterval(timer);
  }, [load]);
  async function act(path: string, body: unknown) {
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/admin/sunday-assistant/gmail/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || result.error || "附件收存失敗");
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : "附件收存失敗"); }
    finally { setBusy(false); }
  }
  if (denied || (!state && !error)) return null;
  return <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
    <h2 className="text-lg font-bold">講員 PPT · Gmail → OneDrive</h2>
    <p className="mt-2 text-sm text-slate-600">講員原檔與主日 PPT 放在同一個日期資料夾。保留原有版面、動畫及版本；不合併投影片。相同附件不重複收存，寄件人／日期不清楚時留在此處核對。週六晚上 10 點（達拉斯時間）仍未確認收到時，提醒網站管理員跟進。</p>
    {error && <p role="alert" className="mt-3 rounded bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    {state && !state.configured && <p className="mt-3 text-sm">尚未設定接收郵箱。</p>}
    {state?.mailboxes.map(mail => <p key={mail.email} className="mt-2 break-words text-sm">{mail.email} · {mail.status === "ready" ? "接收正常" : names[mail.status || ""] || (mail.connected ? "等待檢查" : "待連接 Gmail")}{mail.error && `：${mail.error}`}{mail.at && <span className="ml-2 text-xs text-slate-500">{new Date(mail.at).toLocaleString()}</span>}</p>)}
    {!!state?.message_errors?.length && state.message_errors.map(mail => <div key={mail.key} className="mt-3 rounded border border-amber-200 p-3 text-sm"><p>{mail.subject} · {mail.mailbox}</p><p className="mt-1 text-amber-800">{mail.error}</p><button type="button" disabled={busy} className="mt-2 underline" onClick={() => void act(`messages/${mail.key}/retry`, {})}>重試郵件判讀</button></div>)}
    {state?.configured && !state.files.filter(file => file.status !== "ignored").length && <p className="mt-3 text-sm text-slate-500">尚無已匹配或待核對的講道附件。</p>}
    <div className="mt-4 max-h-[36rem] space-y-3 overflow-y-auto">{state?.files.filter(file => file.status !== "ignored").map(file => {
      const choice = choices[file.id] || { date: file.date || "", speaker: file.speaker || "" };
      return <article key={file.id} className="rounded-lg border p-3 text-sm">
        <p className="font-semibold">{file.date || "日期待核對"} · {file.speaker || "講員待核對"} · {names[file.status] || file.status}</p>
        <p className="mt-1 break-words">{file.filename}</p>
        <p className="mt-1 break-words text-xs text-slate-500">{file.from} → {file.mailbox} · {new Date(file.sent_at).toLocaleString()}</p>
        <p className="mt-1 break-words">{file.subject}</p>
        <a href={file.sourceUrl} target="_blank" rel="noreferrer" className="mt-1 inline-block underline">查看原郵件</a>
        {file.reason && <p className="mt-2 text-amber-800">{file.reason}</p>}
        {["review", "error", "publishing"].includes(file.status) && <div className="mt-3 flex flex-wrap items-center gap-2">
          <input aria-label={`附件 ${file.filename} 的主日日期`} type="date" className="rounded border p-2" value={choice.date} onChange={e => setChoices(old => ({ ...old, [file.id]: { ...choice, date: e.target.value } }))} />
          <select aria-label={`附件 ${file.filename} 的講員`} className="max-w-full rounded border p-2" value={choice.speaker} onChange={e => setChoices(old => ({ ...old, [file.id]: { ...choice, speaker: e.target.value } }))}><option value="">請選擇講員</option>{workers.map(name => <option key={name}>{name}</option>)}</select>
          <button type="button" disabled={busy || !choice.date || !choice.speaker} className="rounded bg-indigo-600 px-3 py-2 text-white disabled:opacity-40" onClick={() => void act(`files/${file.id}`, { action: "apply", ...choice })}>核對寄件人並收存講道 PPT</button>
          <button type="button" disabled={busy} className="underline" onClick={() => void act(`files/${file.id}`, { action: "dismiss" })}>略過</button>
        </div>}
      </article>;
    })}</div>
  </section>;
}
