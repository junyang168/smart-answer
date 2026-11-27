import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Durable } from "../durable.mjs";

test("inbound queue survives restart and duplicate delivery", () => {
  const root = mkdtempSync(join(tmpdir(), "sunday-bridge-"));
  try {
    const path = join(root, "journal.json");
    const first = new Durable(path);
    assert.equal(first.enqueue({ message_id: "m1", text: "hi" }), true);
    const next = new Durable(path);
    assert.equal(next.data.pending.m1.text, "hi");
    assert.equal(next.enqueue({ message_id: "m1" }), false);
    next.delivered("m1");
    assert.equal(new Durable(path).enqueue({ message_id: "m1" }), false);
  } finally { rmSync(root, { recursive: true }); }
});

test("concurrent/repeated sends and restart do not duplicate external messages", async () => {
  const root = mkdtempSync(join(tmpdir(), "sunday-bridge-"));
  try {
    const path = join(root, "journal.json");
    const journal = new Durable(path);
    let count = 0;
    const send = async () => { count++; return "remote"; };
    await Promise.all([journal.send("key", "group", "text", send), journal.send("key", "group", "text", send)]);
    assert.equal(count, 1);
    const next = new Durable(path);
    assert.equal((await next.send("key", "group", "text", send)).message_id, "remote");
    assert.equal(count, 1);
    await assert.rejects(next.send("key", "group", "changed", send), /different content/);
  } finally { rmSync(root, { recursive: true }); }
});

test("uncertain send outcome stays uncertain across retries and restart", async () => {
  const root = mkdtempSync(join(tmpdir(), "sunday-bridge-"));
  try {
    const path = join(root, "journal.json");
    const journal = new Durable(path);
    let count = 0;
    const send = async () => { count++; throw new Error("lost connection"); };
    assert.equal((await journal.send("key", "group", "text", send)).status, "unknown");
    assert.equal((await new Durable(path).send("key", "group", "text", send)).status, "unknown");
    assert.equal(count, 1);
  } finally { rmSync(root, { recursive: true }); }
});
