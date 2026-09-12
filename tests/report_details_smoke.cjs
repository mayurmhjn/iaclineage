// Requires Playwright; see docs/platform-support.md for browser setup.
// Generate: uv run iaclineage report tests/fixtures/ui_details --format interactive-html --output <report.html>
// Run: node tests/report_details_smoke.cjs <report.html>
const {launchBrowser} = require('./browser.cjs');
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const path = require("node:path");

async function main() {
  assert.ok(process.argv[2], "Pass a report generated from tests/fixtures/ui_details");
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 950});
      await page.goto(pathToFileURL(path.resolve(process.argv[2])).href);
      await page.locator("#declaration-rows button").filter({hasText: "resource.example_service.demo"}).click();
      const details = page.locator(".lineage-card > details");
      const all = page.locator("#all-lineage-details");
      assert.equal(await details.count(), 3);
      await all.click();
      assert.equal(await page.locator(".lineage-card > details[open]").count(), 3);
      assert.equal(await all.textContent(), "Collapse all details");

      // Individual toggles still work after bulk expansion and update the bulk label.
      await details.first().locator("summary").click();
      await page.waitForFunction(() => document.getElementById("all-lineage-details").textContent === "Expand all details");
      assert.equal(await page.locator(".lineage-card > details[open]").count(), 2);
      await page.locator("#depth").selectOption("2");
      assert.equal(await page.locator(".lineage-card > details[open]").count(), 2);
      await all.click();
      await page.locator("#depth").selectOption("1");
      assert.equal(await page.locator(".lineage-card > details[open]").count(), 3);
      await all.click();
      assert.equal(await page.locator(".lineage-card > details[open]").count(), 0);
      await details.first().locator("summary").click();
      assert.equal(await page.locator(".lineage-card > details[open]").count(), 1);

      // Whole declaration restores both independent field references without changing depth/direction.
      await page.locator('[data-direction="up"]').click();
      await page.locator("#depth").selectOption("2");
      await page.locator('#field-rows button[data-field="name"]').click();
      assert.equal(await page.locator("#connection-list tbody tr").count(), 1);
      assert.ok((await page.locator("#connection-list").innerText()).includes("variable.first"));
      assert.equal(await page.locator("#whole-declaration").isVisible(), true);
      await page.locator("#whole-declaration").click();
      assert.equal(await page.locator("#connection-list tbody tr").count(), 2);
      assert.ok((await page.locator("#connection-list").innerText()).includes("variable.second"));
      assert.equal(await page.locator("#whole-declaration").isVisible(), false);
      assert.equal(await page.locator("#lineage-heading").textContent(), "Focused lineage");
      assert.equal(await page.locator("#depth").inputValue(), "2");
      assert.equal(await page.locator('[data-direction="up"]').getAttribute("aria-pressed"), "true");

      await page.locator("#lineage-view").selectOption("table");
      assert.equal(await all.isDisabled(), true);
      await page.locator("#lineage-view").selectOption("both");
      assert.equal(await all.isEnabled(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (process.env.UI_SCREENSHOT_DIR) {
        await all.click();
        await page.screenshot({path: path.join(process.env.UI_SCREENSHOT_DIR, `details-${width}.png`), fullPage: true});
      }
    }
    assert.deepEqual(errors, []);
    console.log("Lineage bulk/individual details and whole-declaration tracing passed at 1440px and 800px.");
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
