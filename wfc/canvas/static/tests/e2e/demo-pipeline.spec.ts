/**
 * Browser smoke for the wfc demo: `?pipeline=demo` paints the
 * five-method demo pipeline with METHOD-SPECIFIC slots.
 *
 * The load path under test is App.svelte's demo branch: fetch
 * GET /api/pipelines/demo, wait for the modules store (Sidebar's
 * /api/modules fetch), then loadPipeline(). If loadPipeline were bypassed
 * (store-poking) or raced ahead of the registry, every method node would
 * fall back to the generic `data`/`output` CSV pair — so the assertions
 * target the real slot handle ids (clean/filtered/labeled) and the
 * label → {summarize, plot} branch.
 */
import { expect, test, type Page } from '@playwright/test';

// Mirror of wfc/demo/assets/pipeline.json (inline: specs cannot read
// package assets from the Vite server).
//
// The scaffold registers every demo entity under the reserved `__demo__`
// prefix. It rewrites the five assets/methods/<name>/ directory names as it
// registers them, but it copies THIS asset verbatim (shutil.copy2), so the
// file on disk already carries the prefixed names. Mirror what the demo
// *registers* — wfc/demo/scaffold.py::DEMO_METHODS — not what the asset
// file says, or these assertions go green against a rendering the product
// no longer produces. tests/test_demo_spec_mirror.py gates both this mirror
// and the asset against that one oracle.
const DEMO_PIPELINE = {
  name: 'demo',
  nodes: [
    { id: 'node_1', type: 'input_selector', method: '', module: '', params: {}, samples: ['__demo__ctrl_01', '__demo__treat_01', '__demo__treat_02'], source: 'registered', fan_mode: 'out', keep_going: true, position: { x: 40, y: 220 } },
    { id: 'node_2', type: 'method', method: '__demo__preprocess', module: '__demo__', params: { drop_na: true, value_column: 'intensity' }, position: { x: 300, y: 220 } },
    { id: 'node_3', type: 'method', method: '__demo__filter_cells', module: '__demo__', params: { min_quality: 0.5 }, position: { x: 560, y: 220 } },
    { id: 'node_4', type: 'method', method: '__demo__label', module: '__demo__', params: { threshold: 150, label_column: 'label' }, position: { x: 820, y: 220 } },
    { id: 'node_5', type: 'method', method: '__demo__summarize', module: '__demo__', params: { group_by: 'label' }, position: { x: 1080, y: 100 } },
    { id: 'node_6', type: 'method', method: '__demo__plot', module: '__demo__', params: { value_column: 'intensity', bins: 20 }, position: { x: 1080, y: 340 } },
  ],
  links: [
    { source: 'node_1', target: 'node_2', sourceHandle: 'output', targetHandle: 'data' },
    { source: 'node_2', target: 'node_3', sourceHandle: 'clean', targetHandle: 'data' },
    { source: 'node_3', target: 'node_4', sourceHandle: 'filtered', targetHandle: 'data' },
    { source: 'node_4', target: 'node_5', sourceHandle: 'labeled', targetHandle: 'data' },
    { source: 'node_4', target: 'node_6', sourceHandle: 'labeled', targetHandle: 'data' },
  ],
  samples: ['__demo__ctrl_01', '__demo__treat_01', '__demo__treat_02'],
};

// /api/modules raw shape (Sidebar.svelte transforms it into ModuleDef[]).
// The method KEYS carry the `__demo__` prefix — they are the registered
// names `loadPipeline` looks a node's `method` up by. The slot names inside
// each method do NOT: slots are the method's contract, untouched by the
// demo's namespacing.
const MODULES = {
  __demo__: {
    description: 'Demo module',
    methods: {
      __demo__preprocess: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { clean: { type: 'csv' } },
        params_schema: { drop_na: { type: 'bool', default: true }, value_column: { type: 'str', default: 'intensity' } },
      },
      __demo__filter_cells: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { filtered: { type: 'csv' } },
        params_schema: { min_quality: { type: 'float', required: true }, max_area: { type: 'float' } },
      },
      __demo__label: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { labeled: { type: 'csv' } },
        params_schema: { threshold: { type: 'float', required: true }, label_column: { type: 'str', default: 'label' } },
      },
      __demo__summarize: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { summary: { type: 'csv' } },
        params_schema: { group_by: { type: 'str', default: 'label' } },
      },
      __demo__plot: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { figure: { type: 'png' } },
        params_schema: { value_column: { type: 'str', default: 'intensity' }, bins: { type: 'int', default: 20 } },
      },
    },
  },
};

async function setupDemoRoutes(page: Page): Promise<void> {
  await page.route('**/api/pipelines/demo', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DEMO_PIPELINE) });
  });
  await page.route('**/api/modules', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MODULES) });
  });
  await page.route('**/api/samples', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DEMO_PIPELINE.samples) });
  });
  await page.route('**/api/dev/status', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ dev: false }) });
  });
}

test.describe('?pipeline=demo pre-wiring (smoke)', () => {
  test('paints five connected method nodes with real slots incl. the label branch', async ({ page }) => {
    await setupDemoRoutes(page);
    await page.goto('/?pipeline=demo');

    // Six nodes total: input_selector + five methods.
    await expect(page.locator('.svelte-flow__node')).toHaveCount(6, { timeout: 15_000 });
    // `loadPipeline` labels a method node after the document's `method`, and
    // CustomNode renders that label verbatim — so the prefix is visible on
    // the card. Asserting the bare names would still pass by substring, and
    // would not notice the demo dropping the prefix.
    for (const label of [
      '__demo__preprocess', '__demo__filter_cells', '__demo__label',
      '__demo__summarize', '__demo__plot',
    ]) {
      await expect(page.locator('.svelte-flow__node', { hasText: label }).first()).toBeVisible();
    }

    // Method-specific slot handles resolved from the registry — the generic
    // fallback would render `output` handles instead of these.
    for (const slot of ['clean', 'filtered', 'labeled', 'summary', 'figure']) {
      await expect(page.locator(`[data-handleid="${slot}"]`).first()).toBeVisible();
    }

    // All five edges painted, including the branch: label feeds BOTH
    // summarize and plot from its `labeled` slot.
    await expect(page.locator('.svelte-flow__edge')).toHaveCount(5);
  });
});
