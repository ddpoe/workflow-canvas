/**
 * Browser smoke (a) for the wfc demo cycle: `?pipeline=demo` paints the
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
const DEMO_PIPELINE = {
  name: 'demo',
  nodes: [
    { id: 'node_1', type: 'input_selector', method: '', module: '', params: {}, samples: ['__demo__ctrl_01', '__demo__treat_01', '__demo__treat_02'], source: 'registered', fan_mode: 'out', keep_going: true, position: { x: 40, y: 220 } },
    { id: 'node_2', type: 'method', method: 'preprocess', module: '__demo__', params: { drop_na: true, value_column: 'intensity' }, position: { x: 300, y: 220 } },
    { id: 'node_3', type: 'method', method: 'filter_cells', module: '__demo__', params: { min_quality: 0.5 }, position: { x: 560, y: 220 } },
    { id: 'node_4', type: 'method', method: 'label', module: '__demo__', params: { threshold: 150, label_column: 'label' }, position: { x: 820, y: 220 } },
    { id: 'node_5', type: 'method', method: 'summarize', module: '__demo__', params: { group_by: 'label' }, position: { x: 1080, y: 100 } },
    { id: 'node_6', type: 'method', method: 'plot', module: '__demo__', params: { value_column: 'intensity', bins: 20 }, position: { x: 1080, y: 340 } },
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
const MODULES = {
  __demo__: {
    description: 'Demo module',
    methods: {
      preprocess: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { clean: { type: 'csv' } },
        params_schema: { drop_na: { type: 'bool', default: true }, value_column: { type: 'str', default: 'intensity' } },
      },
      filter_cells: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { filtered: { type: 'csv' } },
        params_schema: { min_quality: { type: 'float', required: true }, max_area: { type: 'float' } },
      },
      label: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { labeled: { type: 'csv' } },
        params_schema: { threshold: { type: 'float', required: true }, label_column: { type: 'str', default: 'label' } },
      },
      summarize: {
        inputs: { data: { type: 'csv', required: true } },
        outputs: { summary: { type: 'csv' } },
        params_schema: { group_by: { type: 'str', default: 'label' } },
      },
      plot: {
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
    for (const label of ['preprocess', 'filter_cells', 'label', 'summarize', 'plot']) {
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
