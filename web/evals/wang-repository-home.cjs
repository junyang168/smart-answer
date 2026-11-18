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
    const matthew = main.getByRole("link", { name: /^馬太福音/ });
    const mark = main.getByRole("link", { name: /^馬可福音/ });
    await matthew.waitFor();
    const first = await matthew.boundingBox();
    const next = await mark.boundingBox();
    assert(first.y + first.height <= viewport.height, "The first book must fit on the initial screen");
    assert.equal((await matthew.innerText()).trim(), "馬太福音", "Book entry should show the name only");
    if (viewport.width < 400) {
      assert.equal(first.y, next.y, "First two books should share a row on mobile");
    }
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Page must not scroll horizontally");

    const filters = main.getByText("篩選講道與筆記來源", { exact: true });
    const topic = main.getByRole("link", { name: "按講論主題" });
    const articles = main.getByRole("link", { name: "綜合文章" });
    for (const link of [filters, topic, articles]) {
      assert((await link.boundingBox()).y >= first.y + first.height, "Secondary entries belong after the books");
    }
    assert.match(await topic.getAttribute("href"), /\/library\?view=topic$/);
    assert.match(await articles.getAttribute("href"), /\/library\?kind=article$/);

    const group = main.getByRole("group", { name: "來源" });
    assert.equal(await group.isVisible(), false, "New readers should see books before a list of source choices");
    await filters.click();
    await group.getByRole("button", { name: notes }).click();
    await page.waitForURL((current) => current.searchParams.get("source") === notes);
    await main.getByText("目前來源", { exact: false }).waitFor();
    assert.equal(await mark.count(), 0);
    assert.equal(new URL(await matthew.getAttribute("href"), url).searchParams.get("source"), notes);

    await page.reload();
    await main.getByText("目前來源", { exact: false }).waitFor();
    await main.getByRole("button", { name: "清除篩選" }).click();
    await mark.waitFor();
    assert.equal(new URL(page.url()).searchParams.has("source"), false);
    await matthew.click();
    await page.getByRole("region", { name: "釋經系列" }).waitFor();
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
