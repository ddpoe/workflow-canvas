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
    { id: 'node_2', type: 'method', method: '__demo__preprocess', module: '__demo__cleanup', params: { drop_na: true, value_column: 'intensity' }, position: { x: 300, y: 220 } },
    { id: 'node_3', type: 'method', method: '__demo__filter_cells', module: '__demo__cleanup', params: { min_quality: 0.5 }, position: { x: 560, y: 220 } },
    { id: 'node_4', type: 'method', method: '__demo__label', module: '__demo__labeling', params: { threshold: 150, label_column: 'label' }, position: { x: 820, y: 220 } },
    { id: 'node_5', type: 'method', method: '__demo__summarize', module: '__demo__labeling', params: { group_by: 'label' }, position: { x: 1080, y: 100 } },
    { id: 'node_6', type: 'method', method: '__demo__plot', module: '__demo__plotting', params: { value_column: 'intensity', bins: 20, smoothing: 1.0 }, position: { x: 1080, y: 340 } },
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
// demo's namespacing. Each method is its newest version's contract plus
// `versions`, the one registered version carrying that same contract.
function registered(contract: Record<string, unknown>, identity: string) {
  const full = { ...contract, executor: 'python' };
  return {
    ...full,
    env: '__demo__',
    versions: [{
      label: 1,
      identity: identity.repeat(40),
      registered_at: '2026-10-01T09:00:00Z',
      env: '__demo__',
      newest: true,
      ...full,
    }],
  };
}

// `__demo__label`'s `labeled` output declares the sample columns it passes
// through and the column it adds, named by `label_column`.
const LABELED_OPTIONS = {
  fixed: ['id', 'intensity', 'area', 'quality'],
  from_params: [{ params: ['label_column'], pattern: '{}' }],
};

// What POST /api/contracts/__demo__labeling.__demo__label/output_options answers for
// the pipeline's `label_column: label`.
const LABELED_ANSWER = {
  fixed: ['id', 'intensity', 'area', 'quality'],
  built: [{ params: ['label_column'], names: ['label'] }],
  patterns: [],
  all: ['id', 'intensity', 'area', 'quality', 'label'],
};

// Three modules, as the scaffold registers them (DEMO_MODULES).
const MODULES = {
  __demo__cleanup: {
    description: 'Demo module',
    methods: {
      __demo__preprocess: registered({
        inputs: { data: { type: 'csv', required: true } },
        outputs: { clean: { type: 'csv' } },
        params_schema: { drop_na: { type: 'bool', default: true }, value_column: { type: 'str', default: 'intensity' } },
      }, '1'),
      __demo__filter_cells: registered({
        inputs: { data: { type: 'csv', required: true } },
        outputs: { filtered: { type: 'csv' } },
        params_schema: { min_quality: { type: 'float', required: true }, max_area: { type: 'float' } },
      }, '2'),
    },
  },
  __demo__labeling: {
    description: 'Demo module',
    methods: {
      __demo__label: registered({
        inputs: { data: { type: 'csv', required: true } },
        outputs: { labeled: { type: 'csv', options: LABELED_OPTIONS } },
        params_schema: { threshold: { type: 'float', required: true }, label_column: { type: 'str', default: 'label' } },
      }, '3'),
      __demo__summarize: registered({
        inputs: { data: { type: 'csv', required: true } },
        outputs: { summary: { type: 'csv' } },
        params_schema: { group_by: { type: 'str', default: 'label', options_from: 'data' } },
      }, '4'),
    },
  },
  __demo__plotting: {
    description: 'Demo module',
    methods: {
      __demo__plot: registered({
        inputs: { data: { type: 'csv', required: true } },
        outputs: { figure: { type: 'png' } },
        params_schema: { value_column: { type: 'str', default: 'intensity', options_from: 'data' }, bins: { type: 'int', default: 20 }, smoothing: { type: 'float', default: 1.0 } },
      }, '5'),
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
  await page.route('**/api/contracts/*/output_options', async (route) => {
    const method = decodeURIComponent(new URL(route.request().url()).pathname.split('/').slice(-2)[0]);
    const answer = method === '__demo__labeling.__demo__label'
      ? LABELED_ANSWER
      : { fixed: [], built: [], patterns: [], all: [] };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer) });
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

  test("group_by's picker lists the label column built from label_column beside the sample columns", async ({ page }) => {
    await setupDemoRoutes(page);
    await page.goto('/?pipeline=demo');
    const summarize = page.locator('.svelte-flow__node[data-id="node_5"]');
    await expect(summarize).toBeVisible({ timeout: 15_000 });
    await summarize.click();

    const block = page.locator('.inspector .param-block')
      .filter({ has: page.locator('.pname', { hasText: 'group_by' }) });
    await expect(block.getByTestId('options-picker')).toBeVisible();
    await block.getByTestId('options-chevron').click();
    const list = block.getByTestId('options-list');
    await expect(list).toBeVisible();
    await expect(list.getByTestId('options-section-head').locator('.chip')).toHaveText('__demo__label');
    await expect(list.locator('.grp')).toHaveText(['Fixed', /Built from\s*param\s*label_column ↗/]);
    await expect(block.getByTestId('options-item')).toHaveText(['id', 'intensity', 'area', 'quality', 'label']);
    await expect(list.locator('.opt.cur')).toHaveText('label');
  });

  test('a selected edge is near-white with the neutral glow; the others stay grey (case 20)', async ({ page }) => {
    await setupDemoRoutes(page);
    await page.goto('/?pipeline=demo');
    const edges = page.locator('.svelte-flow__edge');
    await expect(edges).toHaveCount(5, { timeout: 15_000 });

    const pathStyle = (i: number) => edges.nth(i).locator('.svelte-flow__edge-path').evaluate((el) => {
      const s = getComputedStyle(el);
      return { stroke: s.stroke, width: s.strokeWidth, filter: s.filter };
    });
    expect((await pathStyle(0)).stroke).toBe('rgb(85, 85, 85)');

    await edges.first().dispatchEvent('click');
    await expect(edges.first()).toHaveClass(/selected/);
    const sel = await pathStyle(0);
    expect(sel.stroke).toBe('rgb(224, 224, 224)');
    expect(sel.width).toBe('2px');
    expect(sel.filter).toContain('drop-shadow');
    expect(sel.filter).toContain('rgba(240, 240, 240, 0.34)');
    // An edge never takes a module, slot or status colour: the rest stay grey.
    expect((await pathStyle(1)).stroke).toBe('rgb(85, 85, 85)');
  });
});
