import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { getUserByEmail, getUserConfigPath } from "../src/app/utils/user-db.ts";

test("role assignment and revocation use the authoritative current file without a restart", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "beta-roles-"));
  const previous = process.env.DATA_BASE_DIR;
  process.env.DATA_BASE_DIR = root;
  try {
    await mkdir(path.join(root, "config"));
    assert.equal(getUserConfigPath(), path.join(root, "config", "config.json"));
    const config = {
      users: [{ id: "tester@example.com", name: "Tester" }],
      user_roles: [{ user: "tester@example.com", role: "beta_tester" }],
    };
    await writeFile(getUserConfigPath(), JSON.stringify(config));
    assert.equal(
      (await getUserByEmail("TESTER@example.com")).role,
      "beta_tester",
    );
    config.user_roles[0].role = "editor";
    await writeFile(getUserConfigPath(), JSON.stringify(config));
    assert.equal((await getUserByEmail("tester@example.com")).role, "editor");
    config.users = [];
    await writeFile(getUserConfigPath(), JSON.stringify(config));
    assert.equal(await getUserByEmail("tester@example.com"), null);
  } finally {
    if (previous === undefined) delete process.env.DATA_BASE_DIR;
    else process.env.DATA_BASE_DIR = previous;
    await rm(root, { recursive: true });
  }
});
