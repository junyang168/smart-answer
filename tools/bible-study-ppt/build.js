#!/usr/bin/env node
// node tools/bible-study-ppt/build.js <study folder>
//
// Reads <folder>/slides.json and <folder>/script.md, writes <folder>/out/:
//   <pptx>.pptx                      the deck
//   <script>.md                      the script without the checker's note tags
//   <script>（投影片對照）.md          the script with 【第 N 頁】 marks and each slide's verses
//
// slides.json:
//   { "title": "...", "pptx": "你們不知道所求的是甚麼 太20-17至34",
//     "script": "马太福音 20-17至34 你們不知道所求的是甚麼 查經逐字稿",
//     "slides": [ { "type": "points", "title": "...", "items": [...], "refs": ["MAT 20:24"], "paras": [72, 73] }, ... ] }
// `paras` are indices of script.md's blank-line-separated paragraphs. Every
// paragraph must be claimed by some slide, or the build fails.
const fs = require("fs");
const path = require("path");
const { Deck } = require("./lib");

const TYPES = new Set(["title", "section", "scripture", "points", "statement", "pair", "table", "question"]);

async function build(folder) {
  const spec = JSON.parse(fs.readFileSync(path.join(folder, "slides.json"), "utf8"));
  for (const key of ["title", "pptx", "script", "slides"]) if (!spec[key]) throw new Error(`slides.json: missing "${key}"`);
  const out = path.join(folder, "out");
  fs.mkdirSync(out, { recursive: true });
  const scriptPath = path.join(folder, "script.md");
  const deck = new Deck(scriptPath, { title: spec.title, crossReference: path.join(out, `${spec.script}（投影片對照）.md`) });
  spec.slides.forEach((slide, i) => {
    const { type, ...options } = slide;
    if (!TYPES.has(type)) throw new Error(`slides.json: slide entry ${i} has unknown type ${type}`);
    deck[type](options);
  });
  const result = await deck.save(path.join(out, `${spec.pptx}.pptx`));
  const clean = fs.readFileSync(scriptPath, "utf8").replace(/[ \t]*<!--[\s\S]*?-->/g, "");
  fs.writeFileSync(path.join(out, `${spec.script}.md`), clean);
  fs.writeFileSync(path.join(out, "verses.json"), JSON.stringify(result.verses, null, 1) + "\n");
  return { ...result, pptx: path.join(out, `${spec.pptx}.pptx`) };
}

if (require.main === module) {
  const folder = process.argv[2];
  if (!folder) {
    console.error("usage: build.js <study folder>");
    process.exit(2);
  }
  build(path.resolve(folder))
    .then((r) => console.log(`${r.pptx}\n  ${r.slides} slides, ${r.verses.length} distinct verses\n  ${r.crossReference}`))
    .catch((e) => {
      console.error(`error: ${e.message}`);
      process.exit(1);
    });
}

module.exports = { build };
