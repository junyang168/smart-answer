export function registeredMessage(key, text) {
  if (typeof key !== "string" || key.length > 300 || typeof text !== "string" || text.length > 12000) return false;
  return [["ack:", "【請假登記回覆】"], ["friday:", "【主日資料提醒】"],
    ["ppt-ready:", "【主日PPT已準備好】"], ["admin-help:", "【主日助理系統求助】"], ["clarify:", "【主日助理提問】"], ["confirmed:", "【主日資料回覆】"]]
    .some(([keyPrefix, textPrefix]) => key.startsWith(keyPrefix) && text.startsWith(textPrefix));
}


export function allowedBySettings(config, key) {
  if (typeof key !== "string") return false;
  if (key.startsWith("ppt-ready:")) return config.enabled === true && config.allow_ppt_ready === true;
  if (key.startsWith("admin-help:")) return config.allow_admin_help === true;
  if (key.startsWith("clarify:")) return config.allow_questions === true;
  if (key.startsWith("friday:")) return config.allow_reminders === true;
  return true;
}


export function allowedDestination(config, key, group) {
  if (key.startsWith("ppt-ready:")) return config.enabled === true && config.allow_ppt_ready === true && group === config.admin_group && !config.groups.includes(group);
  if (key.startsWith("admin-help:")) return config.allow_admin_help === true && group === config.admin_group && !config.groups.includes(group);
  return config.enabled === true && config.groups.includes(group) && group !== config.admin_group;
}
