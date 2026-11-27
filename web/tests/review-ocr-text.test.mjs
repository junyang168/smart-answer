import test from "node:test";
import assert from "node:assert/strict";
import {
  editOcrText,
  ocrText,
} from "../src/app/components/admin/surmons/review-ocr-text.ts";
const regions = [
  { id: "a", text: "心有理性", x: 10 },
  { id: "b", text: "出36:2", x: 20 },
  { id: "c", text: "原文", x: 30 },
];
test("editing one OCR line preserves other source regions and coordinates", () => {
  const updated = editOcrText(regions, "心有情感\n出36:2\n原文");
  assert.deepEqual(updated[1], regions[1]);
  assert.deepEqual(updated[2], regions[2]);
  assert.equal(updated[0].x, 10);
  assert.equal(regions[0].text, "心有理性");
});
test("whole-slide editing preserves exact text through line merges, splits and deletion", () => {
  for (const text of [
    "心有理性出36:2\n原文",
    "心有\n理性\n出36:2\n原文",
    "\n出36:2\n原文",
    "心有理性\n",
    "",
    "新投影片\n\n結尾\n",
  ]) {
    const updated = editOcrText(regions, text);
    assert.equal(ocrText(updated), text);
    assert.deepEqual(
      updated.map((r) => [r.id, r.x]),
      regions.map((r) => [r.id, r.x]),
    );
    assert.equal(ocrText(editOcrText(updated, text + "新增")), text + "新增");
  }
});
