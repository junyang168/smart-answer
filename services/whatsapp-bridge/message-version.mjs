import { createHash } from "node:crypto";

export function messageVersion(message, editedEvent = false) {
  const originalMs = Number(message.timestamp) * 1000;
  const editMs = Number(message.latestEditSenderTimestampMs);
  const verifiedEdit = Number.isFinite(editMs) && editMs >= originalMs && editMs > 100000000000;
  const parts = [message.body || "", message.type];
  if (verifiedEdit) parts.push([editMs, message.latestEditMsgKey?._serialized || message.latestEditMsgKey?.$1 || ""]);
  // Keep original-message IDs compatible with existing durable journals. Edit
  // timestamps distinguish A -> B -> A, not just distinct body hashes.
  return {
    id: message.id._serialized + ":v:" + createHash("sha256").update(JSON.stringify(parts)).digest("hex").slice(0, 16),
    sent_at: new Date(verifiedEdit ? editMs : originalMs).toISOString(),
    source_sent_at: verifiedEdit ? new Date(originalMs).toISOString() : null,
    unverified_edit: editedEvent && !verifiedEdit,
  };
}
