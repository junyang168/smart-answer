import test from "node:test";
import assert from "node:assert/strict";
import { betaEditorDestination } from "../src/app/components/admin/surmons/review-entry.ts";

test("Edit selects activated private beta drafts, including Chinese and space IDs", async () => {
  const item = "2018 NYSC 專題：馬太福音釋經（七）3";
  for (const mode of ["review", "candidate"]) {
    const calls = [];
    const destination = await betaEditorDestination(item, async (url) => {
      calls.push(url);
      return { mode };
    });
    assert.deepEqual(calls, [
      `/api/sc_api/beta/review_mode/${encodeURIComponent(item)}`,
    ]);
    assert.equal(
      destination,
      `/admin/transcript-beta/${encodeURIComponent(item)}`,
    );
    assert.equal(decodeURIComponent(destination.split("/").at(-1)), item);
  }
});

test("only absent/unactivated beta drafts use the original editor", async () => {
  assert.equal(
    await betaEditorDestination("S1", async () => {
      throw { status: 404 };
    }),
    undefined,
  );
  assert.equal(
    await betaEditorDestination("S1", async () => ({ mode: "legacy" })),
    undefined,
  );
});

test("authorization, source and connection failures do not silently open another manuscript", async () => {
  for (const error of [
    { status: 401 },
    { status: 403 },
    { status: 409 },
    { status: 503 },
    new Error("offline"),
  ]) {
    await assert.rejects(
      betaEditorDestination("S1", async () => {
        throw error;
      }),
      (value) => value === error,
    );
  }
  await assert.rejects(
    betaEditorDestination("S1", async () => ({ mode: "unexpected" })),
    /無效/,
  );
});
