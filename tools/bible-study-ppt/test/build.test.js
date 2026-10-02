// npm test   (from tools/bible-study-ppt)
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "bible-study-ppt-"));
const cuv = path.join(tmp, "cuv");
const chapter = (book, ch, n) => {
  fs.mkdirSync(path.join(cuv, book), { recursive: true });
  const verses = Object.fromEntries(Array.from({ length: n }, (_, i) => [String(i + 1), `${book}${ch}章${i + 1}節經文`]));
  fs.writeFileSync(path.join(cuv, book, `${ch}.json`), JSON.stringify(verses));
};
chapter("MAT", 16, 28);
chapter("MAT", 17, 27);
chapter("MAT", 19, 30);
chapter("MAT", 20, 34);
process.env.CUV_DIR = cuv;

const { build } = require("../build");
const { Deck } = require("../lib");

function study(name, script, slides) {
  const folder = path.join(tmp, name);
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(path.join(folder, "script.md"), script);
  fs.writeFileSync(path.join(folder, "slides.json"), JSON.stringify({ title: "測試", pptx: "測試", script: "測試逐字稿", slides }));
  return folder;
}

const SCRIPT = "## 一、開場\n\n第一段。 <!-- n1; carson v2 p429 -->\n\n第二段。\n";

test("builds a deck, a clean script and a cross-reference", async () => {
  const folder = study("ok", SCRIPT, [
    { type: "section", num: "一", title: "開場", paras: [0] },
    { type: "points", title: "重點", items: ["一條短句", "另一條短句"], refs: ["MAT 20:28"], paras: [1, 2] },
  ]);
  const result = await build(folder);
  assert.strictEqual(result.slides, 1);
  assert.deepStrictEqual(result.verses, ["MAT 20:28"]);
  const out = path.join(folder, "out");
  assert.ok(fs.existsSync(path.join(out, "測試.pptx")));
  assert.ok(!fs.readFileSync(path.join(out, "測試逐字稿.md"), "utf8").includes("<!--"));
  const xref = fs.readFileSync(path.join(out, "測試逐字稿（投影片對照）.md"), "utf8");
  assert.ok(xref.includes("**【第 1 頁】**") && !xref.includes("<!--"));
});

test("every script paragraph must reach some slide", async () => {
  const folder = study("uncovered", SCRIPT, [{ type: "points", title: "重點", items: ["一條短句"], paras: [0, 1] }]);
  await assert.rejects(build(folder), /uncovered paragraphs: \[2\]/);
});

test("key points only: at most four, each on one line", () => {
  const deck = new Deck(path.join(study("points", SCRIPT, []), "script.md"), { title: "t", crossReference: "/dev/null" });
  assert.throws(() => deck.points({ title: "太多", items: ["一", "二", "三", "四", "五"] }), /5 points; at most 4/);
  const sentence = "彼得說這話，正是在少年財主走了之後。那個人捨不得撇下，彼得說：我們撇下了。";
  assert.throws(() => deck.points({ title: "整句", items: [sentence] }), /does not fit on one line/);
});

test("passages from different chapters are labelled and separated; a reading running on is not", () => {
  const deck = new Deck(path.join(study("labels", SCRIPT, []), "script.md"), { title: "t", crossReference: "/dev/null" });
  const { verses } = require("../lib");
  const two = verses(["MAT 16:21", "MAT 17:22-23"]);
  assert.deepStrictEqual(two.map((_, i) => deck.verseNum(two, i)), ["16:21", "17:22", "23"]);
  assert.strictEqual(deck.verseRuns(two, 32).filter((r) => r.text === "").length, 1);
  const reading = verses(["MAT 19:29-30", "MAT 20:1"]);
  assert.deepStrictEqual(reading.map((_, i) => deck.verseNum(reading, i)), ["29", "30", "20:1"]);
  assert.strictEqual(deck.verseRuns(reading, 32).filter((r) => r.text === "").length, 0);
});

test("an uncached chapter says how to cache it", () => {
  const { verses } = require("../lib");
  assert.throws(() => verses(["MRK 10:45"]), /MRK\/10 is not cached/);
});
