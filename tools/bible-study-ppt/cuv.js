// 和合本 (traditional) from the cache the Python side fills:
// $DATA_BASE_DIR/bible/cuv/<USFM book>/<chapter>.json, {"1": "...", ...}.
// Slides never fetch scripture themselves; a chapter that is not cached yet is
// cached by `python -m backend.bible_study.sources` (or backend.bible_study.cuv).
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "../..");

function dataBaseDir() {
  if (process.env.DATA_BASE_DIR) return process.env.DATA_BASE_DIR;
  const env = path.join(REPO_ROOT, ".env");
  if (fs.existsSync(env)) {
    const m = fs.readFileSync(env, "utf8").match(/^DATA_BASE_DIR=["']?([^"'\n]+)["']?\s*$/m);
    if (m) return m[1];
  }
  throw new Error("DATA_BASE_DIR is required");
}

function cuvDir() {
  return process.env.CUV_DIR || path.join(dataBaseDir(), "bible", "cuv");
}

const cache = new Map();
function chapterVerses(book, chapter) {
  const key = `${book}/${chapter}`;
  if (!cache.has(key)) {
    const file = path.join(cuvDir(), book, `${chapter}.json`);
    if (!fs.existsSync(file)) {
      throw new Error(`${key} is not cached: .venv/bin/python -c "from backend.bible_study.cuv import chapter; chapter('${book.toLowerCase()}', ${chapter})"`);
    }
    cache.set(key, JSON.parse(fs.readFileSync(file, "utf8")));
  }
  return cache.get(key);
}

module.exports = { chapterVerses, dataBaseDir };
