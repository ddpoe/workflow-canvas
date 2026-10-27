/**
 * Pinning a node to an older method version and running it, in the browser.
 *
 * The method has two registered versions. Choosing v1 in the Inspector's
 * version dropdown pins the node; the run the canvas submits names that
 * version by its content identity, and the node shows the version it runs.
 *
 * Every backend route is intercepted (the project's e2e model): the run
 * route records the document it receives.
 */
import { expect, test, type Page } from '@playwright/test';

const V1 = '1'.repeat(40);
const V2 = '2'.repeat(40);
const SAMPLES = ['ctrl_01'];
const PIPELINE = {
  name: 'pin-demo',
  nodes: [
    { id: 'node_1', type: 'input_selector', method: '', module: '', params: {}, samples: SAMPLES, source: 'registered', fan_mode: 'out', keep_going: true, position: { x: 40, y: 220 } },
    { id: 'node_2', type: 'method', method: 'preprocess', module: 'demo', params: { value_column: 'intensity' }, position: { x: 300, y: 220 } },
  ],
  links: [
    { source: 'node_1', target: 'node_2', sourceHandle: 'output', targetHandle: 'data' },
  ],
  samples: SAMPLES,
};
const CONTRACT = {
  inputs: { data: { type: 'csv', required: true } },
  outputs: { clean: { type: 'csv' } },
  params_schema: { value_column: { type: 'str', default: 'intensity' } },
  executor: 'python',
};
const MODULES = {
  demo: {
    description: 'Demo module',
    methods: {
      preprocess: {
        ...CONTRACT,
        env: 'demo',
        versions: [
          { label: 1, identity: V1, registered_at: '2026-10-01T09:00:00Z', env: 'demo', newest: false, ...CONTRACT },
          { label: 2, identity: V2, registered_at: '2026-10-08T09:00:00Z', env: 'demo', newest: true, ...CONTRACT },
        ],
      },
    },
  },
};

interface Doc { nodes: { id: string; type?: string }[]; samples: string[] }

function cacheStatusFor(doc: Doc) {
  const rows = [];
  for (const n of doc.nodes.filter(n => n.type === 'method')) {
    for (const s of doc.samples) {
      rows.push({
        key: `${n.id}::${s}::default`, node_id: n.id, sample: s, variant: 'default',
        status: 'new_step_changed', reason: null, cache_key: null,
        source_run_id: null, source_nid: null, outputs: [],
      });
    }
  }
  return { rows, blocked_reason: null };
}

async function setupRoutes(page: Page, runBodies: unknown[]): Promise<void> {
  // Registered first so the specific routes below take precedence.
  await page.route('**/api/**', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/pipelines/demo', r => r.fulfill(json(PIPELINE)));
  await page.route('**/api/modules', r => r.fulfill(json(MODULES)));
  await page.route('**/api/samples', r => r.fulfill(json(SAMPLES)));
  await page.route('**/api/wfc/samples', r => r.fulfill(json([])));
  await page.route('**/api/dev/status', r => r.fulfill(json({ dev: false })));
  await page.route('**/api/workflow/validate', r => r.fulfill(json({ valid: true, errors: [] })));
  await page.route('**/api/wfc/cache-status', r => r.fulfill(json(cacheStatusFor(r.request().postDataJSON() as Doc))));
  await page.route('**/api/workflow/run', async r => {
    runBodies.push(r.request().postDataJSON());
    await r.fulfill(json({ job_id: 'job-1', pipeline_id: 'job-1' }));
  });
}

test('a node pinned to an older version runs that version', async ({ page }) => {
  const runBodies: unknown[] = [];
  await setupRoutes(page, runBodies);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(2, { timeout: 15_000 });

  // A node that follows the newest runs v2.
  const node = page.locator('.svelte-flow__node[data-id="node_2"]');
  await node.click();
  const dropdown = page.locator('.inspector .inspector-version');
  await expect(dropdown).toHaveValue('2');

  // Choose v1: the node is pinned to it.
  await dropdown.selectOption('1');
  await expect(dropdown).toHaveValue('1');
  await expect(node).toContainText('v1');

  // Run: the unlocked row confirms first, then the run is submitted.
  await page.locator('.btn-run').click();
  const summary = page.getByTestId('lock-summary');
  await expect(summary).toBeVisible();
  await summary.getByTestId('lock-summary-confirm').click();
  await expect.poll(() => runBodies.length).toBeGreaterThan(0);

  // The submitted document names v1 by its identity on that node.
  const body = JSON.stringify(runBodies[0]);
  expect(body).toContain(`"version":"${V1}"`);
  expect(body).not.toContain(V2);
});
