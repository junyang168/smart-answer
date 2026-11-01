import http from "node:http";
import { timingSafeEqual, createHash } from "node:crypto";
import { mkdirSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { Durable } from "./durable.mjs";
import { senderId } from "./sender.mjs";
import { registeredMessage } from "./outgoing.mjs";

const secret = process.env.SUNDAY_ASSISTANT_SECRET;
const stateDir = resolve(process.env.SUNDAY_WHATSAPP_STATE_DIR || "./.state");
const backend = (process.env.FULL_ARTICLE_SERVICE_URL || "http://127.0.0.1:8555").replace(/\/$/, "");
const port = Number(process.env.SUNDAY_WHATSAPP_PORT || 8766);
if (!secret || secret.length < 24) throw new Error("Set SUNDAY_ASSISTANT_SECRET (at least 24 characters)");
const apiBase = new URL(backend);
if (!["127.0.0.1", "localhost", "[::1]"].includes(apiBase.hostname)) throw new Error("Backend must be local");
mkdirSync(stateDir, { recursive: true, mode: 0o700 });
// Run through scripts/run-sunday-assistant.py: its OS lock prevents a second
// supported supervisor from owning this session and independently sending.
const journal = new Durable(join(stateDir, "journal.json"));
let client, connecting = false, qr = null, state = "disconnected", lastError = null;
let groups = [], config = { enabled: false, groups: [] }, synced = false, lastSyncAt = null;
let lastHistorySync = 0;

async function backendCall(path, data) {
  const response = await fetch(backend + "/admin/sunday-assistant/" + path, {
    method: data ? "POST" : "GET", headers: { Authorization: `Bearer ${secret}`, "Content-Type": "application/json" },
    body: data ? JSON.stringify(data) : undefined, signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error(`Backend ${path}: HTTP ${response.status}`);
  return response.json();
}

function isOwnReply(message) {
  return message.fromMe && Object.values(journal.data.sent).some(x => x.group_id === (message.to || message.from) && x.text === message.body);
}

async function capture(message, edited = false, initialSync = false) {
  const group = message.fromMe ? message.to : message.from;
  if (!config.enabled || !config.groups.includes(group) || isOwnReply(message)) return;
  if (message.type === "revoked") {
    lastError = "群消息已撤回，請在後台核對可能已套用的資料";
    return;
  }
  if (!["chat", "image", "video", "audio", "ptt", "document"].includes(message.type)) return;
  let quoted = null;
  if (message.hasQuotedMsg) {
    try { quoted = (await message.getQuotedMessage()).id._serialized; } catch { /* context remains optional */ }
  }
  const id = message.id._serialized + ":v:" + createHash("sha256").update(JSON.stringify([message.body || "", message.type])).digest("hex").slice(0, 16);
  journal.enqueue({ message_id: id, group_id: group,
    sender_id: senderId(message, client.info.wid._serialized),
    sent_at: new Date(message.timestamp * 1000).toISOString(), text: message.body || "",
    source_id: message.id._serialized, quoted_message_id: quoted, unsupported_media: message.type !== "chat", initial_sync: initialSync });
}

async function connect() {
  if (connecting || state === "ready" || state === "qr") return;
  connecting = true; state = "connecting"; lastError = null; synced = false;
  try {
    const [{ default: wweb }, { default: qrcode }] = await Promise.all([import("whatsapp-web.js"), import("qrcode")]);
    const { Client, LocalAuth } = wweb;
    if (client) await client.destroy().catch(() => {});
    const executable = process.env.SUNDAY_WHATSAPP_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
    if (!existsSync(executable)) throw new Error("Configure SUNDAY_WHATSAPP_CHROME with an installed Chrome executable");
    client = new Client({ authStrategy: new LocalAuth({ dataPath: join(stateDir, "auth") }),
      puppeteer: { headless: true, executablePath: executable } });
    client.on("qr", async code => { qr = await qrcode.toDataURL(code); state = "qr"; });
    client.on("ready", () => { state = "ready"; qr = null; connecting = false; lastHistorySync = 0; });
    client.on("auth_failure", () => { state = "auth_failure"; connecting = false; lastError = "WhatsApp 登入失敗，請重新連接"; });
    client.on("disconnected", () => { state = "disconnected"; synced = false; connecting = false; lastError = "WhatsApp 已斷線，請重新連接"; });
    client.on("message_create", message => { capture(message).catch(error => { lastError = error.message; synced = false; }); });
    client.on("message_edit", message => { capture(message, true).catch(error => { lastError = error.message; synced = false; }); });
    client.on("message_revoke_everyone", (message, before) => {
      const group = message.fromMe ? message.to : message.from;
      if (!config.enabled || !config.groups.includes(group)) return;
      synced = false;
      journal.enqueue({ message_id: message.id._serialized + ":revoked", group_id: group,
        sender_id: senderId(message, client.info.wid._serialized),
        sent_at: new Date().toISOString(), text: "已撤回的消息：" + (before?.body || "（原文無法取得）"), revoked: true });
    });
    await client.initialize();
  } catch (error) { state = "error"; lastError = error.message; connecting = false; }
}

async function poll() {
  try {
    const nextConfig = await backendCall("bridge-config");
    if (lastError === "fetch failed" || lastError?.startsWith("Backend bridge-config:")) lastError = null;
    if (JSON.stringify(nextConfig) !== JSON.stringify(config)) { synced = false; lastHistorySync = 0; }
    config = nextConfig;
    if (state === "ready" && config.enabled) {
      if (Date.now() - lastHistorySync > 60000) {
        synced = false;
        const chats = await client.getChats();
        groups = chats.filter(c => c.isGroup).map(c => ({ id: c.id._serialized, name: c.name }));
        for (const group of config.groups) {
          const chat = chats.find(c => c.id._serialized === group);
          if (!chat) throw new Error(`無法讀取指定群 ${group}`);
          const initialSync = !journal.data.cursors[group];
          const cutoff = journal.data.cursors[group] || Date.now() / 1000 - 30 * 86400;
          const limit = Number(process.env.SUNDAY_WHATSAPP_HISTORY_LIMIT || 1000);
          const messages = await chat.fetchMessages({ limit });
          if (messages.length >= limit && messages[0].timestamp > cutoff - 120) throw new Error("歷史消息超過讀取上限，請提高 HISTORY_LIMIT 後重新同步");
          // Revisit the retained 30-day window: a message may have been edited
          // while offline without receiving a new original timestamp.
          const windowStart = initialSync ? cutoff - 120 : Date.now() / 1000 - 30 * 86400;
          for (const message of messages) if (message.timestamp >= windowStart) await capture(message, false, initialSync);
          journal.data.cursors[group] = Date.now() / 1000;
          journal.save();
        }
        lastHistorySync = Date.now();
      }
    }
    for (const message of Object.values(journal.data.pending).sort((a, b) => a.sent_at.localeCompare(b.sent_at))) {
      if (!config.groups.includes(message.group_id)) throw new Error("有待送消息所屬群已改變，請核對橋接記錄");
      await backendCall("ingest", message);
      journal.delivered(message.message_id);
    }
    if (state === "ready" && config.enabled && !Object.keys(journal.data.pending).length && lastHistorySync) {
      synced = true; lastSyncAt = new Date().toISOString(); lastError = null;
    }
  } catch (error) { lastError = error.message; synced = false; }
  setTimeout(poll, 10000);
}

function authorized(req) {
  const supplied = Buffer.from(req.headers.authorization || "");
  const expected = Buffer.from(`Bearer ${secret}`);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

async function bodyOf(req) {
  let body = "";
  for await (const chunk of req) {
    body += chunk;
    if (Buffer.byteLength(body) > 64000) throw new Error("Request too large");
  }
  return JSON.parse(body || "{}");
}

const server = http.createServer(async (req, res) => {
  const reply = (code, data) => { res.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" }); res.end(JSON.stringify(data)); };
  if (!authorized(req)) return reply(401, { error: "Unauthorized" });
  try {
    if (req.method === "GET" && req.url === "/status") return reply(200, {
      state, qr, error: lastError, synced, lastSyncAt, pending: Object.keys(journal.data.pending).length,
      account: state === "ready" ? client.info.wid._serialized : null,
    });
    if (req.method === "GET" && req.url === "/groups") {
      if (state !== "ready") return reply(409, { error: "WhatsApp not ready" });
      groups = (await client.getChats()).filter(c => c.isGroup).map(c => ({ id: c.id._serialized, name: c.name }));
      return reply(200, { groups });
    }
    if (req.method === "POST" && req.url === "/connect") { void connect(); return reply(202, { state: "connecting" }); }
    if (req.method === "GET" && req.url.startsWith("/outbox/")) {
      const key = decodeURIComponent(req.url.slice(8));
      const result = journal.data.sent[key];
      return reply(200, result ? { status: result.status, message_id: result.message_id } : { status: "unknown" });
    }
    if (req.method === "POST" && req.url === "/send") {
      const { key, group_id, text } = await bodyOf(req);
      // Refresh authorization immediately before an external action.
      config = await backendCall("bridge-config");
      if (state !== "ready" || !config.enabled || !config.groups.includes(group_id)) return reply(409, { error: "Group is not connected/enabled" });
      if (!registeredMessage(key, text)) {
        return reply(422, { error: "Only registered assistant replies, questions and Friday reminders may be sent" });
      }
      if (key.startsWith("friday:") && (!synced || Object.keys(journal.data.pending).length || !lastSyncAt || Date.now() - Date.parse(lastSyncAt) > 60000)) {
        return reply(200, { status: "deferred" });
      }
      const result = await journal.send(key, group_id, text, async () => {
        const sent = await client.sendMessage(group_id, text);
        return sent.id._serialized;
      });
      return reply(200, { status: result.status, message_id: result.message_id });
    }
    return reply(404, { error: "Not found" });
  } catch (error) { return reply(500, { error: error.message }); }
});

server.listen(port, "127.0.0.1", () => process.stdout.write(`WhatsApp bridge listening on 127.0.0.1:${port}; connect from the admin page\n`));
void poll();
if (process.env.SUNDAY_WHATSAPP_AUTOCONNECT === "1") void connect();
async function stop() { server.close(); if (client) await client.destroy().catch(() => {}); process.exit(0); }
process.on("SIGTERM", stop); process.on("SIGINT", stop);
