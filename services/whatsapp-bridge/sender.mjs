export function normalizeSenderId(id) {
  if (typeof id !== "string") return null;
  const normalized = id.replace(/:\d+(?=@)/, "").replace(/@s\.whatsapp\.net$/, "@c.us");
  return /^\d+@(lid|c\.us)$/.test(normalized) ? normalized : null;
}

export function senderId(message, account) {
  // Own group messages carry a linked-device LID in author; use the signed-in
  // account consistently across devices and after reconnecting.
  const id = message.fromMe ? account : (message.author || message.from);
  return normalizeSenderId(id);
}
