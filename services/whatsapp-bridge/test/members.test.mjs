import test from "node:test";
import assert from "node:assert/strict";
import { readMembers } from "../members.mjs";

function client(pair = { lid: "111@lid", pn: "123@c.us" }) {
  return {
    async getChatById() { return { isGroup: true, participants: [{ id: { _serialized: "111:3@lid" } }] }; },
    async getContactLidAndPhone() { return [pair]; },
    async getContactById() { return { name: "Saved Name", pushname: "Other Name", isMyContact: true }; },
  };
}
test("roster deduplicates devices/groups and links only queried WhatsApp LID/PN pairs", async () => {
  const result = await readMembers(client(), ["one@g.us", "two@g.us"]);
  assert.equal(result.members.length, 1);
  assert.deepEqual(result.members[0].ids, ["111@lid", "123@c.us"]);
  assert.deepEqual(result.members[0].groups, ["one@g.us", "two@g.us"]);
  assert.equal(result.members[0].contact_name, "Saved Name");
  assert.equal(result.members[0].worker, undefined);
});
test("mismatched lookup never joins another account; labels still remain hints", async () => {
  const result = await readMembers(client({ lid: "999@lid", pn: "123@c.us" }), ["one@g.us"]);
  assert.deepEqual(result.members[0].ids, ["111@lid"]);
  assert.ok(result.members[0].error);
});
test("lookup failure preserves other evidence without inventing an ID", async () => {
  const c = client();
  c.getContactLidAndPhone = async () => { throw new Error("offline"); };
  const result = await readMembers(c, ["one@g.us"]);
  assert.deepEqual(result.members[0].ids, ["111@lid"]);
  assert.equal(result.members[0].contact_name, "Saved Name");
});

test("unsupported participant IDs fail the audit instead of silently omitting people", async () => {
  const c = client();
  c.getChatById = async () => ({ isGroup: true, participants: [{ id: { _serialized: "unsupported@other" } }] });
  await assert.rejects(readMembers(c, ["one@g.us"]), /Cannot safely identify/);
});
