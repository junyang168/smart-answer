import test from "node:test";
import assert from "node:assert/strict";
import { senderId } from "../sender.mjs";

test("own linked-device messages use account identity, other senders retain stable LID", () => {
  assert.equal(senderId({fromMe:true,author:"999:3@lid"},"123@c.us"), "123@c.us");
  assert.equal(senderId({fromMe:false,author:"999:3@lid"},"123@c.us"), "999@lid");
  assert.equal(senderId({fromMe:false,author:"999:6@lid"},"123@c.us"), "999@lid");
});
