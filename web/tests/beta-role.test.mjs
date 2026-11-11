import test from "node:test";
import assert from "node:assert/strict";
import {
  hasEditorAccess,
  hasTranscriptBetaAccess,
  normalizeRole,
  canManageUsers,
  roleLabel,
} from "../src/app/utils/roles.ts";

test("beta tester has editor access and beta access; an ordinary editor does not gain beta access", () => {
  assert.equal(normalizeRole("beta_tester"), "beta_tester");
  assert.equal(roleLabel("beta_tester"), "beta tester");
  assert.equal(hasEditorAccess("beta_tester"), hasEditorAccess("editor"));
  assert.equal(
    hasTranscriptBetaAccess("tester@example.com", "beta_tester"),
    true,
  );
  for (const role of ["editor", "reader", "reviewer", undefined]) {
    assert.equal(hasTranscriptBetaAccess("tester@example.com", role), false);
  }
  assert.equal(hasTranscriptBetaAccess("junyang168@gmail.com", "editor"), true);
  assert.equal(hasEditorAccess("reader"), false);
});

test("testers cannot grant themselves or others elevated roles", () => {
  for (const role of ["editor", "beta_tester", "reader", undefined]) {
    assert.equal(canManageUsers("someone@example.com", role), false);
  }
  assert.equal(canManageUsers("admin@example.com", "admin"), true);
  assert.equal(canManageUsers("junyang168@gmail.com", "editor"), true);
  assert.equal(canManageUsers(undefined, "admin"), false);
});

test("role definition inherits editor permissions and preserves other grants", async () => {
  const { withBetaRole } = await import("../src/app/utils/roles.ts");
  const config = {
    roles: ["editor", "reader"],
    role_permissions: [
      { role: "editor", permission: "write_owned_item" },
      { role: "admin", permission: "assign_any_item" },
    ],
  };
  withBetaRole(config);
  withBetaRole(config);
  assert.equal(config.roles.filter((role) => role === "beta_tester").length, 1);
  assert.deepEqual(
    config.role_permissions.filter((row) => row.role === "beta_tester"),
    [{ role: "beta_tester", permission: "write_owned_item" }],
  );
  assert.equal(
    config.role_permissions.filter((row) => row.role === "admin").length,
    1,
  );
});

test("changing a role removes all stale duplicate beta grants", async () => {
  const { assignUserRole } = await import("../src/app/utils/roles.ts");
  assert.deepEqual(
    assignUserRole(
      [
        { user: "Tester@example.com", role: "editor" },
        { user: "tester@example.com", role: "beta_tester" },
        { user: "other@example.com", role: "editor" },
      ],
      "tester@example.com",
      "reader",
    ),
    [
      { user: "other@example.com", role: "editor" },
      { user: "tester@example.com", role: "reader" },
    ],
  );
});
