import test from "node:test";
import assert from "node:assert/strict";
import { reviewEditorHistoryEvents } from "../src/app/components/admin/surmons/review-editor-history.ts";

test("loading server text resets undo, while typing and undo keep history", () => {
  let resets = 0;
  const editor = { clearHistory: () => { resets++; } };
  for (const origin of ["+input", "paste", "undo", "redo"]) {
    reviewEditorHistoryEvents.change(editor, { origin });
  }
  assert.equal(resets, 0);
  reviewEditorHistoryEvents.change(editor, { origin: "setValue" });
  assert.equal(resets, 1);
});
