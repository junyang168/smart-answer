import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { registeredMessage, allowedDestination, allowedBySettings } from "../outgoing.mjs";
import { saveAdminConfig, loadAdminConfig } from "../admin-config.mjs";

const config = { enabled: true, groups: ["1@g.us"], admin_group: "2@g.us", allow_admin_help: true, allow_questions: false };
test("admin help is a separate registered envelope with an exact dedicated destination", () => {
  assert.ok(registeredMessage("admin-help:abc:open", "【主日助理系統求助】需要協助"));
  assert.ok(allowedDestination(config, "admin-help:abc:open", "2@g.us"));
  assert.equal(allowedDestination(config, "admin-help:abc:open", "1@g.us"), false);
  assert.equal(allowedDestination(config, "ack:abc", "2@g.us"), false);
  assert.ok(allowedBySettings(config, "admin-help:abc:open"));
  assert.equal(allowedBySettings(config, "clarify:abc"), false);
  assert.equal(allowedDestination({ ...config, allow_admin_help: false }, "admin-help:a", "2@g.us"), false);
});
test("source collection can be paused without disabling administrator alerts", () => {
  assert.ok(allowedDestination({ ...config, enabled: false }, "admin-help:a", "2@g.us"));
  assert.equal(allowedDestination({ ...config, enabled: false }, "ack:a", "1@g.us"), false);
});
test("backend-outage cache authorizes only the approved admin group and expires", () => {
  const path = join(mkdtempSync(join(tmpdir(), "admin-config-")), "config.json");
  saveAdminConfig(path, config, 1000);
  const cached = loadAdminConfig(path, 2000);
  assert.ok(allowedDestination(cached, "admin-help:a", "2@g.us"));
  assert.equal(allowedDestination(cached, "ack:a", "1@g.us"), false);
  assert.equal(loadAdminConfig(path, 86402000), null);
  saveAdminConfig(path, { ...config, allow_admin_help: false }, 3000);
  assert.equal(allowedBySettings(loadAdminConfig(path, 4000), "admin-help:a"), false);
});
test("cached admin authorization never permits a source group as the admin destination", () => {
  const path = join(mkdtempSync(join(tmpdir(), "admin-config-")), "config.json");
  saveAdminConfig(path, { ...config, admin_group: "1@g.us" }, 1000);
  assert.equal(allowedDestination(loadAdminConfig(path, 2000), "admin-help:a", "1@g.us"), false);
});

test('PPT ready notices reach only the configured admin group with fresh explicit enablement', () => {
  const enabled = { ...config, allow_admin_help: false, allow_ppt_ready: true };
  assert.ok(registeredMessage('ppt-ready:date:version', '【主日PPT已準備好】可以下載'));
  assert.ok(allowedDestination(enabled, 'ppt-ready:date:version', '2@g.us'));
  assert.ok(allowedBySettings(enabled, 'ppt-ready:date:version'));
  assert.equal(allowedDestination(enabled, 'ppt-ready:date:version', '1@g.us'), false);
  assert.equal(allowedDestination(config, 'ppt-ready:date:version', '2@g.us'), false);
  assert.equal(allowedBySettings({...enabled,enabled:false}, 'ppt-ready:date:version'), false);
  assert.equal(allowedDestination({...enabled,admin_group:'1@g.us'}, 'ppt-ready:date:version', '1@g.us'), false);
  assert.equal(registeredMessage('ppt-ready:date:version', '任意自由文字'), false);
});
