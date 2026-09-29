/**
 * Runs Preview lock flow in the browser: Lock All → the summary → statuses
 * shown (with a cached row's per-output lines) → an edit clears them → Run
 * with an unlocked row opens the same summary → confirm → the run is
 * submitted.
 *
 * Every backend route is intercepted. The cache-status answer is built from
 * the posted document, so the rows it names are the document's own targets.
 */
import { expect, test, type Page } from '@playwright/test';

const SAMPLES = ['ctrl_01', 'treat_01', 'treat_02'];
const PIPELINE = {
  name: 'lock-demo',
  nodes: [
    { id: 'node_1', type: 'input_selector', method: '', module: '', params: {}, samples: SAMPLES, source: 'registered', fan_mode: 'out', keep_going: true, position: { x: 40, y: 220 } },
    { id: 'node_2', type: 'method', method: 'preprocess', module: 'demo', params: { value_column: 'intensity' }, position: { x: 300, y: 220 } },
    { id: 'node_3', type: 'method', method: 'plot', module: 'demo', params: { bins: 20 }, position: { x: 560, y: 220 } },
  ],
  links: [
    { source: 'node_1', target: 'node_2', sourceHandle: 'output', targetHandle: 'data' },
    { source: 'node_2', target: 'node_3', sourceHandle: 'clean', targetHandle: 'data' },
  ],
  samples: SAMPLES,
};
const MODULES = {
  demo: {
    description: 'Demo module',
    methods: {
      preprocess: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { clean: { type: 'csv' }, report: { type: 'csv' } },
        params_schema: { value_column: { type: 'str', default: 'intensity' }, drop_na: { type: 'bool', default: true } },
      },
      plot: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { figure: { type: 'png' } },
        params_schema: { bins: { type: 'int', default: 20 } },
      },
    },
  },
};

interface Doc { nodes: { id: string; type?: string }[]; samples: string[] }

/** preprocess rows are cached locally (two outputs); plot rows are new. */
function cacheStatusFor(doc: Doc) {
  const rows = [];
  for (const n of doc.nodes.filter(n => n.type === 'method')) {
    for (const [i, s] of doc.samples.entries()) {
      const cached = n.id === 'node_2';
      rows.push({
        key: `${n.id}::${s}::default`, node_id: n.id, sample: s, variant: 'default',
        status: cached ? 'cached_local' : 'new_step_changed', reason: null, cache_key: null,
        source_run_id: cached ? 100 + i : null, source_nid: cached ? `preprocess_${i}` : null,
        outputs: cached ? [{ slot: 'clean', location: 'local' }, { slot: 'report', location: 'local' }] : [],
      });
    }
  }
  return { rows, blocked_reason: null };
}

async function setupRoutes(page: Page, posted: string[]): Promise<void> {
  // Registered first so the specific routes below take precedence.
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'POST') posted.push(new URL(route.request().url()).pathname);
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/pipelines/demo', r => r.fulfill(json(PIPELINE)));
  await page.route('**/api/modules', r => r.fulfill(json(MODULES)));
  await page.route('**/api/samples', r => r.fulfill(json(SAMPLES)));
  await page.route('**/api/wfc/samples', r => r.fulfill(json([])));
  await page.route('**/api/dev/status', r => r.fulfill(json({ dev: false })));
  await page.route('**/api/workflow/validate', r => r.fulfill(json({ valid: true, errors: [] })));
  await page.route('**/api/workflow/run', async r => {
    posted.push('/api/workflow/run');
    await r.fulfill(json({ job_id: 'job-1', pipeline_id: 'job-1' }));
  });
  await page.route('**/api/wfc/cache-status', async r => {
    posted.push('/api/wfc/cache-status');
    await r.fulfill(json(cacheStatusFor(r.request().postDataJSON() as Doc)));
  });
}

test('clicking between two nodes of one method leaves nothing that Lock All cannot lock', async ({ page }) => {
  const posted: string[] = [];
  await setupRoutes(page, posted);
  // Registered after setupRoutes, so it answers the demo route instead.
  const twin = {
    ...PIPELINE,
    nodes: [
      PIPELINE.nodes[0],
      { ...PIPELINE.nodes[1], id: 'node_a', position: { x: 300, y: 80 } },
      { ...PIPELINE.nodes[1], id: 'node_b', position: { x: 300, y: 380 } },
    ],
    links: [
      { source: 'node_1', target: 'node_a', sourceHandle: 'output', targetHandle: 'data' },
      { source: 'node_1', target: 'node_b', sourceHandle: 'output', targetHandle: 'data' },
    ],
  };
  await page.route('**/api/pipelines/demo', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(twin) }));
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(3, { timeout: 15_000 });

  for (const id of ['node_a', 'node_b', 'node_a']) {
    await page.locator(`.svelte-flow__node[data-id="${id}"]`).click();
    await expect(page.locator('.inspector')).toContainText('value_column');
  }

  const preview = page.locator('.runs-preview-panel');
  await preview.getByTestId('preview-lock-all').click();
  const summary = page.getByTestId('lock-summary');
  await expect(summary).toBeVisible();
  await summary.getByTestId('lock-summary-confirm').click();
  await expect(summary).toBeHidden();
  await expect(page.getByTestId('lock-summary-failed')).toHaveCount(0);
  expect(posted).toContain('/api/wfc/cache-status');
});

test('Lock All shows statuses, an edit clears them, Run with an unlocked row confirms first', async ({ page }) => {
  const posted: string[] = [];
  await setupRoutes(page, posted);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(3, { timeout: 15_000 });

  // Counts only before any lock.
  const preview = page.locator('.runs-preview-panel');
  await expect(preview.locator('.tally-unlocked')).toContainText('6 runs');
  await expect(preview.locator('tr.summary-row[data-method="node_2"] .col-cached-local')).toHaveText('—');

  // Lock All opens the summary: the never-touched drop_na runs its default.
  await preview.getByTestId('preview-lock-all').click();
  const summary = page.getByTestId('lock-summary');
  await expect(summary).toBeVisible();
  await expect(summary.getByTestId('lock-summary-default').filter({ hasText: 'drop_na' })).toContainText('true');
  await summary.getByTestId('lock-summary-confirm').click();
  await expect(summary).toBeHidden();

  // Statuses for the locked state.
  await expect(preview.locator('tr.summary-row[data-method="node_2"] .col-cached-local')).toHaveText('3');
  await expect(preview.locator('tr.summary-row[data-method="node_3"] .col-new')).toHaveText('3');
  expect(posted.filter(p => p === '/api/wfc/cache-status')).toHaveLength(1);

  // Expand a cached row: one line per output, with where it is and the action.
  await preview.locator('tr.summary-row[data-method="node_2"]').click();
  const first = preview.locator('tr.run-row').first();
  await first.locator('.btn-chevron').click();
  const lines = preview.locator('tr.output-line');
  await expect(lines).toHaveCount(2);
  await expect(lines.first()).toContainText('clean');
  await expect(lines.first().locator('.out-location')).toHaveText('local');
  await expect(lines.first().locator('.out-action')).toHaveText('read here');

  // An edit: open the plot node and type a new bins value without committing.
  await page.locator('.svelte-flow__node', { hasText: 'plot' }).first().click();
  const bins = page.locator('.inspector input[type="number"]').first();
  await bins.fill('30');
  await expect(preview.locator('.tally-unlocked')).toContainText('statuses appear after Lock All');

  // Run with that row unlocked opens the same summary; nothing runs yet.
  await page.locator('.btn-run').click();
  await expect(summary).toBeVisible();
  await expect(summary.getByTestId('lock-summary-unlocked')).toContainText('plot · bins');
  await expect(summary.getByTestId('lock-summary-confirm')).toHaveText('Lock and run');
  const beforeConfirm = posted.length;
  await summary.getByTestId('lock-summary-confirm').click();
  await expect(summary).toBeHidden();
  await expect.poll(() => posted.slice(beforeConfirm).some(p => p.includes('/run'))).toBe(true);
  expect(posted.slice(beforeConfirm)).not.toContain('/api/wfc/cache-status');
});
