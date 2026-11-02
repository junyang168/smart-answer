"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

type Settings = {
  enabled: boolean; availability_group: string; service_group: string;
  availability_group_name: string; service_group_name: string;
  auto_apply: boolean; reminders_enabled: boolean; friday_hour: number; friday_minute: number;
  sender_workers: Record<string, string[]>; trusted_senders: string[];
  households: string[][]; worker_aliases: Record<string, string>;
};
type Message = { id: string; status: string; error?: string;
  payload: { sender_id: string; group_id: string; sent_at: string; text: string; unsupported_media: boolean } };
type Operation = { kind: string; date?: string; worker?: string; field?: string;
  before: unknown; after: unknown; evidence: string };
type Proposal = { id: number; message_id: string; status: string; error?: string;
  payload: { operations: Operation[]; questions: string[] } };
type Data = {
  identities?: { sender_id: string; worker_name: string }[];
  settings: Settings; messages: Message[]; proposals: Proposal[];
  outbox: { key: string; text: string; status: string; error?: string; updated_at: string }[];
  bridge: { state: string; qr?: string; error?: string; account?: string; synced?: boolean; pending?: number; lastSyncAt?: string };
  worker?: { status: string; at: string; reminder?: string; error?: string };
  conflicts: { date: string; role: string; worker: string }[];
  sunday: string; missing: string[]; workers: string[];
};
const labels: Record<string, string> = {
  queued: "等待處理", processing: "正在整理消息", review: "待核對", done: "已完成", applied: "已更新網站",
  irrelevant: "無須更新", error: "處理失敗", dismissed: "已略過", applying: "正在儲存",
  waiting_reply: "已在群內提問，等待回答", resolved: "已由後續回答處理", cancelled: "已取消",
  sent: "已發送", sending: "發送中", unknown: "發送結果待確認", ready: "已連線", disconnected: "未連接",
  offline: "橋接器未啟動", connecting: "連接中", qr: "等待手機掃碼", auth_failure: "需要重新登入",
  paused: "已暫停", idle: "等待新消息", waiting_for_messages: "有資料待處理，暫緩提醒",
  stale: "處理程序未更新",
  bridge_unavailable: "WhatsApp 未連線，暫緩提醒", bridge_not_synced: "等待群消息同步",
  bridge_stale: "同步狀態過期，暫緩提醒", complete: "資料齊全", not_due: "尚未到提醒時間",
  schedule_conflict: "本主日同工安排有衝突，請先處理",
  sermonSpeaker: "講員", sermonTitle: "講道題目", hymn: "詩歌", responseHymn: "回應詩歌", scripture: "讀經經文", donationAmount: "本週奉獻",
};
const display = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value ?? "未填");
const card = "rounded-xl border border-slate-200 bg-white p-5 shadow-sm";
const button = "rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40";
const input = "w-full rounded-lg border border-slate-300 bg-white p-2 text-sm";

async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api/admin/sunday-assistant/${path}`, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store",
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "操作失敗");
  return data;
}

export default function SundayAssistantPage() {
  const [data, setData] = useState<Data | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [groups, setGroups] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const [sender, setSender] = useState("");
  const [selectedWorkers, setSelectedWorkers] = useState<string[]>([]);
  const load = useCallback(async () => {
    try { const next = await call<Data>("state"); setData(next); setLoadError(""); if (!dirtyRef.current) setSettings(next.settings); }
    catch (e) { setLoadError(e instanceof Error ? e.message : "無法載入"); }
  }, []);
  useEffect(() => { void load(); const id = setInterval(() => void load(), 5000); return () => clearInterval(id); }, [load]);
  function patch(update: Partial<Settings>) { dirtyRef.current = true; setSettings(old => old && { ...old, ...update }); setDirty(true); }
  async function action(fn: () => Promise<unknown>, message: string) {
    setBusy(true); setError(""); setFeedback("");
    try { await fn(); setFeedback(message); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "操作失敗"); }
    finally { setBusy(false); }
  }
  const save = () => action(async () => {
    const saved = await call<Settings>("settings", "PUT", settings);
    setSettings(saved); dirtyRef.current = false; setDirty(false);
  }, "設定已儲存");
  const pending = data?.proposals.filter(p => !["applied", "dismissed", "resolved"].includes(p.status)) || [];
  return <div className="mx-auto max-w-5xl space-y-5 px-2 pb-16">
    <header className="rounded-2xl bg-slate-900 p-6 text-white">
      <p className="text-sm text-indigo-200">主日服事 · WhatsApp</p>
      <h1 className="mt-1 text-2xl font-bold">主日資料助理</h1>
      <p className="mt-3 text-sm leading-6 text-slate-200">整理外出日期、講員、講道題目、詩歌、讀經與本週奉獻；支援不同同工分次補充。不清楚時在原群追問，收到回答後繼續登記。請假資料成功更新後回覆原群；周五下午 5 點檢查崇拜準備資料的缺項並提醒。</p>
      <div className="mt-4 flex flex-wrap gap-4 text-sm underline"><Link href="/admin/sunday-service">主日表單</Link><Link href="/admin/sunday-workers">同工及外出日期</Link></div>
    </header>
    {(error || loadError) && <div role="alert" className="rounded-lg bg-red-50 p-4 text-red-800">{error || loadError}</div>}
    {feedback && <p role="status" className="rounded-lg bg-emerald-50 p-4 text-emerald-800">{feedback}</p>}
    {!data || !settings ? <p>正在載入助理狀態…</p> : <>
      <div className="grid gap-4 sm:grid-cols-3">
        <section className={card}><h2 className="font-semibold">WhatsApp</h2><p className="mt-2">{labels[data.bridge.state] || data.bridge.state}</p><p className="mt-1 break-all text-xs text-slate-500">{data.bridge.account}</p><p className="mt-2 text-xs">{data.bridge.synced ? "群消息已同步" : "尚未完成同步"} · 等待傳送 {data.bridge.pending ?? 0} 則</p>{data.bridge.error && <p className="mt-2 text-sm text-amber-800">{data.bridge.error}</p>}</section>
        <section className={card}><h2 className="font-semibold">整理進度</h2><p className="mt-2">{labels[data.worker?.status || ""] || "處理程序尚未啟動"}</p><p className="mt-2 text-sm">{pending.length} 項待核對</p>{data.worker && <p className="mt-1 text-xs text-slate-500">最近更新 {new Date(data.worker.at).toLocaleString()}</p>}{data.worker?.error && <p className="mt-2 text-sm text-amber-800">{data.worker.error}</p>}</section>
        <section className={card}><h2 className="font-semibold">{data.sunday} 主日資料</h2><p className="mt-2 text-sm">{data.missing.length ? `尚缺：${data.missing.join("、")}` : "已齊全"}</p><p className="mt-2 text-xs text-slate-500">{labels[data.worker?.reminder || ""] || ""}</p></section>
      </div>
      <section className={card}>
        <h2 className="text-lg font-bold">連接與設定</h2>
        <p className="mt-2 text-sm text-slate-600">WhatsApp Web 接入使用非官方工具，可能因平台更新斷線或影響帳號。連接後先選定兩個資料來源群；首次同步最近 30 天可讀取的文字消息。</p>
        <div className="mt-4 flex flex-wrap gap-3">
          <button className={button} disabled={busy || data.bridge.state === "ready"} onClick={() => void action(() => call("connect", "POST", {}), "請使用手機 WhatsApp 的連結裝置掃碼")}>連接 WhatsApp</button>
          <button className={button} disabled={busy || data.bridge.state !== "ready"} onClick={() => void action(async () => { const result = await call<{ groups: typeof groups }>("groups"); setGroups(result.groups); }, "群名單已載入")}>讀取群名單</button>
        </div>
        {data.bridge.qr && <div className="mt-4"><img src={data.bridge.qr} width={240} height={240} alt="WhatsApp 連結裝置 QR 碼" /><p className="text-sm">手機 WhatsApp → 設定 → 連結裝置</p></div>}
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          {([ ["availability_group", "availability_group_name", "外出登記群"], ["service_group", "service_group_name", "主日內容群"] ] as const).map(([key, nameKey, label]) => <label key={key} className="space-y-2 text-sm"><span className="font-semibold">{label} · {settings[nameKey]}</span><select className={input} value={settings[key]} onChange={e => { const found = groups.find(g => g.id === e.target.value); patch({ [key]: e.target.value, ...(found ? { [nameKey]: found.name } : {}) }); }}><option value="">請連接後選擇群</option>{settings[key] && !groups.some(g => g.id === settings[key]) && <option value={settings[key]}>{settings[nameKey]}</option>}{groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>)}
        </div>
        <div className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
          <label><input type="checkbox" checked={settings.enabled} onChange={e => patch({ enabled: e.target.checked })} /> 啟用消息收集與整理</label>
          <label><input type="checkbox" checked={settings.auto_apply} onChange={e => patch({ auto_apply: e.target.checked })} /> 自動登記所選群內的明確資料</label>
          <label><input type="checkbox" checked={settings.reminders_enabled} onChange={e => patch({ reminders_enabled: e.target.checked })} /> 啟用周五缺項提醒</label>
          <label className="flex items-center gap-3">周五（達拉斯時間）<input type="time" value={`${String(settings.friday_hour).padStart(2, "0")}:${String(settings.friday_minute).padStart(2, "0")}`} onChange={e => { const [h, m] = e.target.value.split(":").map(Number); patch({ friday_hour: h, friday_minute: m }); }} /></label>
        </div>
        <p className="mt-3 text-xs text-slate-500">所選群的不同同工可分次提供資料。更正既有內容時先讀取並核對；日期或對象不清楚時在原群追問。首次歷史同步只提供預覽。</p>
        <details className="mt-5 rounded-lg border p-3"><summary className="cursor-pointer font-semibold">發送人姓名對應</summary>
          <p className="mt-3 text-sm">從下方原消息複製發送人 ID，對應到既有同工。每個帳號只對應本人；夫妻關係另存。首次自報姓名核對後會記住，下次「我」不再重問。</p>
          <input className={`${input} mt-3`} placeholder="WhatsApp 發送人 ID" value={sender} onChange={e => setSender(e.target.value)} />
          <div className="my-3 flex max-h-40 flex-wrap gap-3 overflow-auto text-sm">{data.workers.map(name => <label key={name}><input type="radio" name="sender-worker" checked={selectedWorkers.includes(name)} onChange={() => setSelectedWorkers([name])} /> {name}</label>)}</div>
          <div className="flex flex-wrap gap-3"><button className={button} disabled={!sender.trim() || !selectedWorkers.length} onClick={() => { patch({ sender_workers: { ...settings.sender_workers, [sender.trim()]: selectedWorkers } }); setSelectedWorkers([]); }}>加入姓名對應</button></div>
          <ul className="mt-3 space-y-2 text-sm">{Object.entries(settings.sender_workers).map(([id, names]) => <li key={id} className="break-all">{id} → {names.join("、")} <button className="text-red-700 underline" onClick={() => { const next = { ...settings.sender_workers }; delete next[id]; patch({ sender_workers: next }); }}>移除</button></li>)}</ul>
          {!!data.identities?.length && <div className="mt-3 text-sm"><p className="font-semibold">已核對的自我介紹</p>{data.identities.map(item => <p className="break-all" key={item.sender_id}>{item.sender_id} → {item.worker_name}</p>)}</div>}
          <p className="mt-4 text-sm font-semibold">已確認夫妻</p>
          {(settings.households || []).map(pair => <p className="mt-1 text-sm" key={pair.join("/")}>{pair.join(" ／ ")}</p>)}
          <p className="mt-4 text-sm font-semibold">已確認姓名別名</p>
          {Object.entries(settings.worker_aliases || {}).map(([alias, name]) => <p className="mt-1 text-sm" key={alias}>{alias} → {name}</p>)}
        </details>
        <div className="mt-5 flex flex-wrap gap-3"><button className={button} disabled={busy || !dirty} onClick={() => void save()}>儲存設定</button><button className={button} disabled={busy || dirty || !settings.enabled} onClick={() => void action(() => call("tick", "POST", {}), "已要求處理待辦，進度會自動更新")}>處理待辦</button>{dirty && <span className="self-center text-sm text-amber-800">有尚未儲存的設定</span>}</div>
      </section>
      {!!data.conflicts.length && <section className="rounded-xl border border-amber-300 bg-amber-50 p-5"><h2 className="font-bold">外出日期與現有安排衝突</h2><ul className="mt-3 space-y-2">{data.conflicts.map((c, i) => <li key={i}>{c.date} · {c.role}：{c.worker} 不能服事</li>)}</ul><Link className="mt-3 inline-block underline" href="/admin/sunday-service">開啟主日表單調整</Link></section>}
      <section className={card}><h2 className="text-lg font-bold">待核對更新</h2>{!pending.length && <p className="mt-3 text-sm text-slate-500">目前沒有待核對資料。</p>}
        {pending.map(p => { const message = data.messages.find(m => m.id === p.message_id); return <article key={p.id} className="mt-4 space-y-3 rounded-xl border p-4">
          <p className="text-sm text-slate-500">{message ? new Date(message.payload.sent_at).toLocaleString() : ""} · {labels[p.status] || p.status}</p>
          <p className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm">{message?.payload.text || "含附件的消息"}</p>
          {p.payload.operations.map((op, i) => <div key={i} className="rounded-lg border border-indigo-100 p-3 text-sm"><p className="font-semibold">{op.kind === "service" ? `${op.date} · ${labels[op.field || ""]}` : `${op.worker} · 不能服事日期`}</p><p className="mt-2 break-words text-slate-500">目前：{display(op.before)}</p><p className="mt-1 break-words">更新為：{display(op.after)}</p><p className="mt-2 whitespace-pre-wrap text-xs text-slate-500">依據：{op.evidence}</p></div>)}
          {p.payload.questions.map((q, i) => <p key={i} className="text-sm text-amber-800">需確認：{q}</p>)}
          {p.error && <p className="text-sm text-red-700">{p.error}</p>}
          <div className="flex flex-wrap gap-3">{!p.payload.questions.length && <button className={button} disabled={busy} onClick={() => void action(() => call(`proposals/${p.id}`, "POST", { action: "apply" }), "已更新網站；請假回覆已列入發送佇列")}>核對並更新</button>}<button className={button} disabled={busy || p.status === "waiting_reply"} onClick={() => void action(() => call(`proposals/${p.id}`, "POST", { action: "retry" }), "已排入重新整理")}>重新整理</button><button className="text-sm text-slate-600 underline" disabled={busy} onClick={() => void action(() => call(`proposals/${p.id}`, "POST", { action: "dismiss" }), "已略過此更新")}>略過（已由表單處理或無須更新）</button></div>
        </article>; })}
      </section>
      <section className={card}><h2 className="text-lg font-bold">最近消息</h2><div className="mt-4 max-h-96 space-y-4 overflow-auto">{data.messages.map(m => <article key={m.id} className="border-b pb-3 text-sm"><div className="flex justify-between gap-3"><span>{new Date(m.payload.sent_at).toLocaleString()}</span><span>{labels[m.status] || m.status}</span></div><p className="mt-1 break-all text-xs text-slate-500">{m.payload.sender_id}</p><p className="mt-2 whitespace-pre-wrap">{m.payload.text || "語音、圖片或附件，待人工確認"}</p>{m.error && <p className="mt-2 text-red-700">{m.error}</p>}{m.status === "error" && <button className="mt-2 underline" disabled={busy} onClick={() => void action(() => call(`messages/${encodeURIComponent(m.id)}/retry`, "POST", {}), "已排入重試")}>重試</button>}</article>)}</div></section>
      <section className={card}><h2 className="text-lg font-bold">群回覆與提醒記錄</h2>{!data.outbox.length && <p className="mt-3 text-sm text-slate-500">尚無回覆或提醒。</p>}{data.outbox.map(o => <article key={o.key} className="mt-4 border-t pt-3 text-sm"><p>{labels[o.status] || o.status} · {new Date(o.updated_at).toLocaleString()}</p><p className="mt-2 whitespace-pre-wrap">{o.text}</p>{o.error && <p className="mt-2 text-amber-800">{o.error}</p>}</article>)}</section>
    </>}
  </div>;
}
