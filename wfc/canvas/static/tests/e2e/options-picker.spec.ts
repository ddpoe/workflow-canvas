/**
 * The options picker in the browser, state by state, through route replay.
 *
 * `/api/modules` declares a `plot_channel` method whose `channel_col` param
 * takes `options_from: data`, and a `join_cells` method whose `join_key`
 * takes `options_from: [cells, annotations]`. `POST .../output_options`
 * answers per upstream method (or per run, for a Run Reference). Each test
 * loads a small pipeline, opens the downstream node's Inspector and drives
 * the picker: open, filtered, No match, pattern fill, pattern tick, no
 * tick, the keyboard, locked, nothing wired, no options, several inputs
 * folded, a Run Reference upstream, and Lock and Run with a value that is
 * not among the options.
 *
 * Each state's PNG goes to `gallery/options-picker/<state>.png`, written
 * only when the gallery is being regenerated (`CAPTURE_GALLERY`, see
 * playwright.config.ts); the assertions run on every run.
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import { CAPTURE_GALLERY } from '../../playwright.config';

test.describe.configure({ mode: 'serial' });

const SAMPLES = ['ctrl_01', 'treat_01'];

/** A method as `/api/modules` sends it: its newest (and only) version's contract plus `versions`. */
function registered(contract: Record<string, unknown>, identity: string) {
  const full = { ...contract, executor: 'python' };
  return {
    ...full,
    env: 'demo',
    versions: [{
      label: 1, identity: identity.repeat(40), registered_at: '2026-10-01T09:00:00Z',
      env: 'demo', newest: true, ...full,
    }],
  };
}

const MODULES = {
  measure: {
    description: 'Measurement',
    color: '#06809C',
    methods: {
      measure_cells: registered({
        inputs: { data: { type: 'csv', required: true } },
        outputs: { measurements: { type: 'csv' } },
        params_schema: { channels: { type: 'list', default: ['dapi', 'gfp', 'cy5'] } },
      }, 'a'),
    },
  },
  annot: {
    description: 'Annotation',
    color: '#6467B4',
    methods: {
      annotate: registered({
        inputs: { data: { type: 'csv', required: true } },
        outputs: { labels: { type: 'csv' } },
        params_schema: {},
      }, 'b'),
    },
  },
  filt: {
    description: 'Filtering',
    methods: {
      filter_cells: registered({
        inputs: { data: { type: 'csv', required: true } },
        outputs: { table: { type: 'csv' } },
        params_schema: {},
      }, 'c'),
    },
  },
  plots: {
    description: 'Plots',
    methods: {
      plot_channel: registered({
        inputs: { data: { type: 'csv', required: true } },
        outputs: { figure: { type: 'png' } },
        params_schema: {
          channel_col: { type: 'str', options_from: 'data' },
          title: { type: 'str', default: 'cells' },
        },
      }, 'd'),
      join_cells: registered({
        inputs: { cells: { type: 'csv', required: true }, annotations: { type: 'csv', required: true } },
        outputs: { joined: { type: 'csv' } },
        params_schema: { join_key: { type: 'str', options_from: ['cells', 'annotations'] } },
      }, 'e'),
    },
  },
};

/** The mocked modules' colours, as computed CSS: measure #06809C, annot #6467B4. */
const MEASURE_RGB = 'rgb(6, 128, 156)';
const ANNOT_RGB = 'rgb(100, 103, 180)';

const MEASURE_OPTIONS = {
  fixed: ['cell_id', 'area', 'perimeter'],
  built: [{ params: ['channels'], names: ['dapi_mean', 'gfp_mean', 'cy5_mean'] }],
  patterns: ['intensity_*'],
  all: ['cell_id', 'area', 'perimeter', 'dapi_mean', 'gfp_mean', 'cy5_mean'],
};
const ANSWERS: Record<string, unknown> = {
  'measure.measure_cells': MEASURE_OPTIONS,
  'annot.annotate': {
    fixed: ['cell_id', 'cell_type', 'confidence'], built: [], patterns: [],
    all: ['cell_id', 'cell_type', 'confidence'],
  },
  'filt.filter_cells': { fixed: [], built: [], patterns: [], all: [] },
  __run_reference__: {
    fixed: ['cell_id', 'area', 'perimeter'],
    built: [{ params: ['channels'], names: ['dapi_mean', 'gfp_mean'] }],
    patterns: ['intensity_*'],
    all: ['cell_id', 'area', 'perimeter', 'dapi_mean', 'gfp_mean'],
    module: 'measure', method: 'measure_cells',
  },
};

const SELECTOR = { id: 'node_1', type: 'input_selector', method: '', module: '', params: {}, samples: SAMPLES, source: 'registered', fan_mode: 'out', keep_going: true, position: { x: 40, y: 200 } };
const MEASURE = { id: 'node_2', type: 'method', method: 'measure_cells', module: 'measure', params: {}, position: { x: 300, y: 120 } };
const plot = (params: Record<string, unknown>) => ({ id: 'node_3', type: 'method', method: 'plot_channel', module: 'plots', params, position: { x: 580, y: 200 } });

/** The pipelines the tests open by name. */
const PIPELINES: Record<string, unknown> = {
  one: {
    name: 'one', samples: SAMPLES,
    nodes: [SELECTOR, MEASURE, plot({ channel_col: 'dapi_mean' })],
    links: [
      { source: 'node_1', target: 'node_2', sourceHandle: 'output', targetHandle: 'data' },
      { source: 'node_2', target: 'node_3', sourceHandle: 'measurements', targetHandle: 'data' },
    ],
  },
  unwired: {
    name: 'unwired', samples: SAMPLES,
    nodes: [SELECTOR, plot({ channel_col: '' })],
    links: [],
  },
  nooptions: {
    name: 'nooptions', samples: SAMPLES,
    nodes: [SELECTOR, { ...MEASURE, method: 'filter_cells', module: 'filt' }, plot({ channel_col: 'intensity' })],
    links: [
      { source: 'node_1', target: 'node_2', sourceHandle: 'output', targetHandle: 'data' },
      { source: 'node_2', target: 'node_3', sourceHandle: 'table', targetHandle: 'data' },
    ],
  },
  several: {
    name: 'several', samples: SAMPLES,
    nodes: [
      SELECTOR, MEASURE,
      { id: 'node_4', type: 'method', method: 'annotate', module: 'annot', params: {}, position: { x: 300, y: 320 } },
      { id: 'node_3', type: 'method', method: 'join_cells', module: 'plots', params: { join_key: 'dapi_mean' }, position: { x: 580, y: 200 } },
    ],
    links: [
      { source: 'node_1', target: 'node_2', sourceHandle: 'output', targetHandle: 'data' },
      { source: 'node_1', target: 'node_4', sourceHandle: 'output', targetHandle: 'data' },
      { source: 'node_2', target: 'node_3', sourceHandle: 'measurements', targetHandle: 'cells' },
      { source: 'node_4', target: 'node_3', sourceHandle: 'labels', targetHandle: 'annotations' },
    ],
  },
  runref: {
    name: 'runref', samples: SAMPLES,
    nodes: [
      { id: 'node_1', type: 'run_reference', method: '', module: '', params: {}, run_id: '412', position: { x: 200, y: 200 } },
      // Unwired: its header shows the module colour the run's chip must take.
      { ...MEASURE, position: { x: 200, y: 360 } },
      plot({ channel_col: 'dapi_mean' }),
    ],
    links: [{ source: 'node_1', target: 'node_3', sourceHandle: 'output', targetHandle: 'data' }],
  },
};

interface Posted { path: string; body: string }

/** Every backend route; `/api/pipelines/demo` (what `?pipeline=demo` loads) answers the named pipeline. */
async function setupRoutes(page: Page, pipeline: string, posted: Posted[]): Promise<void> {
  // Registered first so the specific routes below take precedence.
  await page.route('**/api/**', async route => {
    const req = route.request();
    if (req.method() === 'POST') posted.push({ path: new URL(req.url()).pathname, body: req.postData() ?? '' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/pipelines/demo', r => r.fulfill(json(PIPELINES[pipeline])));
  await page.route('**/api/modules', r => r.fulfill(json(MODULES)));
  await page.route('**/api/samples', r => r.fulfill(json(SAMPLES)));
  await page.route('**/api/wfc/samples', r => r.fulfill(json([])));
  await page.route('**/api/dev/status', r => r.fulfill(json({ dev: false })));
  await page.route('**/api/workflow/validate', r => r.fulfill(json({ valid: true, errors: [] })));
  await page.route('**/api/workflow/run', async r => {
    posted.push({ path: '/api/workflow/run', body: r.request().postData() ?? '' });
    await r.fulfill(json({ job_id: 'job-1', pipeline_id: 'job-1' }));
  });
  await page.route('**/api/wfc/cache-status', r => r.fulfill(json({ rows: [], blocked_reason: null })));
  await page.route('**/api/contracts/*/output_options', async r => {
    const path = new URL(r.request().url()).pathname;
    const method = decodeURIComponent(path.split('/').slice(-2)[0]);
    posted.push({ path, body: r.request().postData() ?? '' });
    await r.fulfill(json(ANSWERS[method] ?? { fixed: [], built: [], patterns: [], all: [] }));
  });
}

/** Load a pipeline, select the downstream node, and return the param's block. */
async function openParam(page: Page, pipeline: string, param: string, posted: Posted[] = []): Promise<Locator> {
  await setupRoutes(page, pipeline, posted);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node[data-id="node_3"]')).toBeVisible({ timeout: 15_000 });
  await page.locator('.svelte-flow__node[data-id="node_3"]').click();
  const block = page.locator('.inspector .param-block').filter({ has: page.locator('.pname', { hasText: param }) });
  await expect(block.getByTestId('options-picker')).toBeVisible();
  return block;
}

/** A gallery shot of the Inspector, or of the whole page when *wholePage* is set. */
async function capture(page: Page, name: string, wholePage = false): Promise<void> {
  if (!CAPTURE_GALLERY) return;
  const path = `gallery/options-picker/${name}.png`;
  if (wholePage) await page.screenshot({ path });
  else await page.locator('.inspector').screenshot({ path });
}

const items = (block: Locator) => block.getByTestId('options-item');

/**
 * A header band on one line: every part on the same row, the chip whole, the
 * count inside at the right, and "Options of" whole while "via <input>" has
 * any width ("via" gives way first).
 */
async function expectOneLineBand(head: Locator): Promise<void> {
  const shape = await head.evaluate(h => {
    const box = h.getBoundingClientRect();
    const kids = [...h.children].map(k => k.getBoundingClientRect());
    const chip = h.querySelector('.chip .n') as HTMLElement;
    const lbl = h.querySelector('.lbl') as HTMLElement;
    const via = h.querySelector('.via') as HTMLElement;
    const count = h.querySelector('.scount')!.getBoundingClientRect();
    // The label's width as laid out, and unshrunk: a fraction of a pixel
    // short already draws the ellipsis, which the whole-pixel scrollWidth misses.
    const shown = lbl.getBoundingClientRect().width;
    lbl.style.flex = 'none';
    const whole = lbl.getBoundingClientRect().width;
    lbl.style.flex = '';
    return {
      tops: kids.map(k => Math.round(k.top + k.height / 2)),
      chipWhole: chip.scrollWidth <= chip.clientWidth + 1,
      countInside: count.right <= box.right + 0.5 && count.left > box.left,
      label: [shown, whole],
      labelWholeOrViaGone: shown >= whole - 0.01 || via.getBoundingClientRect().width < 0.5,
      widths: [box.width, ...kids.map(k => Math.round(k.width))],
    };
  });
  const why = JSON.stringify(shape);
  expect(new Set(shape.tops).size, why).toBe(1);
  expect(shape.chipWhole, why).toBe(true);
  expect(shape.countInside, why).toBe(true);
  expect(shape.labelWholeOrViaGone, why).toBe(true);
}

test('open: the chevron opens one section with its header, groups and the tick; no ⤢', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  const chevron = block.getByTestId('options-chevron');
  await expect(chevron).toBeEnabled();
  await expect(chevron).toHaveAttribute('title', 'Show the options');
  await chevron.click();
  const list = block.getByTestId('options-list');
  await expect(list).toBeVisible();
  // Open, the value box takes the controls blue (--accent).
  await expect(block.locator('.single-box')).toHaveCSS('border-top-color', 'rgb(74, 144, 217)');
  const head = list.getByTestId('options-section-head');
  await expect(head.locator('.chip')).toHaveText('measure_cells');
  await expect(head.locator('.chip .n')).toHaveCSS('background-color', MEASURE_RGB);
  await expect(head).toContainText('via data');
  await expect(head.locator('.scount')).toHaveText('7');
  await expect(head.locator('.sc')).toHaveCount(0);
  await expectOneLineBand(head);
  await expect(list.locator('.grp')).toHaveText(['Fixed', /Built from\s*param\s*channels ↗/, 'Patterns']);
  await expect(items(block)).toHaveText(['cell_id', 'area', 'perimeter', 'dapi_mean', 'gfp_mean', 'cy5_mean', 'intensity_*']);
  await expect(list.locator('.opt.cur')).toHaveText('dapi_mean');
  await expect(list.getByTestId('options-param-pill')).toHaveAttribute('title', 'Go to channels on measure_cells');
  // The options row has no ⤢; the plain string row beside it keeps it.
  await expect(block.locator('.act.expand')).toHaveCount(0);
  const title = page.locator('.inspector .param-block').filter({ has: page.locator('.pname', { hasText: 'title' }) });
  await expect(title.locator('.act.expand')).toHaveCount(1);
  await capture(page, 'open');
});

test('filtered: typing filters the open list', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  await block.getByTestId('options-input').fill('gfp');
  await expect(block.getByTestId('options-list')).toBeVisible();
  await expect(items(block)).toHaveText(['gfp_mean']);
  await capture(page, 'filtered');
});

test('no match: typed text that matches nothing says so', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  await block.getByTestId('options-input').fill('dapi_avg');
  await expect(block.getByTestId('options-no-match')).toHaveText('No match');
  await expect(items(block)).toHaveCount(0);
  await capture(page, 'no-match');
});

test('pattern fill: clicking a pattern fills its fixed part and keeps the list open', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  await block.getByTestId('options-chevron').click();
  await block.locator('.pat').click();
  await expect(block.getByTestId('options-input')).toHaveValue('intensity_');
  await expect(block.getByTestId('options-list')).toBeVisible();
  await expect(items(block)).toHaveText(['intensity_*']);
  await capture(page, 'pattern-fill');
});

test('pattern tick: a value fitting a pattern ticks it', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  const input = block.getByTestId('options-input');
  await input.fill('intensity_max');
  await input.press('Escape');
  await expect(block.getByTestId('options-list')).toHaveCount(0);
  await expect(input).toHaveValue('intensity_max');
  await block.getByTestId('options-chevron').click();
  await expect(block.locator('.pat.cur')).toHaveText('intensity_*');
  await expect(block.locator('.opt.cur')).toHaveCount(0);
  await capture(page, 'pattern-tick');
});

test('no tick: a value among no option and fitting no pattern ticks nothing', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  const input = block.getByTestId('options-input');
  await input.fill('dapi_avg');
  await input.press('Escape');
  await block.getByTestId('options-chevron').click();
  await expect(items(block)).toHaveCount(7);
  await expect(block.locator('.opt.cur, .pat.cur')).toHaveCount(0);
  await capture(page, 'no-tick');
});

test('keyboard: ArrowDown opens and moves, Enter picks, Esc closes and keeps the text', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  const input = block.getByTestId('options-input');
  await input.click();
  await input.press('Escape');
  await expect(block.getByTestId('options-list')).toHaveCount(0);
  await input.press('ArrowDown');
  await expect(block.getByTestId('options-list')).toBeVisible();
  await input.press('ArrowDown');
  await input.press('ArrowDown');
  await expect(block.locator('.opt.hl')).toHaveText('area');
  await capture(page, 'keyboard-highlight');
  await input.press('Enter');
  await expect(block.getByTestId('options-list')).toHaveCount(0);
  await expect(input).toHaveValue('area');
  // Picking leaves the row unlocked; ✓ locks it.
  await expect(block.locator('.row')).toHaveClass(/(^|\s)editing(\s|$)/);
});

test('locked: the chevron stays, dimmed and disabled, with its hover; the box is read-only', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  await expect(block.getByTestId('options-chevron')).toBeEnabled();
  await block.locator('.act.commit').click();
  await expect(block.locator('.row')).not.toHaveClass(/(^|\s)editing(\s|$)/);
  const chevron = block.getByTestId('options-chevron');
  await expect(chevron).toBeDisabled();
  await expect(chevron).toHaveClass(/(^|\s)off(\s|$)/);
  await expect(chevron).toHaveAttribute('title', 'Unlock the row to change it');
  const input = block.getByTestId('options-input');
  await expect(input).not.toBeEditable();
  await input.click();
  await expect(block.getByTestId('options-list')).toHaveCount(0);
  await expect(block.locator('.single-box')).not.toHaveCSS('border-top-color', 'rgb(74, 144, 217)');
  await capture(page, 'locked');
});

test('fit: at the default Inspector width the row shows ✓ and 🔗 unlocked, the padlock and the value start locked', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  const box = block.locator('.single-box');
  const viewport = page.viewportSize()!;
  // Inside the value box's own rectangle and the viewport, with no scrolling.
  const fits = async (el: Locator): Promise<void> => {
    const outer = (await box.boundingBox())!;
    const r = (await el.boundingBox())!;
    expect(r.width).toBeGreaterThan(0);
    expect(r.x).toBeGreaterThanOrEqual(outer.x - 0.5);
    expect(r.x + r.width).toBeLessThanOrEqual(outer.x + outer.width + 0.5);
    expect(r.x + r.width).toBeLessThanOrEqual(viewport.width);
  };
  expect(await box.evaluate(e => e.scrollWidth <= e.clientWidth)).toBe(true);
  await fits(block.locator('.act.commit'));
  await fits(block.locator('.act.bind'));
  await block.locator('.act.commit').click();
  await expect(block.locator('.row')).not.toHaveClass(/(^|\s)editing(\s|$)/);
  expect(await box.evaluate(e => e.scrollWidth <= e.clientWidth)).toBe(true);
  await fits(block.locator('.gutter'));
  await fits(block.locator('.act.edit'));
  await fits(block.locator('.act.bind'));
  // The value's start: the box's left edge sits inside the value box.
  const input = (await block.getByTestId('options-input').boundingBox())!;
  const outer = (await box.boundingBox())!;
  expect(input.x).toBeGreaterThanOrEqual(outer.x);
  expect(input.x).toBeLessThan(outer.x + outer.width - 28 * 2);
});

test('nothing wired: the chevron dims with "Nothing wired to data"', async ({ page }) => {
  const block = await openParam(page, 'unwired', 'channel_col');
  const chevron = block.getByTestId('options-chevron');
  await expect(chevron).toHaveAttribute('title', 'Nothing wired to data');
  await expect(chevron).toBeDisabled();
  await expect(chevron).toHaveClass(/(^|\s)off(\s|$)/);
  // Free text still goes in.
  await block.getByTestId('options-input').fill('anything');
  await expect(block.getByTestId('options-list')).toHaveCount(0);
  await capture(page, 'nothing-wired');
});

test('no options: an upstream declaring none dims the chevron with "No options on <node>"', async ({ page }) => {
  const block = await openParam(page, 'nooptions', 'channel_col');
  const chevron = block.getByTestId('options-chevron');
  await expect(chevron).toHaveAttribute('title', 'No options on filter_cells');
  await expect(chevron).toBeDisabled();
  // Locked, as the gallery shows it: the padlock, the value read-only, the chevron still dimmed.
  await block.locator('.act.commit').click();
  await expect(block.locator('.row')).not.toHaveClass(/(^|\s)editing(\s|$)/);
  await expect(block.locator('.gutter')).toHaveText('🔒');
  await expect(block.getByTestId('options-input')).toHaveValue('intensity');
  await expect(block.getByTestId('options-input')).not.toBeEditable();
  await expect(chevron).toBeDisabled();
  await capture(page, 'no-options');
});

test('several inputs: one section each, the one holding the value open, the rest folded', async ({ page }) => {
  const block = await openParam(page, 'several', 'join_key');
  await block.getByTestId('options-chevron').click();
  const sections = block.getByTestId('options-section');
  await expect(sections).toHaveCount(2);
  await expect(sections.nth(0).locator('.chip')).toHaveText('measure_cells');
  await expect(sections.nth(0)).toContainText('via cells');
  await expect(sections.nth(1).locator('.chip')).toHaveText('annotate');
  await expect(sections.nth(0).locator('.chip .n')).toHaveCSS('background-color', MEASURE_RGB);
  await expect(sections.nth(1).locator('.chip .n')).toHaveCSS('background-color', ANNOT_RGB);
  await expect(sections.nth(1)).toContainText('via annotations');
  await expect(sections.nth(1).locator('.scount')).toHaveText('3');
  await expect(sections.locator('.sc')).toHaveCount(2);
  await expect(sections.nth(0)).not.toHaveClass(/(^|\s)shut(\s|$)/);
  await expect(sections.nth(1)).toHaveClass(/(^|\s)shut(\s|$)/);
  await expect(sections.nth(1).getByTestId('options-item')).toHaveCount(0);
  for (const i of [0, 1]) await expectOneLineBand(sections.nth(i).getByTestId('options-section-head'));
  await capture(page, 'several-inputs-folded');
  // Unfolding the second shows its options; cell_id, in both, is ticked in both once it is the value.
  await sections.nth(1).getByTestId('options-section-head').click();
  await expect(sections.nth(1).getByTestId('options-item')).toHaveText(['cell_id', 'cell_type', 'confidence']);
  await block.getByTestId('options-input').press('Escape');
  await block.getByTestId('options-input').fill('cell_id');
  await block.getByTestId('options-input').press('Escape');
  await block.getByTestId('options-chevron').click();
  await expect(block.locator('.opt.cur')).toHaveText(['cell_id', 'cell_id']);
  // The second section's tick is in view for the shot.
  const secondTick = sections.nth(1).locator('.opt.cur');
  await secondTick.scrollIntoViewIfNeeded();
  await expect(secondTick).toBeInViewport();
  await capture(page, 'several-inputs-open');
});

test('Run Reference: the chip names the run in its method\'s colour; the request names the run', async ({ page }) => {
  const posted: Posted[] = [];
  const block = await openParam(page, 'runref', 'channel_col', posted);
  await block.getByTestId('options-chevron').click();
  const head = block.getByTestId('options-section-head');
  await expect(head.locator('.chip')).toHaveText('measure_cells · run 412');
  await expectOneLineBand(head);
  await expect(block.getByTestId('options-param-pill')).toHaveAttribute('title', 'Open run 412 in History on its Params tab');
  const ask = posted.find(p => p.path.includes('__run_reference__'));
  expect(ask && JSON.parse(ask.body)).toMatchObject({ slot: 'output', run_id: '412' });
  // The chip takes the run's method's module colour: the one a measure_cells node header shows.
  const chipColor = await head.locator('.chip .n').evaluate(e => getComputedStyle(e).backgroundColor);
  const headerColor = await page.locator('.svelte-flow__node[data-id="node_2"] .node-header')
    .evaluate(e => getComputedStyle(e).backgroundColor);
  expect(chipColor).toBe(MEASURE_RGB);
  expect(headerColor).toBe(MEASURE_RGB);
  await expect(head.locator('.chip .n')).toHaveCSS('color', 'rgb(255, 255, 255)');
  await capture(page, 'run-reference');
});

test('a value not among the options locks, and Run goes ahead with it', async ({ page }) => {
  const posted: Posted[] = [];
  const block = await openParam(page, 'one', 'channel_col', posted);
  const input = block.getByTestId('options-input');
  await input.fill('dapi_avg');
  await input.press('Escape');
  await block.locator('.act.commit').click();
  await expect(block.locator('.row')).not.toHaveClass(/(^|\s)editing(\s|$)/);
  await expect(input).toHaveValue('dapi_avg');
  await expect(block.locator('.row-error')).toHaveCount(0);
  await capture(page, 'locked-not-among');

  await page.locator('.btn-run').click();
  const summary = page.getByTestId('lock-summary');
  if (await summary.isVisible().catch(() => false)) {
    await summary.getByTestId('lock-summary-confirm').click();
  }
  await expect.poll(() => posted.some(p => p.path === '/api/workflow/run')).toBe(true);
  const run = posted.find(p => p.path === '/api/workflow/run')!;
  expect(run.body).toContain('dapi_avg');
});

// ── Case 8: the Built-from `param ↗` link ────────────────────────────────

test('Built-from link, method upstream: selects and centres it, lands on the param, and the typed value comes back unlocked', async ({ page }) => {
  const block = await openParam(page, 'one', 'channel_col');
  // Typed, not locked: typing opens the list filtered to gfp_mean, pill and all.
  await block.getByTestId('options-input').fill('gfp_m');
  await block.getByTestId('options-param-pill').click();

  const upstream = page.locator('.svelte-flow__node[data-id="node_2"]');
  await expect(upstream).toHaveClass(/selected/);
  await expect(page.locator('.svelte-flow__node[data-id="node_3"]')).not.toHaveClass(/selected/);
  const landed = page.locator('.inspector .param-block[data-param="channels"]');
  await expect(landed).toHaveClass(/landed/);
  await expect(landed).toBeInViewport();
  // Centred: the node's centre sits at the flow pane's centre.
  await expect.poll(async () => {
    const pane = (await page.locator('.svelte-flow').boundingBox())!;
    const box = (await upstream.boundingBox())!;
    return Math.max(
      Math.abs(box.x + box.width / 2 - (pane.x + pane.width / 2)),
      Math.abs(box.y + box.height / 2 - (pane.y + pane.height / 2)),
    );
  }).toBeLessThan(40);
  await capture(page, 'built-from-landed');
  // The highlight is brief.
  await expect(landed).not.toHaveClass(/landed/, { timeout: 3_000 });

  await page.locator('.svelte-flow__node[data-id="node_3"]').click();
  const back = page.locator('.inspector .param-block').filter({ has: page.locator('.pname', { hasText: 'channel_col' }) });
  await expect(back.getByTestId('options-input')).toHaveValue('gfp_m');
  await expect(back.getByTestId('options-input')).not.toHaveAttribute('readonly', /.*/);
  await expect(back.locator('.row')).toHaveClass(/editing/);
  await expect(back.locator('.gutter')).toHaveText('🔓');
});

test('Built-from link, Run Reference upstream: opens the run in History on its Params tab', async ({ page }) => {
  const block = await openParam(page, 'runref', 'channel_col');
  const RUN = {
    id: '412', module: 'measure', method: 'measure_cells', version: 1, versionIdentity: null,
    timestamp: 1_700_000_000_000, duration: 1.5, status: 'success',
    inputs: { channels: ['dapi', 'gfp'] }, outputs: {}, outputAvailability: {}, metrics: {},
    dataSource: 'ctrl_01', parentRunIds: [], upstreamRunIds: [], parents: [],
    codeFingerprint: null, envName: null, envFingerprint: null, gitCommit: null, wfcVersion: null,
    pipelineDocHash: null, keyedUnderEarlierRule: false, experimentId: 'exp1', runName: 'measure_cells',
    nid: 'v1', user: 'tester', favorite: false, pipelineId: null, scriptPath: null,
  };
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/wfc/runs', r => r.fulfill(json([RUN])));
  await page.route('**/api/wfc/pipelines', r => r.fulfill(json([])));
  await page.route('**/api/wfc/draft-steps', r => r.fulfill(json({ steps: [] })));
  await page.route('**/api/wfc/modules', r => r.fulfill(json(['measure'])));
  await page.route('**/api/wfc/methods', r => r.fulfill(json([])));
  await page.route('**/api/wfc/status', r => r.fulfill(json({ loaded: true, path: '/x', modules: 1, runs: 1 })));
  await page.route('**/api/wfc/run/*', async r => {
    if (!/\/api\/wfc\/run\/412(?:\?.*)?$/.test(r.request().url())) { await r.fallback(); return; }
    await r.fulfill(json(RUN));
  });
  await page.route('**/api/wfc/run/*/cancelled-descendants', r => r.fulfill(json([])));
  await page.route('**/api/wfc/run/*/artifacts', r => r.fulfill(json([])));

  await block.getByTestId('options-input').fill('gfp_m');
  await block.getByTestId('options-param-pill').click();

  await expect(page.locator('.main-content')).toBeHidden();
  await expect(page.locator('.toolbar .tab.active, .tab.active').filter({ hasText: /^History$/ })).toBeVisible();
  await expect(page.locator('.tab.active .tab-label')).toHaveText('Parameters');
  await capture(page, 'run-reference-landed', true);
});
