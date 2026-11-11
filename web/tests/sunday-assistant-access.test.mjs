import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";
import { hasEditorAccess } from "../src/app/utils/roles.ts";

// Run the actual route with synthetic sessions and transport; no credentials,
// server-side session cookies, or live WhatsApp/API calls are created.
const require = createRequire(import.meta.url);
const route = ts.transpileModule(
  readFileSync(new URL("../src/app/api/admin/sunday-assistant/[[...path]]/route.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;
function harness(session, mailAccess = false) {
  const calls = [];
  const exports = {};
  vm.runInNewContext(route, {
    exports,
    require(name) {
      if (name === "next/server") return require(name);
      if (name === "@/app/utils/auth") return { getSessionWithDevFallback: async () => session };
      if (name === "@/app/utils/roles") return { hasEditorAccess };
      if (name === "@/app/utils/sunday-gmail-access") return { canReviewSundayGmail: async () => mailAccess };
      throw new Error(`Unexpected module ${name}`);
    },
    process: { env: { SUNDAY_ASSISTANT_SECRET: "synthetic-test-only", FULL_ARTICLE_SERVICE_URL: "http://backend.invalid" } },
    URL, AbortSignal,
    fetch: async (...args) => { calls.push(args); return Response.json({ status: "test-state" }); },
  });
  return {
    calls,
    request(method = "GET", path = ["state"], origin) {
      const headers = { host: "church.test" };
      if (origin) headers.origin = origin;
      return exports[method](new Request("https://church.test/api/admin/sunday-assistant/" + path.join("/"), {
        method, headers,
        ...(method === "GET" ? {} : { body: "{}" }),
      }), { params: Promise.resolve({ path }) });
    },
  };
}

test("all existing editor-equivalent roles can load Sunday assistant state", async () => {
  for (const role of ["admin", "editor", "beta_tester"]) {
    const app = harness({ user: { role } });
    const result = await app.request();
    assert.equal(result.status, 200, role);
    assert.deepEqual(await result.json(), { status: "test-state" });
    assert.equal(app.calls.length, 1);
    assert.equal(app.calls[0][0], "http://backend.invalid/admin/sunday-assistant/state");
    assert.equal(app.calls[0][1].headers.Authorization, "Bearer synthetic-test-only");
  }
});

test("signed-out, ordinary and unknown roles never reach the privileged backend", async () => {
  for (const role of ["reader", "reviewer", "member", "viewer", undefined, "unknown"]) {
    const app = harness({ user: { role } });
    assert.equal((await app.request()).status, 403);
    assert.equal(app.calls.length, 0);
  }
  const app = harness(null);
  assert.equal((await app.request()).status, 403);
  assert.equal(app.calls.length, 0);
});

test("beta editor access retains origin checks and the browser endpoint allow-list", async () => {
  const app = harness({ user: { role: "beta_tester" } });
  assert.equal((await app.request("PUT", ["settings"])).status, 403);
  assert.equal((await app.request("PUT", ["settings"], "https://another.test")).status, 403);
  assert.equal((await app.request("GET", ["bridge-config"])).status, 404);
  assert.equal((await app.request("POST", ["ingest"], "https://church.test")).status, 404);
  assert.equal(app.calls.length, 0);
  assert.equal((await app.request("PUT", ["settings"], "https://church.test")).status, 200);
  assert.equal(app.calls.length, 1);
});


test("personal Gmail state and decisions require a designated reviewer as well as editor access", async () => {
  const editor=harness({user:{role:"editor",email:"coworker@example.com"}});
  assert.equal((await editor.request("GET",["gmail","state"])).status,403);
  assert.equal((await editor.request("POST",["gmail","files","a".repeat(64)],"https://church.test")).status,403);
  assert.equal(editor.calls.length,0);
  const owner=harness({user:{role:"beta_tester",email:"owner@example.com"}},true);
  assert.equal((await owner.request("GET",["gmail","state"])).status,200);
  assert.equal((await owner.request("POST",["gmail","files","a".repeat(64)],"https://church.test")).status,200);
  assert.equal((await owner.request("POST",["gmail","files","a".repeat(64)],"https://other.test")).status,403);
});
