import assert from "node:assert/strict";
import test from "node:test";
import { extractArticleToc } from "../src/app/resources/wang-repository/articles/[slug]/article-toc.ts";

test("returns no entries when an article has no section heading", () => {
  assert.deepEqual(extractArticleToc("# 标题\n\n正文。"), []);
});

test("keeps a single H2 instead of incorrectly falling back to H3", () => {
  assert.deepEqual(extractArticleToc("## 唯一一节\n\n### 小标题"), [
    { id: "article-section-1", label: "唯一一节", level: 2, line: 1, ordinal: 0 },
  ]);
});

test("normalizes inline markdown and gives duplicate headings unique identities", () => {
  assert.deepEqual(extractArticleToc("## **磐石**是谁\n\n## **磐石**是谁"), [
    { id: "article-section-1", label: "磐石是谁", level: 2, line: 1, ordinal: 0 },
    { id: "article-section-2", label: "磐石是谁", level: 2, line: 3, ordinal: 1 },
  ]);
});

test("uses H3 only when the article has no H2", () => {
  assert.deepEqual(extractArticleToc("### 第一节\n\n### 第二节"), [
    { id: "article-section-1", label: "第一节", level: 3, line: 1, ordinal: 0 },
    { id: "article-section-2", label: "第二节", level: 3, line: 3, ordinal: 1 },
  ]);
});
