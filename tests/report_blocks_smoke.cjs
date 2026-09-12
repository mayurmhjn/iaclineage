// Run against the structural report generated from tests/fixtures/nested_blocks.
const {launchBrowser} = require('./browser.cjs');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');

async function main() {
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      const writeText = async text => { window.__copiedLocation = text; };
      try { navigator.clipboard.writeText = writeText; }
      catch { try { Object.defineProperty(navigator, 'clipboard', {value: {writeText}}); } catch {} }
    });
    for (const [width, height] of [[1024,768],[1280,720],[1366,768],[1440,900],[1920,1080],[2560,1440]]) {
      await page.setViewportSize({width, height});
      await page.goto(pathToFileURL(path.resolve(process.argv[2])).href);
      await page.locator('#declaration-rows button').filter({hasText: 'resource.aws_instance.this'}).click();
      const firstCopy = page.locator('#field-rows [data-copy-location]').first();
      const firstLocation = await firstCopy.getAttribute('data-copy-location');
      await firstCopy.click();
      await page.waitForFunction(expected => window.__copiedLocation === expected, firstLocation);
      assert.equal(await firstCopy.textContent(), 'Copied');
      const row = field => page.locator('#field-rows tr').filter({has: page.locator(`[data-field="${field}"]`)});
      assert.equal(await page.locator('#field-rows tr').count(), 8);
      assert.match(await row('provisioner[0]').innerText(), /Provisioner 1 · remote-exec/);
      assert.match(await row('provisioner[0]').innerText(), /inline · 2 entries/);
      assert.match(await row('connection[0]').innerText(), /Resource connection settings/);
      assert.match(await row('provisioner[4]').innerText(), /unknown type/);
      await row('provisioner[0]').getByRole('button', {name: 'Trace 2 references', exact: true}).click();
      const connections = await page.locator('#connection-list').innerText();
      assert.ok(connections.includes('variable.hostname') && connections.includes('variable.username'));
      assert.ok(!connections.includes('variable.size') && !connections.includes('variable.key_path'));
      await row('provisioner[0]').locator('.field-toggle').focus();
      await page.keyboard.press('Enter');
      assert.equal(await row('provisioner[0]').locator('.field-toggle').getAttribute('aria-expanded'), 'true');
      await row('provisioner[0].inline').locator('.field-toggle').click();
      assert.equal(await page.locator('[data-field="provisioner[0].inline[0]"]').textContent(), '[0]');
      await page.locator('[data-field="provisioner[0].inline[0]"]').click();
      assert.equal(await page.locator('#connection-list tbody tr').count(), 1);
      await page.locator('#evidence-content .context-evidence summary').click();
      assert.match(await page.locator('#evidence-content').innerText(), /Attribute of the containing resource: resource.aws_instance.this/);
      const contextCopy = page.locator('#evidence-content .context-evidence [data-copy-location]').first();
      const contextLocation = await contextCopy.getAttribute('data-copy-location');
      await contextCopy.click();
      await page.waitForFunction(expected => window.__copiedLocation === expected, contextLocation);
      assert.equal(await contextCopy.textContent(), 'Copied');
      await page.locator('#field-search').fill('remote-exec');
      assert.equal(await page.locator('#field-rows [data-field="provisioner[2]"]').count(), 0);
      assert.match(await row('provisioner[1].connection[0]').innerText(), /Provisioner connection settings/);
      await page.locator('#clear-field-search').click();
      assert.equal(await row('provisioner[0]').locator('.field-toggle').getAttribute('aria-expanded'), 'true');
      await page.locator('#whole-declaration').click();
      await page.locator('[data-field="root_block_device[0]"]').click();
      assert.equal(await row('root_block_device[0]').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(231, 243, 242)');
      assert.match(await page.locator('#connection-list').innerText(), /variable.size/);
      assert.equal(await page.locator('#connection-list tbody tr').count(), 1);
      await page.locator('#field-search').fill('connection[0].host');
      await page.locator('[data-field="connection[0].host"]').click();
      assert.equal(await page.locator('#connection-list tbody tr').count(), 0);
      await page.locator('#evidence-content .context-evidence summary').focus();
      await page.keyboard.press('Enter');
      assert.match(await page.locator('#evidence-content').innerText(), /self.public_ip/);
      assert.ok(!(await page.locator('#evidence-content').innerText()).includes('No reference evidence'));
      await page.locator('#field-search').fill('provisioner[1].script');
      await page.locator('[data-field="provisioner[1].script"]').click();
      await row('provisioner[1].script').locator('.context-evidence summary').click();
      assert.match(await row('provisioner[1].script').innerText(), /Directory of the module containing this expression/);
      assert.equal(await page.locator('#connection-list tbody tr').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (process.env.UI_SCREENSHOT_DIR) {
        await page.locator('#panel-fields').screenshot({path: path.join(process.env.UI_SCREENSHOT_DIR, `blocks-${width}.png`)});
      }
    }
    assert.deepEqual(errors, []);
    console.log('Nested block labels, keyboard expansion, search, and tracing passed at six desktop resolutions.');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
