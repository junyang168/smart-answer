"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { SundaySpeakerMail } from "@/app/components/admin/sundayService/SundaySpeakerMail";

type Settings = {
  enabled: boolean; availability_group: string; service_group: string; songs_group: string; response_hymn_group: string; offering_group: string;
  availability_group_name: string; service_group_name: string; songs_group_name: string;
  auto_generate_ppt: boolean; auto_apply: boolean; maintain_future_skeletons: boolean; collect_cross_group: boolean; group_questions_enabled: boolean; reminders_enabled: boolean; friday_hour: number; friday_minute: number;
  admin_clarifications_enabled: boolean; admin_clarifications_since?: string;
  fellowship_enabled: boolean; fellowship_start_date?: string; fellowship_since?: string;
  admin_help_enabled: boolean; admin_group: string; admin_group_name: string;
  sender_workers: Record<string, string[]>; trusted_senders: string[];
  households: string[][]; worker_aliases: Record<string, string>; worship_rotation: string[];
};
type Message = { id: string; status: string; error?: string;
  payload: { sender_id: string; group_id: string; sent_at: string; text: string; unsupported_media: boolean } };
type Operation = { kind: string; date?: string; worker?: string; field?: string;
  before: unknown; after: unknown; evidence: string };
type Proposal = { id: number; message_id: string; status: string; error?: string;
  payload: { operations: Operation[]; questions: string[] } };
type IdentityAudit = { checked_at?: string; members: { id: string; ids: string[]; labels: string[]; groups: string[]; saved_contact: boolean; worker?: string; candidates: string[]; status: string; reason?: string; lookup_errors: string[] }[]; workers_without_account: string[]; unseen_mappings: { id: string; workers: string[] }[] };
type Data = {
  fellowship?: { email_status?: string; previous_day: string; target_day: string; missing: string[]; data: { status: string; questions?: string[] }; fields: { host?: string; title?: string; series?: string; sequence?: number; sourceLinks?: { label: string; url: string }[] } }[];
  ppt_readiness?: { date: string; version?: string; status: string; missing?: string[]; error?: string };
  ppt_jobs?: { day: string; version: string; status: string; error?: string }[];
  identity_audit?: IdentityAudit;
  admin_monitor?: { last_check?: number; incidents: { id: string; label: string; active: number; count: number; resolution?: string; details?: { ref: string; label: string; failed_at: string; phase: string; diagnostic: Record<string, unknown>; outcome?: Record<string, unknown> }[] }[]; notices: { key: string; phase: string; status: string }[] };
  identities?: { sender_id: string; worker_name: string }[];
  settings: Settings; messages: Message[]; proposals: Proposal[];
  outbox: { key: string; text: string; status: string; error?: string; updated_at: string }[];
  bridge: { state: string; qr?: string; error?: string; account?: string; synced?: boolean; pending?: number; lastSyncAt?: string };
  worker?: { status: string; at: string; reminder?: string; error?: string };
  conflicts: { date: string; role: string; worker: string }[];
  sunday: string; missing: string[]; workers: string[];
  skeletons?: { date: string; missing: string[]; complete: boolean }[];
};
const labels: Record<string, string> = {
  queued: "等待處理", processing: "正在整理消息", review: "待核對", done: "已完成", applied: "已更新網站",
  irrelevant: "無須更新", error: "處理失敗", dismissed: "已略過", applying: "正在儲存",
  waiting_reply: "待補充／核對", resolved: "已由後續回答處理", cancelled: "已取消",
  held: "僅後台保留（未發送）", sent: "已發送", sending: "發送中", unknown: "發送結果待確認", ready: "已連線", disconnected: "未連接",
  offline: "橋接器未啟動", connecting: "連接中", qr: "等待手機掃碼", auth_failure: "需要重新登入",
  paused: "已暫停", idle: "等待新消息", waiting_for_messages: "有資料待處理，暫緩提醒",
  stale: "處理程序未更新",
  bridge_unavailable: "WhatsApp 未連線，暫緩提醒", bridge_not_synced: "等待群消息同步",
  bridge_stale: "同步狀態過期，暫緩提醒", complete: "資料齊全", not_due: "尚未到提醒時間",
  schedule_conflict: "本主日同工安排有衝突，請先處理",
  hymnYoutubeUrl: "詩歌 YouTube", responseHymnYoutubeUrl: "回應詩歌 YouTube",
  sermonSpeaker: "講員", sermonTitle: "講道題目", hymn: "詩歌", responseHymn: "回應詩歌", scripture: "讀經經文", donationAmount: "本週奉獻", presider: "司會", worshipLeader: "領詩", pianist: "司琴", hymnNotes: "詩歌演唱說明", responseHymnNotes: "回應詩歌演唱說明",
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
  const [alias, setAlias] = useState("");
  const [aliasWorker, setAliasWorker] = useState("");
  const [spouses, setSpouses] = useState<[string, string]>(["", ""]);
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
      <p className="mt-3 text-sm leading-6 text-slate-200">整理外出日期、講員、講道題目、詩歌、讀經與本週奉獻；支援不同同工分次補充。不清楚時先留在後台，獲准群內提問後才發問；補充資料後繼續登記。請假資料成功更新後回覆原群；啟用周五提醒後，下午 5 點檢查崇拜準備資料缺項，分別在負責群提醒。</p>
      <div className="mt-4 flex flex-wrap gap-4 text-sm underline"><Link href="/admin/sunday-service">主日表單</Link><Link href="/admin/sunday-workers">同工及外出日期</Link></div>
    </header>
    {(error || loadError) && <div role="alert" className="rounded-lg bg-red-50 p-4 text-red-800">{error || loadError}</div>}
    {feedback && <p role="status" className="rounded-lg bg-emerald-50 p-4 text-emerald-800">{feedback}</p>}
    {!data || !settings ? (loadError ? <button className={button} onClick={() => void load()}>重新載入助理狀態</button> : <p>正在載入助理狀態…</p>) : <>
      <div className="grid gap-4 sm:grid-cols-3">
        <section className={card}><h2 className="font-semibold">WhatsApp</h2><p className="mt-2">{labels[data.bridge.state] || data.bridge.state}</p><p className="mt-1 break-all text-xs text-slate-500">{data.bridge.account}</p><p className="mt-2 text-xs">{data.bridge.synced ? "群消息已同步" : "尚未完成同步"} · 等待傳送 {data.bridge.pending ?? 0} 則</p>{data.bridge.error && <p className="mt-2 text-sm text-amber-800">{data.bridge.error}</p>}</section>
        <section className={card}><h2 className="font-semibold">整理進度</h2><p className="mt-2">{labels[data.worker?.status || ""] || "處理程序尚未啟動"}</p><p className="mt-2 text-sm">{pending.length} 項待核對</p>{data.worker && <p className="mt-1 text-xs text-slate-500">最近更新 {new Date(data.worker.at).toLocaleString()}</p>}{data.worker?.error && <p className="mt-2 text-sm text-amber-800">{data.worker.error}</p>}</section>
        <section className={card}><h2 className="font-semibold">{data.sunday} 主日資料</h2><p className="mt-2 text-sm">{data.missing.length ? `尚缺：${data.missing.join("、")}` : "已齊全"}</p><p className="mt-2 text-xs text-slate-500">{labels[data.worker?.reminder || ""] || ""}</p></section>
      </div>
      <SundaySpeakerMail workers={data.workers} />
      <section className={card}>
        <h2 className="text-lg font-bold">雙週讀經團契</h2>
        <label className="mt-3 block"><input type="checkbox" checked={settings.fellowship_enabled ?? false} disabled={!settings.admin_group} onChange={e => patch({ fellowship_enabled: e.target.checked })} /> 在管理員群收集下次團契資料並接續通知 email</label>
        <p className="mt-2 text-sm text-slate-600">查經結束後的主日下午 3 點詢問；於緊接的週四前回覆，該週四上午 9 點仍缺資料就催補。時間皆為達拉斯時間。日期依排程填入，收集主講、主題、系列與序號；序號若無可回覆「無」。來源連結選填，不影響資料齊備或寄信。</p>
        <Link href="/admin/fellowship" className="mt-3 inline-block text-sm underline">查看團契表單與通知 email</Link>
        {settings.fellowship_enabled && !data.fellowship?.length && <p className="mt-3 text-sm text-slate-500">下一次查經結束後，會在主日建立收集待辦。</p>}
        {data.fellowship?.map(item => <div key={item.previous_day} className="mt-3 rounded border p-3 text-sm">
          <p className="font-semibold">{item.target_day} 團契 · {item.data.questions?.length ? "待核對" : item.missing.length ? "待補充" : "資料齊備"}</p>
          <p className="mt-1">主講：{item.fields.host || "待補充"}；主題：{item.fields.title || "待補充"}；系列：{item.fields.series || "待補充"}</p>
          <p className="mt-1">序號：{item.fields.sequence ?? "未填／無"}；來源連結（選填）：{item.fields.sourceLinks?.length || 0} 筆</p>
          <p className="mt-1">通知 email：{item.email_status === "sent" ? "已寄出" : item.email_status === "sending" ? "寄送中" : item.email_status === "retryable" ? "正在重試寄信" : item.email_status === "expired" ? "已過寄送時段，請管理員核對" : ["unknown", "error"].includes(item.email_status || "") ? "寄送需管理員核對，未繼續自動重寄" : item.missing.length || item.data.questions?.length ? "等待資料補齊" : "依排程寄出"}</p>
          {!!item.missing.length && <p className="mt-1 text-amber-800">尚缺：{item.missing.join("、")}</p>}
          {!!item.data.questions?.length && <p className="mt-1 text-amber-800">待核對：{item.data.questions.join("；")}</p>}
        </div>)}
      </section>
      <section className={card}><h2 className="text-lg font-bold">主日 PPT</h2>
        <label className="mt-3 block"><input type="checkbox" checked={settings.auto_generate_ppt ?? true} onChange={e => patch({ auto_generate_ppt: e.target.checked })} /> 資料齊備後自動產生 PPT 並通知網站管理員群</label>
        <p className="mt-2 text-sm text-slate-600">司琴可留空。生成前檢查歌詞、讀經安排及未來兩週骨架；資料更正後產生新版。</p>
        {data.ppt_readiness && <div className="mt-3 text-sm"><p>{data.ppt_readiness.date} · {data.ppt_readiness.status === "ready" ? "PPT 已準備好" : data.ppt_readiness.status === "error" ? "PPT 產生失敗" : "等待資料齊備"}</p>
          {!!data.ppt_readiness.missing?.length && <p className="mt-2">{data.ppt_readiness.missing.join("、")}</p>}
          {data.ppt_readiness.error && <p className="mt-2 text-red-700">{data.ppt_readiness.error}</p>}
          {data.ppt_readiness.status === "ready" && data.ppt_readiness.version && <a className="mt-2 inline-block underline" href={`/api/admin/sunday-assistant/ppt-ready/${data.ppt_readiness.date}/${data.ppt_readiness.version}`}>下載已產生 PPT</a>}
          {data.ppt_readiness.status === "error" && <button className={`${button} mt-2`} disabled={busy} onClick={() => void action(() => call(`ppt-retry/${data.ppt_readiness?.date}`, "POST"), "已排入重新產生 PPT")}>重新產生 PPT</button>}
        </div>}
      </section>
      {!!data.skeletons?.length && <section className={card}><h2 className="text-lg font-bold">未來兩週主日骨架</h2><p className="mt-2 text-sm text-slate-600">先確定講員、司會、領詩；其餘資料可在主日前補齊。已有預填安排保留，司會缺項自動按資格、輪值及請假資料分配。</p>{!!settings.worship_rotation?.length && <p className="mt-2 text-sm">領詩輪值：{settings.worship_rotation.join(" ／ ")}；沿用已有預填安排，群內明確更正優先。</p>}{data.skeletons.map(row => <p className="mt-2 text-sm" key={row.date}>{row.date} · {row.complete ? "骨架齊全" : `待補：${row.missing.join("、")}`}</p>)}</section>}
      <section className={card}>
        <h2 className="text-lg font-bold">連接與設定</h2>
        <p className="mt-2 text-sm text-slate-600">WhatsApp Web 接入使用非官方工具，可能因平台更新斷線或影響帳號。連接後選定外出、講道及詩歌資料來源群；首次同步最近 30 天可讀取的文字消息。</p>
        <div className="mt-4 flex flex-wrap gap-3">
          <button className={button} disabled={busy || data.bridge.state === "ready"} onClick={() => void action(() => call("connect", "POST", {}), "請使用手機 WhatsApp 的連結裝置掃碼")}>連接 WhatsApp</button>
          <button className={button} disabled={busy || data.bridge.state !== "ready"} onClick={() => void action(async () => { const result = await call<{ groups: typeof groups }>("groups"); setGroups(result.groups); }, "群名單已載入")}>讀取群名單</button>
        </div>
        {data.bridge.qr && <div className="mt-4"><img src={data.bridge.qr} width={240} height={240} alt="WhatsApp 連結裝置 QR 碼" /><p className="text-sm">手機 WhatsApp → 設定 → 連結裝置</p></div>}
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          {([ ["availability_group", "availability_group_name", "外出及奉獻群"], ["service_group", "service_group_name", "講道資料群"], ["songs_group", "songs_group_name", "主日詩歌與回應詩歌群"] ] as const).map(([key, nameKey, label]) => <label key={key} className="space-y-2 text-sm"><span className="font-semibold">{label} · {settings[nameKey]}</span><select className={input} value={settings[key] || ""} onChange={e => { const found = groups.find(g => g.id === e.target.value); patch({ [key]: e.target.value, ...(found ? { [nameKey]: found.name } : {}) }); }}><option value="">請連接後選擇群</option>{settings[key] && !groups.some(g => g.id === settings[key]) && <option value={settings[key] || ""}>{settings[nameKey]}</option>}{groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>)}
        </div>
        <p className="mt-3 text-sm text-slate-600">主要分工：教會群收集外出及奉獻；講道群收集講員、題目、讀經；詩歌群收集領詩、主日詩歌與回應詩歌。明確的跨群補充也可收集，提醒仍發到主要來源群。只有舊設定未指定詩歌群時，才沿用原講道群收集方式。</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {([["response_hymn_group", "回應詩歌來源", "預設：詩歌群（未設詩歌群時為講道群）"], ["offering_group", "本週奉獻來源", "預設：達拉斯聖道教會（未設詩歌群時沿用講道群）"]] as const).map(([key, label, fallback]) => <label className="text-sm" key={key}>{label}<select className={input} value={settings[key] || ""} onChange={e => patch({ [key]: e.target.value })}><option value="">{fallback}</option>{(["availability_group", "service_group", "songs_group"] as const).filter(role => settings[role]).map(role => <option value={settings[role]} key={role}>{settings[`${role}_name`]}</option>)}</select></label>)}
        </div>
        <div className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
          <label><input type="checkbox" checked={settings.enabled} onChange={e => patch({ enabled: e.target.checked })} /> 啟用消息收集與整理</label>
          <label><input type="checkbox" checked={settings.auto_apply} onChange={e => patch({ auto_apply: e.target.checked })} /> 自動登記所選群內的明確資料</label>
          <label><input type="checkbox" checked={settings.maintain_future_skeletons ?? true} onChange={e => patch({ maintain_future_skeletons: e.target.checked })} /> 維持未來兩個主日骨架，缺項依發問設定留後台或在負責群提問</label>
          <label><input type="checkbox" checked={settings.collect_cross_group ?? true} onChange={e => patch({ collect_cross_group: e.target.checked })} /> 收集三個來源群中的明確跨群資料</label>
          <label><input type="checkbox" checked={settings.admin_clarifications_enabled ?? false} onChange={e => patch({ admin_clarifications_enabled: e.target.checked })} /> 澄清統一送管理員群，回答後接續處理（優先於來源群設定）</label>
          <label><input type="checkbox" checked={settings.group_questions_enabled ?? false} onChange={e => patch({ group_questions_enabled: e.target.checked })} /> 允許在來源群發送追問（僅在未啟用管理群澄清時生效）</label>
          <label><input type="checkbox" checked={settings.reminders_enabled} onChange={e => patch({ reminders_enabled: e.target.checked })} /> 啟用周五缺項提醒</label>
          <label className="flex items-center gap-3">周五（達拉斯時間）<input type="time" value={`${String(settings.friday_hour).padStart(2, "0")}:${String(settings.friday_minute).padStart(2, "0")}`} onChange={e => { const [h, m] = e.target.value.split(":").map(Number); patch({ friday_hour: h, friday_minute: m }); }} /></label>
        </div>
        <p className="mt-3 text-xs text-slate-500">所選群的不同同工可分次提供資料。更正既有內容時先讀取並核對；日期或對象不清楚時，啟用管理群澄清便送管理員群，回答後接續；否則依來源群設定或保留後台。首次歷史同步只提供預覽。</p>
        <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
          <h3 className="font-semibold">Agent 內部故障求助</h3>
          <p className="mt-2">執行失敗立即求助；工作程序超過五分鐘沒有更新、持續連線故障或反覆崩潰時通知管理員。缺資料、等待澄清及一般人工核對不通知。獨立監測，不依賴模型；事件去重，恢復後回報。</p>
          <label className="mt-3 block"><input type="checkbox" checked={settings.admin_help_enabled ?? false} onChange={e => patch({ admin_help_enabled: e.target.checked })} /> 啟用內部故障通知網站管理員</label>
          <label className="mt-3 block">網站管理員求助群<select aria-label="網站管理員求助群" className={`${input} mt-1`} value={settings.admin_group || ""} onChange={e => patch({ admin_group: e.target.value, admin_group_name: groups.find(g => g.id === e.target.value)?.name || "網站管理員" })}><option value="">請讀取群名單後選擇</option>{settings.admin_group && !groups.some(g => g.id === settings.admin_group) && <option value={settings.admin_group}>{settings.admin_group_name}</option>}{groups.filter(g => ![settings.availability_group, settings.service_group, settings.songs_group].includes(g.id)).map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
          <p className="mt-2 text-xs">監測最近更新：{data.admin_monitor?.last_check ? new Date(data.admin_monitor.last_check * 1000).toLocaleString() : "尚未啟用"}。傳送失敗保留佇列；結果不明時不盲目重發。通知不含群原文、姓名、奉獻金額或密鑰。</p>
          {!!data.admin_monitor?.incidents.length && <ul className="mt-2 space-y-2">{data.admin_monitor.incidents.map(i => <li key={i.id}>
            {i.id} · {i.label}（{i.count}項）· {i.active ? "需要管理員協助" : i.resolution === "completed" ? "補做完成（已核對回執）" : "故障條件已解除；任務完成待核實"}
            {i.details?.length ? <details className="mt-1"><summary>失敗原因、已完成步驟與處理回執</summary>{i.details.map(d => <div key={d.ref} className="mt-2 rounded border bg-white p-2">
              <p>{d.label} · 失敗時間：{new Date(d.failed_at).toLocaleString()} · {d.phase === "completed" ? "已有完成回執" : d.phase === "resumed" ? "已開始重試，尚未確認完成" : d.phase === "health_only" ? "目前檢查通過，完成待核實" : "失敗已記錄"}</p>
              <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify({ failure: d.diagnostic, outcome: d.outcome }, null, 2)}</pre>
            </div>)}</details> : <p className="text-xs text-slate-500">舊事件未保存任務詳情，無法由故障解除推斷已完成。</p>}
          </li>)}</ul>}
          <button className={`${button} mt-3`} disabled={busy || dirty || !settings.admin_help_enabled || !settings.admin_group} onClick={() => void action(() => call("admin-help-test", "POST", {}), "已排入管理群上線測試通知（明確標示測試、無需處理）")}>向管理群發送測試通知</button>
        </div>
        <details className="mt-5 rounded-lg border p-3"><summary className="cursor-pointer font-semibold">發送人姓名對應</summary>
          <p className="mt-3 text-sm">從下方原消息複製發送人 ID，對應到既有同工。每個帳號只對應本人；夫妻關係另存。首次自報姓名核對後會記住，下次「我」不再重問。</p>
          <input className={`${input} mt-3`} placeholder="WhatsApp 發送人 ID" value={sender} onChange={e => setSender(e.target.value)} />
          <div className="my-3 flex max-h-40 flex-wrap gap-3 overflow-auto text-sm">{data.workers.map(name => <label key={name}><input type="radio" name="sender-worker" checked={selectedWorkers.includes(name)} onChange={() => setSelectedWorkers([name])} /> {name}</label>)}</div>
          <div className="flex flex-wrap gap-3"><button className={button} disabled={!sender.trim() || !selectedWorkers.length} onClick={() => { patch({ sender_workers: { ...settings.sender_workers, [sender.trim()]: selectedWorkers } }); setSelectedWorkers([]); }}>加入姓名對應</button></div>
          <ul className="mt-3 space-y-2 text-sm">{Object.entries(settings.sender_workers).map(([id, names]) => <li key={id} className="break-all">{id} → {names.join("、")} <button className="text-red-700 underline" onClick={() => { const next = { ...settings.sender_workers }; delete next[id]; patch({ sender_workers: next }); }}>移除</button></li>)}</ul>
          {!!data.identities?.length && <div className="mt-3 text-sm"><p className="font-semibold">已核對的自我介紹</p>{data.identities.map(item => <p className="break-all" key={item.sender_id}>{item.sender_id} → {item.worker_name}</p>)}</div>}
          <p className="mt-4 text-sm font-semibold">已確認夫妻</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {([0, 1] as const).map(index => <select aria-label={index === 0 ? "夫妻第一位" : "夫妻第二位"} className={`${input} sm:w-auto`} key={index} value={spouses[index]} onChange={e => setSpouses(old => index === 0 ? [e.target.value, old[1]] : [old[0], e.target.value])}><option value="">選擇同工</option>{data.workers.map(name => <option key={name}>{name}</option>)}</select>)}
            <button className={button} disabled={!spouses[0] || !spouses[1] || spouses[0] === spouses[1] || (settings.households || []).some(pair => pair.some(name => spouses.includes(name)))} onClick={() => { patch({ households: [...(settings.households || []), spouses] }); setSpouses(["", ""]); }}>加入夫妻關係</button>
          </div>
          {(settings.households || []).map(pair => <p className="mt-2 text-sm" key={pair.join("/")}>{pair.join(" ／ ")} <button aria-label={`移除夫妻關係 ${pair.join("、")}`} className="ml-2 text-red-700 underline" onClick={() => patch({ households: settings.households.filter(item => item !== pair) })}>移除</button></p>)}
          <p className="mt-4 text-sm font-semibold">已確認姓名別名</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input aria-label="姓名別名" className={`${input} sm:w-auto`} placeholder="WhatsApp 稱呼或英文名" value={alias} onChange={e => setAlias(e.target.value)} />
            <select aria-label="別名對應同工" className={`${input} sm:w-auto`} value={aliasWorker} onChange={e => setAliasWorker(e.target.value)}><option value="">對應網站同工</option>{data.workers.map(name => <option key={name}>{name}</option>)}</select>
            <button className={button} disabled={!alias.trim() || !aliasWorker} onClick={() => { patch({ worker_aliases: { ...(settings.worker_aliases || {}), [alias.trim()]: aliasWorker } }); setAlias(""); setAliasWorker(""); }}>加入或更新別名</button>
          </div>
          {Object.entries(settings.worker_aliases || {}).map(([alias, name]) => <p className="mt-2 text-sm" key={alias}>{alias} → {name} <button aria-label={`移除別名 ${alias}`} className="ml-2 text-red-700 underline" onClick={() => { const next = { ...settings.worker_aliases }; delete next[alias]; patch({ worker_aliases: next }); }}>移除</button></p>)}
          <p className="mt-3 text-xs text-slate-500">修改後請按「儲存設定」。對應保存在網站後端，重新整理或重啟後仍保留；只識別登記對象，不建立登入帳號或授予權限。</p>
        </details>
        <div className="mt-5 flex flex-wrap gap-3"><button className={button} disabled={busy || !dirty} onClick={() => void save()}>儲存設定</button><button className={button} disabled={busy || dirty || !settings.enabled} onClick={() => void action(() => call("tick", "POST", {}), "已要求處理待辦，進度會自動更新")}>處理待辦</button>{dirty && <span className="self-center text-sm text-amber-800">有尚未儲存的設定</span>}</div>
      </section>
      <section className={card}>
        <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-bold">WhatsApp 身分核對</h2><button className={button} disabled={busy || dirty || data.bridge.state !== "ready"} onClick={() => void action(() => call("identity-audit", "POST", {}), "已核對三群成員；姓名候選尚未自動採用")}>重新核對三群成員</button></div>
        <p className="mt-3 text-sm">只用 WhatsApp 回傳的 LID／電話帳號關係連接 ID；顯示名稱只供核對，不自動認人。每個帳號對應本人，夫妻關係分開保存。有衝突時停止身分推定。</p>
        <p className="mt-2 text-xs text-slate-500">{data.identity_audit?.checked_at ? `最近核對：${new Date(data.identity_audit.checked_at).toLocaleString()}` : "尚未取得群成員名冊"}</p>
        <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th className="p-2">WhatsApp 聯絡人／ID</th><th className="p-2">狀態與姓名候選</th><th className="p-2">網站同工本人</th></tr></thead><tbody>{data.identity_audit?.members.map(member => <tr key={member.id} className="border-t align-top">
          <td className="p-2"><p>{member.labels.join(" ／ ") || "沒有姓名"}{member.saved_contact ? "（手機聯絡人）" : ""}</p>{member.ids.map(id => <p className="break-all text-xs text-slate-500" key={id}>{id}</p>)}</td>
          <td className="p-2"><p className={member.status === "conflict" ? "text-red-700" : member.status === "confirmed" ? "text-emerald-700" : "text-amber-800"}>{member.status === "conflict" ? "衝突，需核對" : member.status === "confirmed" ? "已對應" : "待你確認"}</p>{!!member.candidates.length && <p className="mt-1">候選：{member.candidates.join("、")}</p>}{!!member.lookup_errors.length && <p className="mt-1 text-xs text-amber-800">部分帳號或姓名無法查回，保留原 ID</p>}</td>
          <td className="p-2"><select className={input} aria-label={`核對 ${member.labels.join("、") || member.id} 的本人`} disabled={member.status === "conflict"} value={settings.sender_workers[member.id]?.[0] || member.worker || ""} onChange={event => { const next = { ...settings.sender_workers }; for (const id of member.ids) { if (event.target.value) next[id] = [event.target.value]; else delete next[id]; } patch({ sender_workers: next }); }}><option value="">尚未確認（不推定本人）</option>{data.workers.map(name => <option key={name}>{name}</option>)}</select></td>
        </tr>)}</tbody></table></div>
        {!!data.identity_audit?.unseen_mappings?.length && <p className="mt-3 text-sm text-amber-800">既有對應未出現在本次群名冊，需另外核對：{data.identity_audit.unseen_mappings.map(item => `${item.workers.join("、")}（${item.id}）`).join("；")}</p>}
        <p className="mt-3 text-xs text-slate-500">選定姓名後按上方「儲存設定」才生效；不會向群發問。未對應帳號：{data.identity_audit?.workers_without_account.join("、") || "無"}</p>
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
