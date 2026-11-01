export function senderId(message, account) {
  // Own group messages carry a linked-device LID in author; use the signed-in
  // account consistently across devices and after reconnecting.
  const id = message.fromMe ? account : (message.author || message.from);
  return id?.replace(/:\d+(?=@)/, "");
}
