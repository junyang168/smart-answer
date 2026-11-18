/** Run against the Wang repository dev server with Playwright and Chromium installed.
 * WANG_EVAL_URL defaults to http://localhost:3648. PLAYWRIGHT_MODULE can point
 * at a separate Playwright installation. CHROMIUM_EXECUTABLE is optional.
 */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const url = new URL("/resources/wang-repository", process.env.WANG_EVAL_URL || "http://localhost:3648");
const notes = "教授釋經筆記";

async function evaluate(browser, viewport) {
  const page = await browser.newPage({ viewport });
  try {
    await page.goto(url.href);
    const main = page.getByRole("main");
    const open = main.getByRole("region", { name: "已開放" });
    const matthew = open.getByRole("link", { name: "馬太福音", exact: true });
    await matthew.waitFor();
    const first = await matthew.boundingBox();
    assert(first.y + first.height <= viewport.height, "Matthew must fit on the initial screen");
    assert.equal(await open.getByRole("link").count(), 1, "Only Matthew has a verified current PPT release");
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Page must not scroll horizontally");

    const preparation = main.getByRole("region", { name: "整理校對中" });
    assert.deepEqual(await preparation.getByRole("listitem").allTextContents(), [
      "約翰福音", "羅馬書", "哥林多前書", "哥林多後書", "提摩太前書", "提摩太後書", "提多書",
    ]);
    assert.equal(await preparation.getByRole("link").count(), 0);
    assert.equal(await preparation.getByRole("button").count(), 0);
    for (const title of ["按講論主題", "綜合文章"]) {
      assert((await main.getByRole("link", { name: title }).boundingBox()).y >= first.y + first.height);
    }

    await matthew.click();
    const series = page.getByRole("region", { name: "釋經系列" });
    const retreat = series.getByRole("article").filter({
      has: page.getByRole("heading", { name: "《馬太福音 24–28 釋經》", exact: true }),
    });
    await retreat.getByText("整理校對中", { exact: true }).waitFor();
    assert.equal(await retreat.getByRole("link").count(), 0);
    const oldSeries = new URL(page.url());
    oldSeries.searchParams.set("view", "matthew-2007");
    await page.goto(oldSeries.href);
    await retreat.getByText("整理校對中", { exact: true }).waitFor();
    assert.equal(await page.getByRole("link", { name: "查看講次", exact: true }).count(), 0);
    const book = new URL(oldSeries);
    book.search = "";
    const nyscName = "紐約靈命進深會";
    const nysc = new URL(book);
    nysc.searchParams.set("source", nyscName);
    const chapterRow = (chapter) => main.getByRole("listitem").filter({
      has: page.getByRole("heading", { name: `第 ${chapter} 章`, exact: true }),
    });
    await page.goto(nysc.href);
    await chapterRow(7).getByRole("link", { name: "聆聽原聲", exact: true }).waitFor();
    const laterChapters = main.getByRole("listitem").filter({
      has: page.getByRole("heading", { name: /^第 (?:[89]|[1-2][0-9]) 章$/ }),
    });
    const pending = laterChapters.filter({ hasText: "整理校對中" });
    assert((await laterChapters.count()) > 0);
    assert.equal(await pending.count(), await laterChapters.count());
    assert.equal(await chapterRow(8).evaluate((row) => row.classList.contains("bg-stone-100")), true);
    assert.equal(await chapterRow(7).getByText("整理校對中", { exact: true }).count(), 0);
    assert.equal(await pending.getByRole("link").count(), 0);
    assert.equal(await pending.getByRole("button").count(), 0);

    book.searchParams.set("view", "chapters");
    await page.goto(book.href);
    await chapterRow(8).getByText("整理校對中", { exact: true }).waitFor();
    const chapterLinks = await chapterRow(8).getByRole("link").evaluateAll((links) => links.map((link) => link.href));
    assert(chapterLinks.some((href) => new URL(href).searchParams.get("source") === notes));
    assert(!chapterLinks.some((href) => new URL(href).searchParams.get("source") === nyscName));

    const filtered = new URL(url);
    filtered.searchParams.set("source", notes);
    await page.goto(filtered.href);
    await matthew.waitFor();
    assert.equal(new URL(await matthew.getAttribute("href"), url).searchParams.get("source"), notes);
    assert.equal(await preparation.getByRole("link").count(), 0);

    await matthew.click();
    const overviews = main.getByRole("region", { name: "結構與總覽" });
    const summary = "總結：登山寶訓的文學結構";
    const summaryLink = overviews.getByRole("link", { name: summary, exact: true });
    await summaryLink.waitFor();
    assert.equal(await overviews.getByRole("link", { name: "引言：登山寶訓的結構", exact: true }).count(), 1);
    assert.equal(await overviews.getByRole("link", { name: "福音書的可信性與馬太福音的結構", exact: true }).count(), 1);
    await summaryLink.click();
    await main.getByRole("heading", { name: summary, exact: true }).waitFor();
    console.log(`${viewport.width}×${viewport.height}: repository entry passed`);
  } finally {
    await page.close();
  }
}

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 1100, height: 850 }]) {
      await evaluate(browser, viewport);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
