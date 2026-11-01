import test from "node:test";
import assert from "node:assert/strict";
import {
  documentVersion,
  recoverTransportVersion,
} from "../src/app/components/admin/surmons/review-version.ts";
const sha = "a".repeat(64);
test("compressed transport does not change document revision identity", () => {
  assert.equal(documentVersion(undefined, `W/"${sha}"`), sha);
  assert.equal(documentVersion(undefined, `"${sha}"`), sha);
  assert.equal(documentVersion(sha, 'W/"different-cache-tag"'), sha);
  assert.throws(() => documentVersion(undefined, null));
});
test("recovering an old compressed-version draft retains all edits and mints a new request identity", () => {
  const saved = {
    baseVersion: `W/${sha}`,
    operations: [{ kind: "text", id: "1", text: "本機校對~~刪除~~" }],
    packet: {
      version: `W/${sha}`,
      operation_id: "old-id",
      epoch: "claim",
      operations: [
        { kind: "paragraph_review", id: "1", decision: "confirmed" },
      ],
    },
  };
  const copy = structuredClone(saved);
  const recovered = recoverTransportVersion(saved);
  assert.equal(recovered.baseVersion, sha);
  assert.equal(recovered.packet.version, sha);
  assert.notEqual(recovered.packet.operation_id, saved.packet.operation_id);
  assert.deepEqual(recovered.operations, saved.operations);
  assert.deepEqual(recovered.packet.operations, saved.packet.operations);
  assert.deepEqual(saved, copy);
  assert.equal(recoverTransportVersion(recovered), recovered);
});
test("different source revisions stay different and valid packets keep their identity", () => {
  const pending = {
    baseVersion: sha,
    packet: { version: sha, operation_id: "same" },
  };
  assert.equal(recoverTransportVersion(pending), pending);
  assert.notEqual(
    recoverTransportVersion({ ...pending, baseVersion: `W/${"b".repeat(64)}` })
      .baseVersion,
    sha,
  );
});
