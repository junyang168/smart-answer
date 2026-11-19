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
const ending = "門徒的義從原則走到實際生活";
const professor = {
  title: "登山寶訓（論彌賽亞／門徒的使命）", at: 20, sha256: "d".repeat(64), layout: "overview",
  labels: ["登山寶訓", "論彌賽亞", "太 5:3–12 應驗賽 61", "門徒的使命", "太 5:13–16 綜合", "太 5:17–7:29 闡明，細節"],
};
const editorial = {
  title: "登山寶訓的文學結構", at: 50, sha256: "c".repeat(64), layout: "literary_structure",
  labels: [
    "太 5:3–12　彌賽亞的身份（八福的 inclusio）", "門徒的使命", "太 5:13–16　總綱：鹽與光",
    "太 5:17 – 7:12　Inclusio：門徒與舊約（律法和先知）的關係", "5:17–48", "門徒的義（勝過法利賽人）",
    "6:8–7:11 inclusio", "門徒義的實踐（施捨，禱告等）", "7:12　小結：律法與先知的總結（黃金律）",
    "7:13–27　門徒使命的結論（兩條路）", "7:28–29　敘事結尾",
  ],
};

const recording = {
  source_id: "lecture-1", transcript_id: "lecture-1", title: "第一講", media_kind: "audio",
  media_url: "/test-listening-diagrams.wav", media_duration: 65, seconds: 45,
  stretches: [{ start: 10, end: 30 }, { start: 35, end: 60 }],
  slides: [
    { at: 10, seconds: 25, title: "登山寶訓的結構", points: [
      { at: 20, text: point }, { at: 22, text: "兩大段各有自己的重點" }, { at: 23, text: elaboration },
      { at: 25, text: "先認識彌賽亞的身份" }, { at: 26, text: "再理解門徒的使命" }, { at: 27, text: ending },
    ], scriptures: [{ at: 27, label: "馬太福音 5:1–12", scripture: "mat-5-1-12" }], verses: [], diagram: professor },
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
    await figure.getByText("論彌賽亞", { exact: true }).waitFor();
    assert.equal(await figure.count(), 1);
    const label = figure.getByText("門徒的使命", { exact: true });
    const measure = await label.evaluate(node => {
      const styles = getComputedStyle(node);
      let background = "rgba(0, 0, 0, 0)";
      for (let parent = node; parent && background === "rgba(0, 0, 0, 0)"; parent = parent.parentElement) {
        background = getComputedStyle(parent).backgroundColor;
      }
      return { font: parseFloat(styles.fontSize), background };
    });
    assert(measure.font >= 14, "Labels must be readable without opening another view");
    assert.notEqual(measure.background, "rgb(255, 255, 255)");
    assert.equal(await figure.getByRole("button").count(), 0);
    assert.equal(await figure.getByText("教授的圖", { exact: true }).count(), 0);
    const visible = text => slides.getByText(text, { exact: true }).and(slides.locator(":visible"));
    const assertDiagramFits = async () => {
      const diagramBox = await figure.boundingBox();
      const paneBox = await slides.boundingBox();
      assert(diagramBox.y >= paneBox.y - 1 && diagramBox.y + diagramBox.height <= paneBox.y + paneBox.height + 1,
        JSON.stringify({ message: "The whole diagram must remain visible", viewport, diagramBox, paneBox }));
    };
    assert.equal(await visible(point).count(), 1);
    await assertDiagramFits();

    await open(22.5);
    await figure.getByText("論彌賽亞", { exact: true }).waitFor();
    if (viewport.width < 640) {
      assert.equal(await visible(elaboration).count(), 0, "A prefetched point is not the highlighted point yet");
    }

    await open(24);
    await figure.getByText("論彌賽亞", { exact: true }).waitFor();
    assert.equal(await visible(elaboration).count(), 1);
    assert.equal(await visible(point).count(), viewport.width < 640 ? 0 : 1);
    await assertDiagramFits();

    await open(27.5);
    await figure.getByText("論彌賽亞", { exact: true }).waitFor();
    assert.equal(await visible(ending).count(), 1);
    assert.equal(await visible(elaboration).count(), viewport.width < 640 ? 0 : 1);
    await assertDiagramFits();
    if (viewport.width < 640) {
      const diagramY = (await figure.boundingBox()).y;
      const scrolled = await slides.evaluate(node => {
        node.scrollTop = node.scrollHeight;
        return node.scrollTop;
      });
      assert(scrolled > 0, "Fixture must exercise a scrollable slide");
      await assertDiagramFits();
      assert.ok(Math.abs((await figure.boundingBox()).y - diagramY) <= 14, "Scrolling Scripture cannot displace the diagram");
      assert.equal(await visible(ending).count(), 1);
    } else {
      const pointBox = await visible(ending).boundingBox();
      assert.ok(pointBox.x + pointBox.width <= (await figure.boundingBox()).x,
        "Laptop points and diagram show in separate columns");
    }

    await open(38);
    assert.equal(await figure.count(), 0, "An image from the previous clip must disappear");
    await open(51);
    await figure.getByText("門徒的使命", { exact: true }).waitFor();
    assert.equal(await figure.getByText("整理", { exact: true }).count(), 0);
    assert.equal(await figure.count(), 1, "The editorial image has its own slide");
    assert.equal(await visible(point).count(), 1);
    await assertDiagramFits();
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
          const box = node.getBoundingClientRect();
          const panel = node.closest("[data-slide-scroll]");
          const bounds = panel.getBoundingClientRect();
          const [r, g, b] = getComputedStyle(node.closest("section")).backgroundColor.match(/\d+/g).map(Number);
          const labels = [...node.querySelectorAll("div,span,strong")]
            .filter(el => el.children.length === 0 && el.textContent.trim());
          return {
            fits: bounds.top <= box.top && box.bottom <= bounds.bottom && bounds.left <= box.left && box.right <= bounds.right,
            dark: Math.max(r, g, b) < 64 && node.querySelector("img") === null,
            no_zoom: node.querySelector("button, dialog") === null,
            min_font_px: labels.length ? Math.min(...labels.map(el => parseFloat(getComputedStyle(el).fontSize))) : 0,
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
