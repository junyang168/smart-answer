// Runs the actual Next users API against disposable data, never production users.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  rm,
  open,
} from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const web = path.join(root, "web");
const require = createRequire(path.join(web, "package.json"));
const { encode } = require("next-auth/jwt");
const data = await mkdtemp(path.join(tmpdir(), "user-role-save-"));
const configPath = path.join(data, "config", "config.json");
const port = Number(process.env.ROLE_SAVE_TEST_PORT || 3561);
const base = `http://127.0.0.1:${port}`;
const secret = randomBytes(32).toString("hex");
const log = await open(path.join(data, "next.log"), "w");
let server;
try {
  await mkdir(path.dirname(configPath));
  await writeFile(
    configPath,
    JSON.stringify({
      users: [
        { id: "junyang168@gmail.com", name: "Test owner" },
        { id: "tester@example.invalid", name: "Tester" },
      ],
      user_roles: [
        { user: "junyang168@gmail.com", role: "editor" },
        { user: "tester@example.invalid", role: "editor" },
      ],
      roles: ["editor", "reader"],
      role_permissions: [
        { role: "editor", permission: "read_any_item" },
        { role: "editor", permission: "write_owned_item" },
        { role: "editor", permission: "assign_item" },
      ],
    }),
  );
  const env = {
    ...process.env,
    NODE_ENV: "development",
    DATA_BASE_DIR: data,
    NEXTAUTH_SECRET: secret,
    NEXTAUTH_URL: base,
    NEXTAUTH_URL_INTERNAL: base,
    GOOGLE_CLIENT_ID: "test-only",
    GOOGLE_CLIENT_SECRET: "test-only",
    FULL_ARTICLE_SERVICE_URL: "http://127.0.0.1:9561",
  };
  server = spawn(
    process.execPath,
    [
      path.join(web, "node_modules/next/dist/bin/next"),
      "dev",
      "--hostname",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    { cwd: web, env, detached: true, stdio: ["ignore", log.fd, log.fd] },
  );
  const token = await encode({
    secret,
    token: { email: "junyang168@gmail.com", role: "editor" },
    maxAge: 180,
  });
  const headers = {
    cookie: `next-auth.session-token=${token}`,
    origin: base,
    "content-type": "application/json",
  };
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    if (server.exitCode !== null)
      throw new Error("Isolated Next server exited");
    try {
      const response = await fetch(base + "/api/admin/users", {
        headers,
        redirect: "manual",
      });
      if (response.status === 200) {
        ready = true;
        break;
      }
    } catch {
      /* Server is still compiling. */
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  assert.ok(ready, "isolated authenticated users API is ready");
  const before = await readFile(configPath, "utf8");
  const badOrigin = await fetch(base + "/api/admin/users", {
    method: "POST",
    headers: { ...headers, origin: "https://elsewhere.invalid" },
    body: JSON.stringify({
      action: "edit",
      user: { id: "tester@example.invalid" },
      role: "beta_tester",
    }),
  });
  assert.equal(badOrigin.status, 403);
  assert.equal(await readFile(configPath, "utf8"), before);
  for (const action of ["edit", "update"]) {
    const response = await fetch(base + "/api/admin/users", {
      method: "POST",
      headers,
      body: JSON.stringify({
        action,
        user: { id: "tester@example.invalid", name: "Tester" },
        role: "beta_tester",
      }),
    });
    assert.equal(response.status, 200, `${action}: ${await response.text()}`);
    const saved = JSON.parse(await readFile(configPath, "utf8"));
    assert.deepEqual(
      saved.user_roles.filter((row) => row.user === "tester@example.invalid"),
      [{ user: "tester@example.invalid", role: "beta_tester" }],
    );
    assert.deepEqual(
      saved.role_permissions
        .filter((row) => row.role === "beta_tester")
        .map((row) => row.permission),
      saved.role_permissions
        .filter((row) => row.role === "editor")
        .map((row) => row.permission),
    );
  }
  const saved = await fetch(base + "/api/admin/users", { headers });
  assert.equal(
    (await saved.json()).user_roles.find(
      (row) => row.user === "tester@example.invalid",
    ).role,
    "beta_tester",
  );
  const unknown = await fetch(base + "/api/admin/users", {
    method: "POST",
    headers,
    body: JSON.stringify({ action: "unknown" }),
  });
  assert.equal(unknown.status, 400);
  console.log(
    "PASS: actual users API saves edit/update roles, readback matches, editor permissions inherited, foreign origin rejected",
  );
} catch (error) {
  console.error(
    (await readFile(path.join(data, "next.log"), "utf8")).slice(-4000),
  );
  throw error;
} finally {
  if (server?.pid && server.exitCode === null) {
    const stopped = once(server, "exit");
    process.kill(-server.pid, "SIGTERM");
    await stopped;
  }
  await log.close();
  await rm(data, { recursive: true });
}
