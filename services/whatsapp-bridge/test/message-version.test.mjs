import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { messageVersion } from "../message-version.mjs";

const original = {id:{_serialized:"source-id"}, timestamp:1793572500, body:"A", type:"chat"};

test("original IDs remain compatible with existing inbound journals", () => {
  const expected="source-id:v:"+createHash("sha256").update(JSON.stringify(["A","chat"])).digest("hex").slice(0,16);
  assert.equal(messageVersion(original).id,expected);
  assert.equal(messageVersion(original).source_sent_at,null);
});

test("A to B to A edits are distinct and repeated delivery is idempotent", () => {
  const b={...original,body:"B",latestEditSenderTimestampMs:1793572505000,latestEditMsgKey:{$1:"edit-1"}};
  const a={...original,latestEditSenderTimestampMs:1793572510000,latestEditMsgKey:{_serialized:"edit-2"}};
  assert.equal(new Set([messageVersion(original).id,messageVersion(b,true).id,messageVersion(a,true).id]).size,3);
  assert.equal(messageVersion(b,true).id,messageVersion(b).id);
  assert.equal(messageVersion(b,true).source_sent_at,new Date(original.timestamp*1000).toISOString());
  assert.equal(messageVersion(a,true).sent_at,new Date(a.latestEditSenderTimestampMs).toISOString());
});

test("missing edit timestamp is flagged instead of inventing version chronology", () => {
  assert.equal(messageVersion({...original,body:"B"},true).unverified_edit,true);
  assert.equal(messageVersion({...original,latestEditSenderTimestampMs:0},true).unverified_edit,true);
});
