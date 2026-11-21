"use client";
import { useCallback, useEffect, useState } from "react";

interface Prayer { markdown: string; revision: string }
const url = "/api/admin/sunday-services/prayer";
async function readReply(response: Response): Promise<Prayer> {
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "代禱事項未能儲存，請重試。");
  return data;
}
export function SundayPrayerSection({ onSaved }: { onSaved: () => void }) {
  const [saved, setSaved] = useState<Prayer | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setBusy(true); setError(""); setMessage("");
    try { const data = await readReply(await fetch(url, { cache: "no-store" })); setSaved(data); setText(data.markdown); }
    catch (err) { setError(err instanceof Error ? err.message : "代禱事項載入失敗。"); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function save() {
    if (!saved) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const data = await readReply(await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ markdown: text, revision: saved.revision }) }));
      setSaved(data); setText(data.markdown); setMessage("已儲存。本週及未來主日產生 PPT 時會使用這份代禱事項。"); onSaved();
    } catch (err) { setError(err instanceof Error ? err.message : "代禱事項儲存失敗。"); }
    finally { setBusy(false); }
  }
  return <section id="sunday-prayer" aria-labelledby="sunday-prayer-heading" className="rounded-lg border border-blue-100 bg-white p-6 shadow-sm">
    <h3 id="sunday-prayer-heading" className="text-lg font-semibold text-gray-900">代禱事項</h3>
    <p className="mt-1 text-sm text-gray-600">統一維護本週及未來主日的代禱內容，不必每週複製。歷史主日保留當時內容；已產生的 PPT 如需更新，請重新產生。</p>
    <label htmlFor="sunday-prayer-text" className="mt-4 block text-sm font-semibold text-gray-700">代禱內容</label>
    <textarea id="sunday-prayer-text" value={text} onChange={event => setText(event.target.value)} disabled={!saved || busy} className="mt-2 h-40 w-full rounded border border-gray-300 px-3 py-2 text-sm" placeholder="每行一項代禱事項" />
    <div className="mt-3 flex gap-3">
      <button type="button" onClick={() => void save()} disabled={!saved || busy || text === saved.markdown} className="rounded bg-blue-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{busy ? "處理中…" : "儲存代禱事項"}</button>
      <button type="button" onClick={() => { if (!saved || text === saved.markdown || window.confirm("重新載入會放棄尚未儲存的修改，確定繼續？")) void load(); }} disabled={busy} className="rounded border px-4 py-2 text-sm">重新載入</button>
    </div>
    {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
    {message && <p role="status" className="mt-2 text-sm text-green-700">{message}</p>}
  </section>;
}
