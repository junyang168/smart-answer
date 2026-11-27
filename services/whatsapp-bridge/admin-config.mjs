import { readFileSync, writeFileSync, renameSync } from "node:fs";

// Only the last backend-authorized administrator destination may be used during
// a backend outage. No source-group settings or credentials are cached here.
export function saveAdminConfig(path, config, now = Date.now()) {
  const value = { admin_group: config.admin_group || "", allow_admin_help: config.allow_admin_help === true, groups: [], checked_at: now };
  if (config.groups.includes(value.admin_group)) value.allow_admin_help = false;
  writeFileSync(path + ".tmp", JSON.stringify(value), { mode: 0o600 });
  renameSync(path + ".tmp", path);
}

export function loadAdminConfig(path, now = Date.now()) {
  try {
    const value = JSON.parse(readFileSync(path, "utf8"));
    if (!/^\d+(?:-\d+)?@g\.us$/.test(value.admin_group) || !Number.isFinite(value.checked_at) || now < value.checked_at || now - value.checked_at > 86400000) return null;
    return value;
  } catch { return null; }
}
