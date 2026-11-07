/**
 * A draft on the Builder canvas, in the browser.
 *
 * The module has a registered method `label` with a draft, and `plot`
 * downstream of it. The demo document runs `label`'s draft. The canvas
 * shows draft mode (the pill, the dashed borders, the solid and outline
 * DRAFT MODE flags), lists the draft in the palette's Drafts group, and the
 * Runs Preview marks the draft row and counts every draft-mode step as new,
 * as the server reports it. When the draft's method.yaml changed since it
 * was read, the node takes the new contract from the palette and Run posts
 * the document with no Update. A refusal the server gives naming a node and
 * a field (a param the contract no longer declares, a required input nothing
 * is connected to) shows on that node's footer and in the Inspector. With no
 * draft on the canvas, a refused node draws a solid red border; an invalid
 * draft says so in its footer; and Run with required params left blank lists
 * each one in the error banner.
 *
 * Every backend route is intercepted (the project's e2e model).
 */
import { expect, test, type Page } from '@playwright/test';

const V1 = '1'.repeat(40);
const SAMPLES = ['ctrl_01', 'treat_01'];
const PIPELINE = {
  name: 'draft-demo',
  nodes: [
    { id: 'node_1', type: 'input_selector', method: '', module: '', params: {}, samples: SAMPLES, source: 'registered', fan_mode: 'out', keep_going: true, position: { x: 40, y: 220 } },
    { id: 'node_2', type: 'method', method: 'label', module: 'demo', version: 'draft', folder: 'methods/label', params: {}, position: { x: 300, y: 220 } },
    { id: 'node_3', type: 'method', method: 'plot', module: 'demo', params: {}, position: { x: 560, y: 220 } },
  ],
  links: [
    { source: 'node_1', target: 'node_2', sourceHandle: 'output', targetHandle: 'data' },
    { source: 'node_2', target: 'node_3', sourceHandle: 'out', targetHandle: 'data' },
  ],
  samples: SAMPLES,
};
const CONTRACT = {
  inputs: { data: { type: 'csv', required: true } },
  outputs: { out: { type: 'csv' } },
  params_schema: {},
  executor: 'python',
};
function registered(env: string) {
  return {
    ...CONTRACT, env,
    versions: [{ label: 1, identity: V1, registered_at: '2026-10-01T09:00:00Z', env, newest: true, ...CONTRACT }],
  };
}
const MODULES = {
  demo: {
    description: 'Demo module',
    methods: { label: registered('demo'), plot: registered('demo') },
    drafts: {
      label: { kind: 'version', folder: 'methods/label', valid: true, reasons: [], env: 'demo', ...CONTRACT },
      qc_report: { kind: 'method', folder: 'methods/qc_report', valid: false, reasons: ['env wfc-qc-env is not registered'], env: 'wfc-qc-env', ...CONTRACT },
    },
  },
};

interface Doc { nodes: { id: string; type?: string }[]; samples: string[] }

/**
 * The rows the server sends for draft-mode steps (`_draft_mode_row`): new,
 * with no key and nothing looked up. The draft's own step is "this step
 * changed"; the step after it, under a new upstream, "upstream re-runs".
 */
function cacheStatusFor(doc: Doc) {
  const rows = [];
  for (const n of doc.nodes.filter(n => n.type === 'method')) {
    for (const s of doc.samples) {
      rows.push({
        key: `${n.id}::${s}::default`, node_id: n.id, sample: s, variant: 'default',
        status: n.id === 'node_2' ? 'new_step_changed' : 'new_upstream_reruns',
        reason: null, cache_key: null, draft_mode: true,
        source_run_id: null, source_nid: null, outputs: [],
      });
    }
  }
  return { rows, blocked_reason: null };
}


/** Keep a screenshot for review when `WFC_SHOTS_DIR` names a folder. */
async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.WFC_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

/** What the mocked server answers; a test may change it mid-test. */
interface Server { contractChanged?: boolean; modules?: unknown; validate?: unknown; pipeline?: unknown }

async function setupRoutes(page: Page, runBodies: unknown[], server: Server = {}): Promise<void> {
  // Registered first so the specific routes below take precedence.
  await page.route('**/api/**', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/pipelines/demo', r => r.fulfill(json(server.pipeline ?? PIPELINE)));
  await page.route('**/api/modules', r => r.fulfill(json(server.modules ?? MODULES)));
  await page.route('**/api/samples', r => r.fulfill(json(SAMPLES)));
  await page.route('**/api/wfc/samples', r => r.fulfill(json([])));
  await page.route('**/api/dev/status', r => r.fulfill(json({ dev: false })));
  await page.route('**/api/workflow/validate', r => r.fulfill(json(server.validate ?? { valid: true, errors: [], draftRefusals: [] })));
  await page.route('**/api/wfc/cache-status', r => r.fulfill(json(cacheStatusFor(r.request().postDataJSON() as Doc))));
  await page.route('**/api/registry/drafts/snapshot-check', r => r.fulfill(json({
    drafts: [{ module: 'demo', method: 'label', state: 'current', contractChanged: !!server.contractChanged, filesUpdated: false, message: null }],
  })));
  await page.route('**/api/workflow/run', async r => {
    runBodies.push(r.request().postDataJSON());
    await r.fulfill(json({ job_id: 'job-1', pipeline_id: 'job-1' }));
  });
}

test('a draft node and the step after it run in draft mode, and the Runs Preview counts them as new', async ({ page }) => {
  await setupRoutes(page, []);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(3, { timeout: 15_000 });

  // The palette lists both drafts at the bottom, the invalid one saying why.
  const palette = page.getByTestId('palette-drafts');
  await expect(palette.getByTestId('palette-draft')).toHaveCount(2);
  await expect(palette.locator('[data-method="qc_report"] .inv')).toHaveAttribute('title', 'env wfc-qc-env is not registered');

  // The canvas is in draft mode; the draft and the step after it are dashed.
  await expect(page.getByTestId('draft-mode-pill')).toContainText('Review in Registry ↗');
  const draft = page.locator('.svelte-flow__node[data-id="node_2"] .custom-node');
  const down = page.locator('.svelte-flow__node[data-id="node_3"] .custom-node');
  await expect(draft).toHaveClass(/dash-2/);
  await expect(down).toHaveClass(/dash-2/);
  // The dashes are the one mask (app.css --dash-2), not a CSS dashed border.
  for (const node of [draft, down]) {
    const mask = await node.evaluate(el => {
      const before = getComputedStyle(el, '::before');
      return before.maskImage || before.getPropertyValue('-webkit-mask-image');
    });
    expect(mask).toContain('data:image/svg+xml');
    expect(await node.evaluate(el => getComputedStyle(el).borderTopStyle)).not.toBe('dashed');
  }
  await expect(draft.getByTestId('draft-flag')).toHaveClass(/strong/);
  await expect(down.getByTestId('draft-flag')).not.toHaveClass(/strong/);
  await expect(draft).toContainText('● DRAFT');

  // The Inspector offers the versions, a divider, then ● DRAFT, and the banner.
  await page.locator('.svelte-flow__node[data-id="node_2"]').click();
  await expect(page.locator('.inspector .inspector-version')).toHaveValue('draft');
  await expect(page.getByTestId('draft-banner')).toContainText('this step and the 1 after it');

  // Runs Preview: the draft row has the pill; draft-mode rows count as new.
  const row = (id: string) => page.locator(`.summary-row[data-method="${id}"]`);
  await expect(row('node_2').getByTestId('draft-pill')).toHaveText(/draft/i);
  await expect(row('node_3').getByTestId('draft-pill')).toHaveCount(0);
  // Statuses show for locked rows: Lock All, confirming the summary.
  await page.locator('.runs-preview-panel').getByTestId('preview-lock-all').click();
  const summary = page.getByTestId('lock-summary');
  await expect(summary).toBeVisible();
  await summary.getByTestId('lock-summary-confirm').click();
  await expect(summary).toBeHidden();
  for (const id of ['node_2', 'node_3']) {
    await expect(row(id).locator('.col-new')).toHaveText('2');
    await expect(row(id).locator('.col-cached-local')).not.toHaveText('2');
  }
});

async function clickRun(page: Page): Promise<void> {
  await page.locator('.btn-run').click();
  const summary = page.getByTestId('lock-summary');
  if (await summary.isVisible()) await summary.getByTestId('lock-summary-confirm').click();
}

test('a draft whose method.yaml changed runs without Update, its node showing the new contract', async ({ page }) => {
  const runBodies: unknown[] = [];
  const server: Server = {};
  await setupRoutes(page, runBodies, server);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(3, { timeout: 15_000 });
  const draft = page.locator('.svelte-flow__node[data-id="node_2"] .custom-node');
  await expect(draft).not.toContainText('mask');

  // method.yaml now declares a `mask` output: the palette reads it now.
  const label = MODULES.demo.drafts.label;
  server.contractChanged = true;
  server.modules = { demo: { ...MODULES.demo, drafts: { ...MODULES.demo.drafts,
    label: { ...label, outputs: { ...label.outputs, mask: { type: 'png' } } } } } };
  await clickRun(page);
  await expect.poll(() => runBodies.length).toBe(1);
  await expect(draft).toContainText('mask');
  await expect(page.getByTestId('pipeline-error-banner')).toHaveCount(0);
  await shot(page, 'task-11-1-changed-method-yaml-runs');
});

test('a mismatched param and an unwired input show on their node; the run is refused naming them', async ({ page }) => {
  const runBodies: unknown[] = [];
  const param = "Node 'node_2' (demo.label draft): parameter 'label_column' is not declared by draft; remove it";
  const unwired = "Node 'node_2': demo.label declares input 'mask', which nothing is connected to (its inputs: data, mask). "
    + "Connect a link to 'mask', or declare it `required: false` in the method's method.yaml.";
  const server: Server = { validate: { valid: false, errors: [param, unwired], draftRefusals: [] } };
  await setupRoutes(page, runBodies, server);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(3, { timeout: 15_000 });
  const draftNode = page.locator('.svelte-flow__node[data-id="node_2"] .custom-node');
  const downNode = page.locator('.svelte-flow__node[data-id="node_3"] .custom-node');
  const dashColor = (node: typeof draftNode) => node.evaluate(el => (el as HTMLElement).style.getPropertyValue('--dash-color'));

  // Run: the server refuses before anything is launched, naming the node and the field.
  // The banner lists Run's refusals under its header, one bullet each, as Validate's.
  await clickRun(page);
  const runBanner = page.getByTestId('pipeline-error-banner');
  await expect(runBanner.locator('.pe-head')).toHaveText('Cannot run — validation errors:');
  await expect(runBanner.locator('li')).toHaveText([param, unwired]);
  expect(runBodies).toHaveLength(0);
  // The node's footer says it is refused, with each refusal on hover; the title row holds the name only.
  const refused = draftNode.locator('.node-footer').getByTestId('node-refused');
  await expect(refused).toHaveText('⚠ refused · 2 issues');
  await expect(refused).toHaveAttribute('title', `${param}\n${unwired}`);
  await expect(draftNode.locator('.node-header [data-testid="node-refused"]')).toHaveCount(0);
  await expect(page.locator('.svelte-flow__node[data-id="node_3"]').getByTestId('node-refused')).toHaveCount(0);
  // The refused draft dashes in the failed colour; the step after it keeps its neutral dash.
  await expect.poll(() => dashColor(draftNode)).toBe('var(--status-failed)');
  expect(await dashColor(downNode)).toBe('var(--dash-neutral)');
  // The DRAFT MODE flag sits clear of the title row.
  const flag = (await draftNode.getByTestId('draft-flag').boundingBox())!;
  const title = (await draftNode.locator('.node-title').boundingBox())!;
  expect(flag.y + flag.height).toBeLessThanOrEqual(title.y);
  await shot(page, 'b-refused-draft-node');

  // The Inspector lists them for the user to fix; Validate gives the same verdict.
  await page.locator('.svelte-flow__node[data-id="node_2"]').click();
  const box = page.getByTestId('inspector-refusals');
  await expect(box.locator('li')).toHaveText([param, unwired]);
  await page.getByRole('button', { name: 'Validate' }).click();
  await expect(refused).toBeVisible();
  await expect(box.locator('li')).toHaveCount(2);
  // The banner lists Validate's errors under its header, one bullet each.
  const banner = page.getByTestId('pipeline-error-banner');
  await expect(banner.locator('.pe-head')).toHaveText('Validation errors:');
  await expect(banner.locator('li')).toHaveText([param, unwired]);
  await shot(page, 'e-banner-validate-refusal-bullets');

  // Fixed: the next Validate clears the refusal, its footer status and the red dash.
  server.validate = { valid: true, errors: [], draftRefusals: [] };
  await page.getByRole('button', { name: 'Validate' }).click();
  await expect(refused).toHaveCount(0);
  await expect.poll(() => dashColor(draftNode)).toBe('var(--dash-neutral)');
});

/** The same pipeline with no draft: `label` runs its registered version. */
const REGISTERED_PIPELINE = {
  ...PIPELINE,
  nodes: PIPELINE.nodes.map(n => (n.id === 'node_2'
    ? { id: 'node_2', type: 'method', method: 'label', module: 'demo', params: {}, position: n.position }
    : n)),
};

test('a refused node with no draft draws a solid red border and says refused in its footer', async ({ page }) => {
  const error = "Node 'node_3' (demo.plot): parameter 'dpi' is not declared by version 1; remove it";
  const server: Server = { pipeline: REGISTERED_PIPELINE, validate: { valid: false, errors: [error], draftRefusals: [] } };
  await setupRoutes(page, [], server);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(3, { timeout: 15_000 });
  await expect(page.getByTestId('draft-mode-pill')).toHaveCount(0);

  await page.getByRole('button', { name: 'Validate' }).click();
  const node = (id: string) => page.locator(`.svelte-flow__node[data-id="${id}"] .custom-node`);
  const refused = node('node_3').locator('.node-footer').getByTestId('node-refused');
  await expect(refused).toHaveText('⚠ refused · 1 issue');
  await expect(refused).toHaveAttribute('title', error);
  // The refused node's border is solid, 2px, in the failed colour; the node before it keeps its idle border.
  await expect(node('node_3')).toHaveAttribute('style', /border: 2px solid var\(--status-failed\)/);
  expect(await node('node_3').evaluate(el => getComputedStyle(el).borderTopStyle)).toBe('solid');
  await expect(node('node_2')).not.toHaveAttribute('style', /--status-failed/);
  await shot(page, 'a-refused-regular-node');
});

test('an invalid draft node says invalid in its footer, the reason on hover', async ({ page }) => {
  const label = { ...MODULES.demo.drafts.label, valid: false, reasons: ['env wfc-label-env is not registered'] };
  const server: Server = { modules: { demo: { ...MODULES.demo, drafts: { ...MODULES.demo.drafts, label } } } };
  await setupRoutes(page, [], server);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(3, { timeout: 15_000 });

  const draftNode = page.locator('.svelte-flow__node[data-id="node_2"] .custom-node');
  const invalid = draftNode.locator('.node-footer').getByTestId('draft-invalid');
  await expect(invalid).toHaveText('⚠ invalid');
  await expect(invalid).toHaveAttribute('title', 'label: env wfc-label-env is not registered');
  await expect(draftNode.locator('.node-header [data-testid="draft-invalid"]')).toHaveCount(0);
  await shot(page, 'c-invalid-draft-node');
});

test('Run with required params left blank lists each one in the banner and sends nothing', async ({ page }) => {
  const runBodies: unknown[] = [];
  const plot = { ...registered('demo'), params_schema: {
    threshold: { type: 'float', required: true },
    sigma: { type: 'float', required: true },
  } };
  const server: Server = {
    pipeline: REGISTERED_PIPELINE,
    modules: { demo: { ...MODULES.demo, methods: { ...MODULES.demo.methods, plot }, drafts: {} } },
  };
  await setupRoutes(page, runBodies, server);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(3, { timeout: 15_000 });

  await clickRun(page);
  const banner = page.getByTestId('pipeline-error-banner');
  await expect(banner.locator('.pe-head')).toHaveText('Cannot run — required params missing:');
  await expect(banner.locator('li')).toHaveText(['plot.threshold is required', 'plot.sigma is required']);
  await expect(banner).toContainText('Fill in the listed params');
  expect(runBodies).toHaveLength(0);
  await shot(page, 'f-banner-required-params-bullets');
});
