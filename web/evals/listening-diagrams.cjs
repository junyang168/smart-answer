/** Run against the local web app. Optional PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE. */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const base = process.env.WANG_EVAL_URL || "http://localhost:3788";
const source = "紐約靈命進深會";
const passage = "mat-5-1-48";
const section = "mat-5-1-12";
const point = "先講彌賽亞的身份，再講門徒的使命";
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="850" height="500"><rect width="850" height="500" fill="white"/></svg>';
const image_src = "data:image/svg+xml;base64," + Buffer.from(svg).toString("base64");
const diagram = (key, kind, title, origin, at) => ({
  kind, title, origin, at, image_src, sha256: key.repeat(64),
});
const professor = diagram("d", "professor", "登山寶訓（論彌賽亞／門徒的使命）", "教授母本講義", 20);
const editorial = diagram("c", "editorial", "登山寶訓的文學結構", "釋經筆記整理", 50);

const recording = {
  source_id: "lecture-1", transcript_id: "lecture-1", title: "第一講", media_kind: "audio",
  media_url: "/test-listening-diagrams.wav", media_duration: 65, seconds: 45,
  stretches: [{ start: 10, end: 30 }, { start: 35, end: 60 }],
  slides: [
    { at: 10, seconds: 25, title: "登山寶訓的結構", points: [{ at: 20, text: point }],
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
    assert.equal(await figure.count(), 0, "The image must wait for the actual statement");
    await open(21);
    const zoom = figure.getByRole("button", { name: "放大" + professor.title });
    await zoom.waitFor();
    assert.equal(await figure.count(), 1);
    assert(await slides.getByText(point, { exact: true }).isVisible(), "The point stays on the slide");
    assert(await figure.getByText("教授的圖", { exact: true }).isVisible());
    const pane = await slides.boundingBox();
    const pointBox = await slides.getByText(point, { exact: true }).boundingBox();
    const graphic = await zoom.getByRole("img").boundingBox();
    assert(pointBox.y >= pane.y && pointBox.y + pointBox.height <= pane.y + pane.height,
      "The first point must be on screen with its image");
    assert(graphic.y >= pane.y && graphic.y + graphic.height <= pane.y + pane.height,
      JSON.stringify({pane, pointBox, graphic}));
    await zoom.click();
    const dialog = page.getByRole("dialog", { name: professor.title });
    await dialog.waitFor();
    await dialog.getByRole("button", { name: "關閉" }).click();
    await dialog.waitFor({ state: "hidden" });
    assert(await slides.getByText(point, { exact: true }).isVisible());

    await open(38);
    assert.equal(await figure.count(), 0, "An image from the previous clip must disappear");
    await open(51);
    await figure.getByText("整理", { exact: true }).waitFor();
    assert.equal(await figure.count(), 1, "The editorial image replaces the previous image");
    assert(await slides.getByText(point, { exact: true }).isVisible());
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    console.log(`${viewport.width}×${viewport.height}: listening diagrams passed`);
  } finally {
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 1100, height: 850 }]) {
      await evaluate(browser, viewport);
    }
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
