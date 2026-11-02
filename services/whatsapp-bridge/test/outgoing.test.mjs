import test from "node:test";
import assert from "node:assert/strict";
import { registeredMessage } from "../outgoing.mjs";

test("only registered reply, reminder and clarification envelopes are accepted", () => {
  assert.equal(registeredMessage("clarify:1:2", "【主日助理提問】請問哪一天？"), true);
  assert.equal(registeredMessage("confirmed:m1", "【主日資料回覆】收到，已更新"), true);
  assert.equal(registeredMessage("ack:m1", "【請假登記回覆】收到"), true);
  assert.equal(registeredMessage("friday:2026-11-08", "【主日資料提醒】讀經尚缺"), true);
  assert.equal(registeredMessage("clarify:1", "任意內容"), false);
  assert.equal(registeredMessage("something", "【主日助理提問】"), false);
  assert.equal(registeredMessage(null, "【主日助理提問】"), false);
});


test('questions and reminders fail closed unless explicitly enabled in fresh configuration', async () => {
  const { allowedBySettings } = await import('../outgoing.mjs');
  assert.equal(allowedBySettings({}, 'clarify:1:2'), false);
  assert.equal(allowedBySettings({allow_questions: false}, 'clarify:1:2'), false);
  assert.equal(allowedBySettings({allow_questions: true}, 'clarify:1:2'), true);
  assert.equal(allowedBySettings({}, 'friday:2026-11-08'), false);
  assert.equal(allowedBySettings({allow_reminders: true}, 'friday:2026-11-08'), true);
  assert.equal(allowedBySettings({}, 'ack:message'), true);
});
