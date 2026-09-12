// Optional browser regression check. Requires Playwright; see docs/platform-support.md for browser setup.
// Generate the input with:
// uv run iaclineage report tests/fixtures/references --format interactive-html --output <report.html>
// Run: node tests/report_ui_smoke.cjs <report.html>
const {launchBrowser} = require('./browser.cjs');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
(async()=>{
 assert.ok(process.argv[2], 'Pass an interactive report generated from tests/fixtures/references');
 const browser=await launchBrowser();
 const page=await browser.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));
 for(const width of [1440,900,390,720]){
  await page.setViewportSize({width,height:950});
  await page.goto(pathToFileURL(path.resolve(process.argv[2])).href);
  assert.equal(await page.locator('#declaration-nav').isVisible(),false);
  await page.locator('#search').fill('no-match-anywhere');
  assert.equal(await page.locator('#table-empty').isVisible(),true);
  await page.locator('#search').fill('');
  await page.locator('#declaration-rows button').first().click();
  assert.equal(await page.locator('#whole-declaration').isVisible(),false);
  assert.equal(await page.locator('#expand-lineage').isVisible(),false);
  await page.locator('.lineage-options summary').focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('#expand-lineage').isVisible(),true);
  await page.locator('.lineage-options summary').click();
  await page.locator('#field-rows button[data-field]').first().click();
  assert.equal(await page.locator('#whole-declaration').isVisible(),true);
  await page.locator('#whole-declaration').click();
  assert.equal(await page.locator('#whole-declaration').isVisible(),false);
  await page.locator('.lineage-options summary').click();
  assert.equal(await page.locator('#lineage-view').isVisible(),true);
  await page.locator('#lineage-view').focus();
  assert.equal(await page.evaluate(()=>document.activeElement.id),'lineage-view');
  for(const view of ['table','graph','both']){
   await page.locator('#lineage-view').selectOption(view);
   const target=view==='table'?'#connection-list button[data-edge]':'#lineage-canvas button[data-edge]';
   await page.locator(target).first().click();
   await page.locator('#return-to-trace').click();
   assert.equal(await page.evaluate(()=>document.activeElement.hasAttribute('data-edge')),true);
  }
  await page.locator('#tab-fields').focus();await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#tab-references').getAttribute('aria-selected'),'true');
  await page.locator('#tab-fields').click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`overflow at ${width}`);
  await page.locator('#browse').click();
 }
 assert.deepEqual(errors,[]); await browser.close();console.log('Browser workflow passed at 1440, 900, 390, and 720 CSS pixels.');
})().catch(e=>{console.error(e);process.exit(1)});
