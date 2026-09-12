// Preserve Windows Edge checks; use Playwright Chromium on Linux/macOS.
const {chromium} = require('playwright');

exports.launchBrowser = () => chromium.launch({
  headless: true,
  channel: process.env.IACLINEAGE_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined),
});
