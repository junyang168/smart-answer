import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { installMsgKeyCompatibility } from "../msg-key-compat.mjs";

test("MsgKey renamed getter restores lookups and preserves legacy assignments", () => {
  class MsgKey { constructor(id) { this.$1 = id; } }
  vm.runInNewContext(`(${installMsgKeyCompatibility.toString()})()`, { window: { require: () => MsgKey } });
  const key = new MsgKey("false_group_message");
  assert.equal(key._serialized, "false_group_message");
  key._serialized = "legacy";
  assert.equal(key._serialized, "legacy");
  assert.equal(key.$1, "false_group_message");
  assert.equal(JSON.parse(JSON.stringify(key))._serialized, "legacy");
});

test("MsgKey compatibility leaves existing native getter unchanged", () => {
  class MsgKey { get _serialized() { return "native"; } }
  const getter = Object.getOwnPropertyDescriptor(MsgKey.prototype, "_serialized").get;
  vm.runInNewContext(`(${installMsgKeyCompatibility.toString()})()`, { window: { require: () => MsgKey } });
  assert.equal(Object.getOwnPropertyDescriptor(MsgKey.prototype, "_serialized").get, getter);
});
