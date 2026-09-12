// Developer-only runner: generate offline fixture reports and run maintained checks.
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const temporaryRoot = fs.realpathSync(os.tmpdir());
const output = fs.mkdtempSync(path.join(temporaryRoot, 'iaclineage-browser-'));

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: root, stdio: 'inherit', timeout: 120_000,
  });
  if (result.error) throw new Error(`${command} could not finish: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`${command} failed (${result.status ?? result.signal}).`);
}

try {
  for (const fixture of ['references', 'ui_details', 'nested_blocks']) {
    const cli = process.env.IACLINEAGE_CLI;
    run(cli || 'uv', [...(cli ? [] : ['run', 'iaclineage']), 'report', `tests/fixtures/${fixture}`,
      '--format', 'interactive-html', '--output', path.join(output, `${fixture}.html`)]);
  }
  for (const [script, fixture] of [
    ['report_ui_smoke.cjs', 'references'],
    ['report_details_smoke.cjs', 'ui_details'],
    ['report_desktop_smoke.cjs', 'ui_details'],
    ['report_blocks_smoke.cjs', 'nested_blocks'],
  ]) {
    console.log(`Running ${script}...`);
    run(process.execPath, [path.join(root, 'tests', script), path.join(output, `${fixture}.html`)]);
  }
  console.log('All browser regression checks passed.');
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  // Delete only the exact temporary directory created by this run.
  const resolved = fs.realpathSync(output);
  if (path.dirname(resolved) !== temporaryRoot || !path.basename(resolved).startsWith('iaclineage-browser-')) {
    throw new Error('Refusing to clean an unexpected browser-test path.');
  }
  fs.rmSync(resolved, {recursive: true});
}
