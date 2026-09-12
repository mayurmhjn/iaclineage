// Requires Playwright and Microsoft Edge; use a report from tests/fixtures/ui_details.
// Run: node tests/report_desktop_smoke.cjs <report.html>
const {launchBrowser} = require('./browser.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

async function checkPanelResize(page, width, height) {
  const panel = page.locator('#declarations');
  const handle = page.locator('#resize-declarations');
  const size = (locator, dimension) => locator.evaluate((el, dimension) => el.getBoundingClientRect()[dimension], dimension);
  async function drag(locator, x, y) {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    const start = {x: box.x + box.width / 2, y: box.y + box.height / 2};
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(start.x + x, start.y + y, {steps: 5});
    await page.mouse.up();
    assert.equal(await page.locator('body').evaluate(el => el.classList.contains('resizing-panels')), false);
  }
  const initial = await size(panel, 'width');
  await drag(handle, 64, 0);
  assert.ok(Math.abs(await size(panel, 'width') - initial - 64) < 2);
  await page.locator('#toggle-declarations').click();
  await page.locator('#toggle-declarations').click();
  assert.ok(Math.abs(await size(panel, 'width') - initial - 64) < 2);
  await handle.press('ArrowLeft');
  assert.ok(Math.abs(await size(panel, 'width') - initial - 48) < 2);
  await handle.press('Home');
  assert.equal(await size(panel, 'width'), 280);
  await drag(handle, -100, 0);
  assert.equal(await size(panel, 'width'), 280);
  await page.locator('#filters').evaluate(element => { element.open = true; });
  const panelBox = await panel.boundingBox();
  for (const selector of ['#search', '#project', '#kind', '#status', '#clear-filters']) {
    const box = await page.locator(selector).boundingBox();
    assert.ok(box.x >= panelBox.x - 1 && box.x + box.width <= panelBox.x + panelBox.width + 1,
      selector + ' should fit inside the declarations panel at its minimum width');
  }
  await page.locator('#kind').selectOption({index: 1});
  assert.notEqual(await page.locator('#kind').inputValue(), '');
  await page.locator('#clear-filters').click();
  assert.equal(await page.locator('#kind').inputValue(), '');
  await handle.press('End');
  assert.ok(await size(panel, 'width') <= Math.min(480, width * .35));
  await handle.dblclick();
  assert.equal(await size(panel, 'width'), initial);

  const evidence = page.locator('#evidence');
  const divider = page.locator('#resize-evidence');
  const stacked = width <= 1400;
  const dimension = stacked ? 'height' : 'width';
  const before = await size(evidence, dimension);
  assert.equal(await divider.getAttribute('aria-orientation'), stacked ? 'horizontal' : 'vertical');
  await drag(divider, stacked ? 0 : -64, stacked ? -64 : 0);
  assert.ok(Math.abs(await size(evidence, dimension) - before - (stacked ? -64 : 64)) < 2);
  await divider.press(stacked ? 'ArrowUp' : 'ArrowRight');
  const changed = await size(evidence, dimension);
  assert.ok(Math.abs(changed - before - (stacked ? -80 : 48)) < 2);
  await divider.press('Home');
  assert.equal(await size(evidence, dimension), stacked ? 160 : 240);
  if (stacked) assert.ok(await evidence.locator('.panel-content').evaluate(el => el.scrollHeight > el.clientHeight));
  await divider.press('End');
  assert.ok(await size(evidence, dimension) <= (stacked ? height * .8 : Math.min(560, width * .35)));
  await divider.dblclick();
  assert.ok(Math.abs(await size(evidence, dimension) - before) < 2);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
}

async function checkFieldFilter(page) {
  const search = page.locator('#field-search');
  const fields = page.locator('#field-rows [data-field]');
  await page.locator('#all-fields').click();
  assert.equal(await fields.count(), 5);
  await search.fill(' REGION ');
  assert.deepEqual(await fields.evaluateAll(elements => elements.map(el => el.dataset.field)), ['settings', 'settings.region']);
  assert.equal(await page.locator('#all-fields').isDisabled(), true);
  assert.equal(await page.locator('#field-rows .field-toggle').getAttribute('aria-expanded'), 'true');
  assert.equal(await page.locator('#connection-list tbody tr').count(), 2);
  await page.locator('#clear-field-search').click();
  assert.equal(await fields.count(), 5);
  assert.equal(await search.evaluate(el => el === document.activeElement), true);
  await page.locator('#all-fields').click();
  assert.equal(await fields.count(), 3);
  await search.fill('missing-field');
  assert.equal(await fields.count(), 0);
  assert.equal(await page.locator('#fields-empty').isVisible(), true);
  await search.fill('name');
  assert.equal(await fields.count(), 1);
  await fields.first().click();
  assert.equal(await page.locator('#connection-list tbody tr').count(), 1);
  await page.locator('#clear-field-search').click();
  assert.equal(await fields.count(), 3);
  assert.equal(await page.locator('#connection-list tbody tr').count(), 1);
  await page.locator('#whole-declaration').click();
  assert.equal(await page.locator('#connection-list tbody tr').count(), 2);
  await search.fill('name');
  await page.locator('#declaration-nav button').filter({hasText:'variable.first'}).click();
  assert.equal(await search.inputValue(), '');
  await page.locator('#back').click();
  assert.equal(await search.inputValue(), '');
}

async function main() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'iaclineage-desktop-'));
  const browser = await launchBrowser();
  try {
    // Model an indexed downloaded declaration while retaining the fixture's edges.
    const html = fs.readFileSync(process.argv[2], 'utf8');
    const pattern = /(<script id="lineage-data" type="application\/json">)([\s\S]*?)(<\/script>)/;
    const data = JSON.parse(html.match(pattern)[2]);
    const demo = data.nodes.find(node => node.address === 'resource.example_service.demo');
    const field = (name, children = []) => ({...demo.fields[0], path:name, kind:children.length ? 'object' : 'literal', value:null, children});
    demo.fields.push(field('settings', [field('settings.region'), field('settings.enabled')]));
    const cached = data.nodes.find(node => node.address === 'variable.second');
    cached.path = '.terraform/modules/example/main.tf';
    cached.location = cached.path + ':1:1-1:10';
    data.diagnostics.push({path: cached.path, location: cached.location, code: 'IAC101', message: 'Cached fixture diagnostic'});
    const report = path.join(directory, 'report.html');
    fs.writeFileSync(report, html.replace(pattern, (_, start, content, end) => start + JSON.stringify(data) + end));
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const [width, height] of [[1024,768],[1280,720],[1366,768],[1440,900],[1920,1080],[2560,1440]]) {
      await page.setViewportSize({width, height});
      await page.goto(pathToFileURL(report).href);
      await page.locator('#declaration-rows button').filter({hasText: 'resource.example_service.demo'}).click();
      assert.equal(await page.locator('#connection-list tbody tr').count(), 2);
      await checkPanelResize(page, width, height);
      await checkFieldFilter(page);
      await page.locator('#toggle-declarations').click();
      assert.equal(await page.locator('#declarations').isVisible(), false);
      await page.locator('#maximize-lineage').click();
      assert.equal(await page.locator('#panel-fields').isVisible(), false);
      assert.equal(await page.locator('#evidence').isVisible(), true);
      assert.equal(await page.locator('#maximize-lineage').getAttribute('aria-pressed'), 'true');
      assert.ok(await page.locator('#lineage-viewport').evaluate(el => el.clientWidth) >= width - 400);
      if (width > 1400) assert.ok(await page.locator('#evidence').evaluate(el => el.getBoundingClientRect().width) <= 360);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#panel-fields').isVisible(), true);
      assert.equal(await page.locator('#maximize-lineage').evaluate(el => el === document.activeElement), true);
      await page.locator('#hide-terraform').check();
      assert.equal(await page.locator('#connection-list tbody tr').count(), 1);
      assert.equal(await page.locator('#source-scope-note').isVisible(), true);
      assert.equal(await page.locator('#report-diagnostics-title').textContent(), 'Report diagnostics (0)');
      for (const tab of ['overview','fields','references','diagnostics','source']) {
        await page.locator('#tab-' + tab).click();
        assert.equal(await page.locator('#hide-terraform').isChecked(), true);
        assert.equal(await page.locator('#workspace').innerText().then(text => text.includes('.terraform/modules/example')), false);
      }
      await page.locator('#toggle-declarations').click();
      await page.locator('#browse').click();
      assert.equal(await page.locator('#declaration-rows tr').count(), 2);
      await page.locator('#hide-terraform').uncheck();
      assert.equal(await page.locator('#declaration-rows tr').count(), 3);
      await page.locator('#declaration-rows button').filter({hasText: 'variable.second'}).click();
      await page.locator('#hide-terraform').check();
      assert.equal(await page.locator('#browse-view').isVisible(), true);
      assert.equal(await page.locator('#evidence').isVisible(), false);
      await page.locator('#declaration-rows button').filter({hasText: 'resource.example_service.demo'}).click();
      assert.equal(await page.locator('#back').isDisabled(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (process.env.UI_SCREENSHOT_DIR) {
        await page.screenshot({path: path.join(process.env.UI_SCREENSHOT_DIR, `desktop-${width}.png`), fullPage: true});
        await page.locator('#maximize-lineage').click();
        await page.screenshot({path: path.join(process.env.UI_SCREENSHOT_DIR, `maximized-${width}.png`), fullPage: true});
      }
    }
    assert.deepEqual(errors, []);
    console.log('Desktop panel resizing, scope, navigation, maximize, Escape, and overflow checks passed at six resolutions.');
  } finally {
    await browser.close();
    fs.rmSync(directory, {recursive: true, force: true});
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
