/** Run against the local web app. Optional PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE. */
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const base = process.env.WANG_EVAL_URL || "http://localhost:3788";
const source = "紐約靈命進深會";
const passage = "mat-5-1-48";
const section = "mat-5-1-12";
const point = "先講彌賽亞的身份，再講門徒的使命";
const elaboration = "新舊約都是宗主國與附庸國之約：宗主國先施恩，再提出要求";
// His drawings are shown as drawn: a white-ground SVG, never re-laid-out or colour-inverted.
const svg = (words) => "data:image/svg+xml;base64," + Buffer.from(
  `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300"><rect width="600" height="300" fill="#fff"/>` +
  words.map((w, i) => `<text x="20" y="${60 + i * 60}" font-size="28">${w}</text>`).join("") + "</svg>").toString("base64");
const professor = { title: "登山寶訓（論彌賽亞／門徒的使命）", at: 20, sha256: "d".repeat(64), image_src: svg(["論彌賽亞", "門徒的使命"]) };
const editorial = { title: "登山寶訓的文學結構", at: 50, sha256: "c".repeat(64), image_src: svg(["太 5:13–16", "太 5:17–7:12"]) };

const recording = {
  source_id: "lecture-1", transcript_id: "lecture-1", title: "第一講", media_kind: "audio",
  media_url: "/test-listening-diagrams.wav", media_duration: 65, seconds: 45,
  stretches: [{ start: 10, end: 30 }, { start: 35, end: 60 }],
  slides: [
    { at: 10, seconds: 25, title: "登山寶訓的結構", points: [{ at: 20, text: point }, { at: 23, text: elaboration }],
      scriptures: [], verses: [], diagram: professor },
    { at: 35, seconds: 25, title: "整理登山寶訓的結構", points: [{ at: 50, text: point }],
      scriptures: [], verses: [], diagram: editorial },
  ],
  transcript: [{ at: 10, end: 30, text: "第一個片段。" }, { at: 35, end: 60, text: "第二個片段。" }],
  judgements: [], spoken: [], readings: [], original_language_events: [],
};

async function evaluate(browser, viewport) {
  const page = await browser.newPage({ viewport });
  try {
    await page.route("**/api/public/repository/books/mat/5", route => route.fulfill({ json: {
      chapter: 5, name: "馬太福音", sources: [{ name: source, page: { kind: "chapter", passage } }],
    } }));
    await page.route("**/api/public/original-audio/mat-5-1-48?*", route => route.fulfill({ json: {
      sections: [{ passage: section, label: "登山寶訓的結構", sermons: [recording] }],
    } }));
    await page.route("**/test-listening-diagrams.wav", route => route.abort());
    const slides = page.locator("[data-slide-scroll]");
    const figure = slides.locator("figure");
    const open = async (at) => {
      const query = new URLSearchParams({ source, section, recording: recording.source_id, at: String(at) });
      await page.goto(new URL("/resources/wang-repository/books/mat/5/listen?" + query, base).href);
      await slides.getByRole("heading", { name: "登山寶訓的結構" }).waitFor();
    };

    await open(19.5);
    assert.equal(await figure.count(), 0, "The image waits for him to start that argument");
    await open(21);
    await figure.waitFor();
    const look = await figure.evaluate(node => {
      const img = node.querySelector("img");
      return { filter: getComputedStyle(img).filter, card: getComputedStyle(img.parentElement).backgroundColor };
    });
    assert.equal(look.filter, "none", "His drawing is not colour-inverted");
    assert.equal(look.card, "rgb(255, 255, 255)", "White-ground drawings sit on a white card");
    assert(await slides.getByText(point, { exact: true }).isVisible(), "Points stay visible with the diagram");
    await figure.getByRole("button").click();
    await page.getByRole("dialog", { name: professor.title }).waitFor();
    await page.getByRole("dialog", { name: professor.title }).click();
    await open(24);
    assert(await slides.getByText(elaboration, { exact: true }).isVisible());
    await open(38);
    assert.equal(await figure.count(), 0, "An image from the previous clip must disappear");
    await open(51);
    await figure.waitFor();
    assert.equal(await figure.count(), 1, "The editorial image has its own slide");
    assert(await slides.getByText(point, { exact: true }).isVisible());
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    console.log(`${viewport.width}×${viewport.height}: listening diagrams passed`);
  } finally {
    await page.close();
  }
}

// Add real-diagram measurements to the same browser evidence read by chapter_listening_eval.
async function measureIndex(browser, index, evidence) {
  const [book, chapter] = index.passage.split("-");
  const chapterPath = `/api/public/repository/books/${book}/${chapter}`;
  const examples = new Map();
  for (const section of index.sections) {
    for (const recording of section.sermons) {
      for (const { diagram } of recording.slides) {
        if (!diagram || examples.has(diagram.sha256)) continue;
        const clip = recording.stretches.find(({ start, end }) => start <= diagram.at && diagram.at < end);
        if (clip) examples.set(diagram.sha256, { diagram, clip, section, recording });
      }
    }
  }
  for (const view of evidence.viewports) {
    view.diagrams = [];
    const page = await browser.newPage({ viewport: { width: view.width, height: view.height } });
    try {
      await page.route(`**${chapterPath}`, route => route.fulfill({ json: {
        chapter: Number(chapter), name: "馬太福音", sources: [{ name: source, page: { kind: "chapter", passage: index.passage } }],
      } }));
      await page.route(`**/api/public/original-audio/${index.passage}?*`, route => route.fulfill({ json: index }));
      await page.route("**/web/video/**", route => route.abort());
      const pane = page.locator("[data-slide-scroll]");
      for (const { diagram, clip, section, recording } of examples.values()) {
        const open = async (at) => {
          const query = new URLSearchParams({ source, section: section.passage, recording: recording.source_id, at: String(at) });
          await page.goto(new URL(`/resources/wang-repository/books/${book}/${chapter}/listen?${query}`, base).href);
          await pane.getByRole("heading").first().waitFor();
        };
        const figure = pane.getByRole("figure", { name: diagram.title, exact: true });
        const before = Math.max(clip.start, diagram.at - 0.05);
        let hiddenBefore = true;
        if (before < diagram.at) {
          await open(before);
          hiddenBefore = await figure.count() === 0;
        }
        await open(diagram.at);
        await figure.waitFor();
        const observation = await figure.evaluate(node => {
          const img = node.querySelector("img");
          const box = img.getBoundingClientRect();
          const bounds = node.closest("[data-slide-scroll]").getBoundingClientRect();
          const point = node.closest("section").querySelector("ol li");
          return {
            fits: bounds.left <= box.left && box.right <= bounds.right && box.width > 0 && box.height > 0,
            as_drawn: getComputedStyle(img).filter === "none" && getComputedStyle(img.parentElement).backgroundColor === "rgb(255, 255, 255)",
            points_visible: !point || (point.offsetParent !== null && getComputedStyle(point).visibility !== "hidden"),
            enlarge: node.querySelector("button") !== null,
          };
        });
        view.diagrams.push({ sha256: diagram.sha256, hidden_before: hiddenBefore, ...observation });
      }
    } finally {
      await page.close();
    }
  }
  return evidence;
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    if (process.argv.length === 4) {
      const [index, evidence] = process.argv.slice(2).map(path => JSON.parse(readFileSync(path, "utf8")));
      console.log(JSON.stringify(await measureIndex(browser, index, evidence), null, 2));
      return;
    }
    for (const viewport of [{ width: 320, height: 568 }, { width: 375, height: 667 }, { width: 390, height: 844 }, { width: 1100, height: 850 }]) {
      await evaluate(browser, viewport);
    }
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
