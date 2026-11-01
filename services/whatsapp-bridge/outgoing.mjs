export function registeredMessage(key, text) {
  if (typeof key !== "string" || key.length > 300 || typeof text !== "string" || text.length > 12000) return false;
  return [["ack:", "【請假登記回覆】"], ["friday:", "【主日資料提醒】"],
    ["clarify:", "【主日助理提問】"], ["confirmed:", "【主日資料回覆】"]]
    .some(([keyPrefix, textPrefix]) => key.startsWith(keyPrefix) && text.startsWith(textPrefix));
}
