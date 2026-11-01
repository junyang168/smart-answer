import { mkdirSync, readFileSync, writeFileSync, renameSync, chmodSync } from "node:fs";
import { dirname } from "node:path";
import { createHash } from "node:crypto";

export class Durable {
  constructor(path) {
    this.path = path;
    mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    try { this.data = JSON.parse(readFileSync(path, "utf8")); }
    catch (error) {
      if (error.code !== "ENOENT") throw error;
      this.data = { pending: {}, seen: {}, sent: {}, cursors: {} };
    }
    // A process exit between send and acknowledgement is an uncertain outcome.
    for (const entry of Object.values(this.data.sent)) {
      if (entry.status === "sending") entry.status = "unknown";
    }
    this.save();
  }
  save() {
    writeFileSync(this.path + ".tmp", JSON.stringify(this.data), { mode: 0o600 });
    chmodSync(this.path + ".tmp", 0o600);
    renameSync(this.path + ".tmp", this.path);
  }
  enqueue(message) {
    if (this.data.seen[message.message_id] || this.data.pending[message.message_id]) return false;
    this.data.pending[message.message_id] = message;
    this.save();
    return true;
  }
  delivered(id) {
    this.data.seen[id] = true;
    delete this.data.pending[id];
    this.save();
  }
  async send(key, group, text, send) {
    const digest = createHash("sha256").update(JSON.stringify([group, text])).digest("hex");
    if (this.data.sent[key]) {
      if (this.data.sent[key].digest !== digest) throw new Error("Idempotency key reused with different content");
      return this.data.sent[key];
    }
    this.data.sent[key] = { status: "sending", digest, group_id: group, text };
    this.save();
    try {
      const id = await send();
      this.data.sent[key] = { ...this.data.sent[key], status: "sent", message_id: id };
    } catch {
      this.data.sent[key].status = "unknown";
    }
    this.save();
    return this.data.sent[key];
  }
}
