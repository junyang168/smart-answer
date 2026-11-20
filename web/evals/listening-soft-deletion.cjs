/** Verify the reviewed Matthew 5:31–32 cut on a local web app and its isolated media. */
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const root = process.env.WANG_CUT_EVAL_DATA;
const base = process.env.WANG_EVAL_URL || "http://127.0.0.1:3824";
if (!root) throw new Error("Set WANG_CUT_EVAL_DATA to the isolated test run");
const index = JSON.parse(readFileSync(join(root, "public.json")));
const section = index.sections.find((row) => row.sub_unit_id === "6.1");
const sermon = section.sermons[0];
const assetName = sermon.media_url.split("/").at(-1);
const asset = readFileSync(join(root, "video", assetName));
const left = sermon.stretches.find((clip) => clip.end === 378.05);
const right = sermon.stretches.find((clip) => clip.start >= 620.9);
const url = new URL("/resources/wang-repository/books/mat/5/listen", base);

async function evaluate(browser, viewport) {
  const page = await browser.newPage({ viewport });
  try {
    await page.route("**/api/public/original-audio/**", (route) => route.fulfill({ json: index }));
    await page.route(`**/dev-media/${assetName}`, (route) => {
      const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || "");
      if (!range) return route.fulfill({ contentType: "audio/mpeg", body: asset });
      const start = Number(range[1]);
      const end = range[2] ? Math.min(Number(range[2]), asset.length - 1) : asset.length - 1;
      return route.fulfill({
        status: 206, contentType: "audio/mpeg",
        headers: { "accept-ranges": "bytes", "content-range": `bytes ${start}-${end}/${asset.length}` },
        body: asset.subarray(start, end + 1),
      });
    });
    const open = async (time, target = section) => {
      url.search = new URLSearchParams({
        source: "紐約靈命進深會", section: target.passage, recording: sermon.source_id, at: String(time),
      }).toString();
      await page.goto(url.href);
    };
    const audio = page.locator("audio");
    const waitForTime = (time) => page.waitForFunction(
      (time) => Math.abs((document.querySelector("audio")?.currentTime ?? 0) - time) < 0.2,
      time,
    );
    await open(377);
    await waitForTime(left.media_start + 377 - left.start);
    assert.match(await audio.getAttribute("src"), /listening-cut-/);
    await page.getByRole("button", { name: "播放", exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll("button")].some(
      (node) => node.textContent.includes("聖經給我們看到一個很好的例子") && node.className.includes("bg-amber-"),
    ));
    assert.match(await page.locator("[data-slide-scroll]").innerText(), /離婚與避不開的惡/);
    assert((await audio.evaluate((element) => element.currentTime)) >= right.media_start - 0.1);
    await open(450);
    await waitForTime(right.media_start);
    assert(await audio.evaluate((element) => element.paused));
    await page.getByRole("button", { name: "播放", exact: true }).click();
    await page.waitForFunction((at) => document.querySelector("audio")?.currentTime >= at + 1, right.media_start);
    await page.getByRole("button", { name: "暫停", exact: true }).click();
    const [bookmark] = await page.evaluate(() => Object.values(localStorage)
      .map((item) => { try { return JSON.parse(item); } catch { return null; } })
      .filter((item) => item?.audioRevision));
    assert(bookmark.time >= right.start && bookmark.time < right.end);
    const withoutTimestamp = new URL(url);
    withoutTimestamp.searchParams.delete("at");
    withoutTimestamp.searchParams.delete("recording");
    await page.goto(withoutTimestamp.href);
    await waitForTime(right.media_start + bookmark.time - right.start);
    assert(await audio.evaluate((element) => element.paused));
    const ending = index.sections.find((row) => row.sub_unit_id === "6.2");
    const endingClip = ending.sermons[0].stretches.find((clip) => clip.start <= 3563 && clip.end > 3563);
    const slides = page.locator("[data-slide-scroll]");
    const finalPoint = slides.getByText("休書須准自由再婚，顯示當時視為當然", { exact: true });
    await open(3560, ending);
    await waitForTime(endingClip.media_start + 3560 - endingClip.start);
    assert.equal(await finalPoint.count(), 0);
    await open(3563.4, ending);
    await waitForTime(endingClip.media_start + 3563.4 - endingClip.start);
    await finalPoint.waitFor();
    const currentCaption = await page.getByRole("button", { name: /還有古時候，我講，休書必須有這句話/ })
      .first().getAttribute("class");
    assert.match(currentCaption, /bg-amber-/);
    console.log(`${viewport.width}×${viewport.height}: source link, natural transition, captions, slide and resume passed`);
  } finally {
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 },
      { width: 375, height: 667 }, { width: 320, height: 568 }]) {
      await evaluate(browser, viewport);
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
