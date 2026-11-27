import test from "node:test";
import assert from "node:assert/strict";
import { textChanges } from "../src/app/components/admin/surmons/review-text-diff.ts";
const reconstruct = (parts, excluded) =>
  parts
    .filter((p) => p.kind !== excluded)
    .map((p) => p.text)
    .join("");

test("the reported soft-delete markers do not mark the whole Chinese paragraph replaced", () => {
  const before =
    "可是呢，後來亞當夏娃犯了罪，魔鬼，亞當夏娃聽魔鬼的話，所以他們不要神做王。";
  const after = before.replace("魔鬼，亞當", "~~魔鬼，~~亞當");
  const parts = textChanges(before, after);
  assert.equal(parts.filter((p) => p.kind === "delete").length, 0);
  assert.equal(
    parts
      .filter((p) => p.kind === "insert")
      .map((p) => p.text)
      .join(""),
    "~~~~",
  );
  assert.equal(reconstruct(parts, "insert"), before);
  assert.equal(reconstruct(parts, "delete"), after);
});

test("replacement highlights only changed characters and retains whitespace and newlines", () => {
  const before = "教授說：我信耶穌。\n  第二行";
  const after = "教授說：你信耶穌。\n  第二行";
  const parts = textChanges(before, after);
  assert.deepEqual(
    parts.filter((p) => p.kind !== "equal"),
    [
      { kind: "delete", text: "我" },
      { kind: "insert", text: "你" },
    ],
  );
  assert.equal(reconstruct(parts, "insert"), before);
  assert.equal(reconstruct(parts, "delete"), after);
});

test("Unicode, literal HTML, Markdown, empty and repetitive source text round-trip exactly", () => {
  for (const [a, b] of [
    ["", "新增"],
    ["刪除", ""],
    ["相同", "相同"],
    ["心 καρδία לֵב 😀 <script>", "心 καρδία לֵב 😇 **<script>**"],
    ["王王王神王王王", "王王王主王王王"],
  ]) {
    const parts = textChanges(a, b);
    assert.equal(reconstruct(parts, "insert"), a);
    assert.equal(reconstruct(parts, "delete"), b);
  }
});

test("very large paragraphs retain common edges with bounded comparison work", () => {
  const before = "前文" + "甲".repeat(2000) + "結尾",
    after = "前文" + "乙".repeat(2000) + "結尾";
  const parts = textChanges(before, after);
  assert.equal(parts[0].text, "前文");
  assert.equal(parts.at(-1).text, "結尾");
  assert.equal(reconstruct(parts, "insert"), before);
  assert.equal(reconstruct(parts, "delete"), after);
});
