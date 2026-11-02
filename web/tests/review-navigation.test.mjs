import test from "node:test";
import assert from "node:assert/strict";
import {
  paragraphIsConfirmed,
  scrollToReviewParagraph,
} from "../src/app/components/admin/surmons/review-navigation.ts";

test("navigation treats changed text, pending edits and questions as pending", () => {
  const reviews = {
    "p:done": { decision: "confirmed", sha256: "a" },
    "p:stale": { decision: "confirmed", sha256: "old" },
    "p:edited": { decision: "confirmed", sha256: "c" },
    "p:question": { decision: "question", sha256: "d" },
  };
  const shas = {
    done: "a",
    stale: "new",
    edited: "c",
    question: "d",
    unseen: "e",
  };
  for (const id of Object.keys(shas)) {
    assert.equal(
      paragraphIsConfirmed(id, reviews, shas, new Set(["edited"])),
      id === "done",
    );
  }
  const rows = ["done", "stale", "edited"];
  assert.equal(
    rows.find((id) => !paragraphIsConfirmed(id, reviews, shas, new Set())),
    "stale",
  );
});

test("each click scrolls even the already selected paragraph below the sticky toolbar", () => {
  const calls = [];
  const paragraph = {
    style: {},
    scrollIntoView: (options) => calls.push(options),
  };
  const header = { getBoundingClientRect: () => ({ bottom: 248 }) };
  scrollToReviewParagraph(paragraph, header);
  scrollToReviewParagraph(paragraph, header);
  assert.equal(paragraph.style.scrollMarginTop, "264px");
  assert.deepEqual(calls, [
    { block: "start", behavior: "instant" },
    { block: "start", behavior: "instant" },
  ]);
  scrollToReviewParagraph(null, header);
});
