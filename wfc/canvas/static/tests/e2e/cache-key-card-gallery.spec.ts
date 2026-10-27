/**
 * The Cache-key inputs card's hovers, asserted and photographed.
 *
 * One plot run with demo-like long names (so chips truncate at the panel's
 * width), a parent, a sample read and a long param, served by mocked
 * `/api/wfc/*` routes. Each hover the card has is shown in turn: every hash
 * tag's explanation, a copy icon's "Copy full hash" and its "Copied", the
 * name chips' full name and where a click goes, the long value's preview, and
 * the ? popover. The test asserts each tooltip's text on every run, and
 * writes one PNG per hover to `tests/e2e/gallery/cache-key-card/`, the
 * committed review gallery of the card's hovers. The write is OPT-IN
 * (`CAPTURE_GALLERY`, see playwright.config.ts): `npm run test:e2e:gallery`.
 */
import { expect, test, type Page } from '@playwright/test';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CAPTURE_GALLERY } from '../../playwright.config';

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'gallery', 'cache-key-card');
const T0 = 1_700_000_000_000;

const CODE_FP = 'ebcb33f8' + '1'.repeat(56);
const COMMIT = '170fb77' + '3'.repeat(33);

function run(p: Record<string, unknown>) {
  return {
    id: 'r1', module: '__demo__', method: '__demo__plot', version: 1, versionIdentity: 'b7e1d3a9c2f40518',
    timestamp: T0, duration: 1.5, status: 'success', inputs: {}, outputs: {}, outputAvailability: {},
    metrics: {}, dataSource: 'treated_cells', parentRunIds: [], upstreamRunIds: [], parents: [],
    sampleInputs: [], codeFingerprint: CODE_FP, envName: 'wfc-demo-env',
    envFingerprint: 'fde24f9d' + '2'.repeat(24), gitCommit: COMMIT, wfcVersion: '0.6.1',
    pipelineDocHash: '69922240' + '4'.repeat(24), keyedUnderEarlierRule: false,
    paramsHash: '3aba165d' + '5'.repeat(24), inputsHash: 'ff117bca' + '6'.repeat(56), paramTypes: {},
    experimentId: 'pl-1', runName: 'plot', nid: 'v1', user: '', favorite: false, pipelineId: 'pl-1',
    scriptPath: null, ...p,
  };
}

const REGIONS = { roi: Array.from({ length: 30 }, (_, i) => ({ name: `region_${i}`, x: 120 + i, y: 40 + i })) };
const RUNS = [
  run({ id: '24', method: '__demo__label', runName: 'label', outputs: { labeled: 'x' }, timestamp: T0 }),
  run({
    id: '29', runName: 'plot', timestamp: T0 + 100_000,
    inputs: { value_column: 'intensity', bins: 30, regions: REGIONS },
    paramTypes: { value_column: 'str', bins: 'int', regions: 'dict' },
    parentRunIds: ['24'], upstreamRunIds: ['24'],
    parents: [{ slot: 'data', sourceRunId: '24', sourceSlot: 'labeled', cacheKey: 'bff78b01' + '7'.repeat(56) }],
    sampleInputs: [{ slot: 'palette', sample: '__demo__treated_cells_palette', contentHash: '9e85bb61' + '8'.repeat(24) }],
  }),
];

async function routes(page: Page): Promise<void> {
  await page.route('**/api/wfc/runs', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RUNS) }));
  await page.route('**/api/wfc/run/*', async r => {
    const m = r.request().url().match(/\/api\/wfc\/run\/([^/?]+)(?:\?.*)?$/);
    if (!m) { await r.fallback(); return; }
    await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RUNS.find(x => x.id === m[1])) });
  });
  const fixed: Array<[string, unknown]> = [
    ['**/api/wfc/modules', ['__demo__']],
    ['**/api/wfc/methods', []],
    ['**/api/wfc/status', { loaded: true, path: '/x', modules: 1, runs: RUNS.length }],
    ['**/api/wfc/run/*/cancelled-descendants', []],
    ['**/api/wfc/run/*/artifacts', []],
    ['**/api/dev/status', { dev: false }],
    ['**/api/samples', []],
  ];
  for (const [url, body] of fixed) {
    await page.route(url, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }));
  }
}

test('the Cache-key inputs card: every hover, asserted and photographed', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.setViewportSize({ width: 1100, height: 1400 });
  await routes(page);
  await page.goto('/?fixture=history-descendants');
  await page.waitForSelector('.view-switcher', { timeout: 10_000 });
  await page.locator('.tree-card', { hasText: 'plot' }).first().click();
  const panel = page.locator('.detail-panel');
  const card = panel.getByTestId('cache-key-inputs');
  await card.waitFor();
  const tip = page.getByTestId('hash-tip');

  // The panel, widened left to take in a tooltip or popover that spills
  // over the run list.
  const shot = async (name: string, also = tip) => {
    if (!CAPTURE_GALLERY) return;
    await page.waitForTimeout(150);
    const p = (await panel.boundingBox())!;
    let x = p.x - 10;
    if (await also.count()) x = Math.min(x, (await also.boundingBox())!.x - 10);
    await page.screenshot({
      path: path.join(OUT, name),
      clip: { x, y: 0, width: p.x + p.width - x, height: 1400 },
    });
  };

  await shot('00-closed.png');
  await card.getByTestId('cache-key-help').click();
  const help = page.getByTestId('cache-key-help-popover');
  await expect(help).toContainText('cache key = hash(');
  await shot('01-help-popover.png', help);
  await page.keyboard.press('Escape');

  await card.getByTestId('cache-key-toggle').click();
  await card.getByTestId('cache-key-slots-toggle').click();
  await card.getByTestId('cache-key-params-toggle').click();
  await page.mouse.move(0, 0);
  await shot('02-opened.png');

  const tagOf = (kind: string) => card.locator(`.hsh:has([data-testid="hash-tag"][data-kind="${kind}"])`).first();
  const copyOf = (kind: string) => card.locator(`[data-testid="hash-copy"][data-kind="${kind}"]`).first();
  const hovers: Array<[string, () => Promise<void>, string | RegExp]> = [
    ['10-code-tag', () => tagOf('code').hover(), /^Code hash: the method's code files/],
    ['11-code-copy-icon', () => copyOf('code').hover(), 'Copy full hash'],
    ['12-code-copied', () => copyOf('code').click(), 'Copied'],
    ['13-env-tag', () => tagOf('env').hover(), /^Env hash: the contents of the env/],
    ['14-inputs-tag', () => tagOf('inputs').hover(), /^Inputs hash:/],
    ['15-params-tag', () => tagOf('params').hover(), /^Params hash:/],
    ['16-run-key-tag', () => tagOf('run-key').hover(), /^Run key: the cache key of the upstream run/],
    ['17-content-tag', () => tagOf('content').hover(), /^Content hash: the sample's content/],
    ['18-commit-tag', () => tagOf('commit').hover(), /^Commit: the git commit this run ran at/],
    ['19-commit-copy-icon', () => copyOf('commit').hover(), 'Copy full commit SHA'],
    ['20-pipeline-tag', () => tagOf('pipeline').hover(), /Click to open it in the Pipelines view\.$/],
    ['30-method-chip', () => card.getByTestId('cache-key-method-link').hover(),
      '__demo__.__demo__plot — open this version in the Registry'],
    ['31-header-method-chip', () => card.locator('.meta .chip').first().hover(), '__demo__.__demo__plot'],
    ['32-output-slot-chip', () => card.getByTestId('slot-output-link').hover(), 'labeled — open run #24 on its Artifacts'],
    ['33-run-chip', () => card.getByTestId('slot-run-link').hover(), 'Open run #24'],
    ['34-sample-chip', () => card.getByTestId('slot-sample-link').hover(),
      "__demo__treated_cells_palette — show the Registry's samples"],
    ['35-sample-copy-icon', () => card.getByTestId('slot-sample-copy').hover(), 'Copy sample name'],
    ['40-long-value-preview', () => card.getByTestId('param-long-toggle').hover(), /^Click to open the full value/],
  ];
  for (const [name, act, text] of hovers) {
    await page.mouse.move(0, 0);
    await act();
    await expect(tip).toHaveText(text);
    await shot(`${name}.png`);
  }
  // The code copy icon put the full hash on the clipboard; the tag's own
  // hover never shows it.
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(CODE_FP);

  await card.getByTestId('param-long-toggle').click();
  await card.getByTestId('param-json-copy').hover();
  await expect(tip).toHaveText('Copy value');
  await shot('41-long-value-open-copy-icon.png');
});
