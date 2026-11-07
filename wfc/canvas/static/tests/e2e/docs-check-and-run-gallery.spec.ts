/**
 * The canvas's check-and-run screens, photographed for the user guide.
 *
 * One small project, served by mocked `/api/**` routes: module `my_analysis`
 * with `normalize`, `filter_data` and `summarize` registered at v1 (env
 * `scores-env`), and the pipeline `my-pipeline` running
 * normalize → filter_data → summarize over four samples. filter_data's
 * `threshold` is set to 0.7 (its default is 0.5); normalize's `method` and
 * summarize's `stat` are left at their declared defaults.
 *
 * The story the screens tell: normalize ran before, so three of its rows are
 * cached and scores_d's outputs were deleted; filter_data's threshold
 * changed, so its rows are new and summarize's re-run after them. The run
 * that follows reuses normalize's three cached rows, re-runs scores_d and
 * fails it, so normalize ends partial (3/4); filter_data is running on the
 * three good samples and summarize waits.
 *
 * Each screen is asserted on every run, so the spec fails when the UI it
 * photographs changes. The PNG writes are OPT-IN (`CAPTURE_GALLERY`, see
 * playwright.config.ts: `npm run test:e2e:gallery`) and go to
 * `userdocs/guide/_images/`, where the guide's pages link them:
 *
 *   canvas-runs-preview-locked.png  the Builder after Lock All, statuses shown
 *   canvas-lock-summary.png         the lock summary Run opens (crop)
 *   canvas-pipeline-running.png     the Builder mid-run, Run become Stop
 *   canvas-node-refused.png         summarize refused by Validate (crop)
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CAPTURE_GALLERY } from '../../playwright.config';
import { setupRouteReplay } from '../../src/lib/__fixtures__/route-replay';

const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../../userdocs/guide/_images');

// ---------------------------------------------------------------------------
// The project
// ---------------------------------------------------------------------------

const MODULE = 'my_analysis';
const ENV = 'scores-env';
const SAMPLES = ['scores_a', 'scores_b', 'scores_c', 'scores_d'];
const PIPELINE_NAME = 'my-pipeline';
const REGISTERED_AT = '2026-10-28T09:14:00+00:00';
/** The registered samples, as `/api/wfc/samples` lists them (the Input Selector's type chips). */
const SAMPLE_INFO = SAMPLES.map((name, i) => ({
  name, file_type: '.csv', registered_path: `/home/dana/projects/scores-study/data/${name}.csv`,
  file_size: 48_200 + i * 1_100, registered_at: REGISTERED_AT, description: null, file_count: null,
}));
const NODE_NORMALIZE = 'node_2';
const NODE_FILTER = 'node_3';
const NODE_SUMMARIZE = 'node_4';

const NORMALIZE_CONTRACT = {
  inputs: { data: { type: '.csv', required: true, description: 'One score table per sample' } },
  outputs: { normalized: { type: '.csv', required: true, description: 'Scores scaled to one range' } },
  params_schema: { method: { type: 'str', default: 'zscore', required: false,
                             description: 'How the scores are scaled' } },
  executor: 'python',
};
const FILTER_CONTRACT = {
  inputs: { data: { type: '.csv', required: true, description: 'Normalized scores, one row per item' } },
  outputs: { filtered: { type: '.csv', required: true, description: 'The rows whose score passes the threshold' } },
  params_schema: { threshold: { type: 'float', default: 0.5, required: false,
                                description: 'Keep rows whose score is at least this' } },
  executor: 'python',
};
const SUMMARIZE_CONTRACT = {
  inputs: { data: { type: '.csv', required: true, description: 'The filtered rows' } },
  outputs: { summary: { type: '.csv', required: true, description: 'One line of statistics' } },
  params_schema: { stat: { type: 'str', default: 'mean', required: false,
                           description: 'The statistic to report' } },
  executor: 'python',
};

/** A method as `/api/modules` sends it: its newest (and only) version's contract plus `versions`. */
function catalogued(contract: Record<string, unknown>, identity: string) {
  return { ...contract, env: ENV,
           versions: [{ label: 1, identity: identity.repeat(8), registered_at: REGISTERED_AT, env: ENV, newest: true, ...contract }] };
}
const BUILDER_MODULES = {
  [MODULE]: {
    description: 'Score tables: normalize, filter and summarize',
    methods: {
      normalize: catalogued(NORMALIZE_CONTRACT, 'a1c4e702'),
      filter_data: catalogued(FILTER_CONTRACT, 'b7d2f915'),
      summarize: catalogued(SUMMARIZE_CONTRACT, 'c3e8a046'),
    },
  },
};
const PIPELINE = {
  name: PIPELINE_NAME,
  nodes: [
    { id: 'node_1', type: 'input_selector', method: '', module: '', params: {}, samples: SAMPLES,
      source: 'registered', fan_mode: 'out', keep_going: true, position: { x: 40, y: 240 } },
    { id: NODE_NORMALIZE, type: 'method', method: 'normalize', module: MODULE, params: {}, position: { x: 330, y: 240 } },
    { id: NODE_FILTER, type: 'method', method: 'filter_data', module: MODULE, params: { threshold: 0.7 },
      position: { x: 620, y: 240 } },
    { id: NODE_SUMMARIZE, type: 'method', method: 'summarize', module: MODULE, params: {}, position: { x: 910, y: 240 } },
  ],
  links: [
    { source: 'node_1', target: NODE_NORMALIZE, sourceHandle: 'output', targetHandle: 'data' },
    { source: NODE_NORMALIZE, target: NODE_FILTER, sourceHandle: 'normalized', targetHandle: 'data' },
    { source: NODE_FILTER, target: NODE_SUMMARIZE, sourceHandle: 'filtered', targetHandle: 'data' },
  ],
  samples: SAMPLES,
};

interface Doc { nodes: { id: string; type?: string }[]; samples: string[] }

/**
 * The Runs Preview rows after Lock All: normalize cached except scores_d,
 * whose outputs were deleted; filter_data new (its threshold changed);
 * summarize new because filter_data re-runs before it.
 */
function cacheStatusFor(doc: Doc) {
  const rows = [];
  for (const n of doc.nodes.filter(x => x.type === 'method')) {
    for (const [i, s] of doc.samples.entries()) {
      const base = { key: `${n.id}::${s}::default`, node_id: n.id, sample: s, variant: 'default',
                     reason: null, cache_key: null, draft_mode: false };
      if (n.id === NODE_NORMALIZE) {
        const missing = s === 'scores_d';
        rows.push({ ...base, status: missing ? 'outputs_missing' : 'cached_local',
                    source_run_id: 101 + i, source_nid: 'v1',
                    outputs: [{ slot: 'normalized', location: missing ? 'missing' : 'local' }] });
      } else {
        rows.push({ ...base, status: n.id === NODE_FILTER ? 'new_step_changed' : 'new_upstream_reruns',
                    source_run_id: null, source_nid: null, outputs: [] });
      }
    }
  }
  return { rows, blocked_reason: null };
}

// The run: one status answer, held mid-run. normalize reused its three
// cached rows and re-ran scores_d, which failed; filter_data is running on
// the three good samples; summarize waits.
const JOB_ID = 'p_3f9c2a71';
const SCORES_D_ERROR = "scores_d: column 'score' not found in scores_d.csv (columns: 'id', 'value')";
const FILTER_RUN = 'run_9a4c71e2';
const RUNNING = {
  job_id: JOB_ID,
  overall_status: 'running',
  thread_alive: true,
  log: '',
  error: null,
  node_states: {
    [NODE_NORMALIZE]: { status: 'mixed', tally: { running: 0, completed: 3, failed: 1 },
                        run_ids: ['run_d07e3b15', 'run_c40a6e58', 'run_8b3d7c12', 'run_5e1f0a9c'],
                        error: SCORES_D_ERROR, error_run_id: 'run_d07e3b15', error_sample: 'scores_d' },
    [NODE_FILTER]: { status: 'running', tally: { running: 3, completed: 0, failed: 0 },
                     run_ids: [FILTER_RUN, 'run_9a4c71e3', 'run_9a4c71e4'] },
    [NODE_SUMMARIZE]: { status: 'pending' },
  },
};
// filter_data's log so far, on scores_a; no terminal frame, so it stays live.
const FILTER_LOG = [
  'filter_data · scores_a · threshold 0.8',
  'read 1204 rows from normalized.csv',
  'kept 693 rows with score >= 0.8',
  'writing filtered.csv …',
];

// Validate's refusal once summarize's input link is removed: the server's
// `unwired_input_errors` message (wfc/graph/validation.py).
const REFUSAL = `Node '${NODE_SUMMARIZE}': ${MODULE}.summarize declares input 'data', which nothing is `
  + "connected to (its inputs: 'data'). Connect a link to 'data', or declare it `required: false` "
  + "in the method's method.yaml.";

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

/** What the mocked server answers; a test may change it mid-test. */
interface Server { validate: unknown; posted: string[] }

async function routes(page: Page, server: Server): Promise<void> {
  // The log stream is an EventSource, which a route cannot pace: the replay
  // helper patches it in the page. Its routes are shadowed by the catch-all
  // below (a route registered later answers first).
  await setupRouteReplay(page, [], {
    sseStream: { events: FILTER_LOG.map((line, i) => ({ delayMs: i === 0 ? 50 : 120, eventType: 'stdout' as const,
                                                        data: { data: line } })) },
  });
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url()).pathname;
    if (req.method() === 'POST') server.posted.push(url);
    // Registry (the Builder's palette and draft check read these at boot)
    if (url === '/api/registry/drafts') return route.fulfill(json({ drafts: [] }));
    if (url === '/api/registry/drafts/snapshot-check') return route.fulfill(json({ drafts: [] }));
    // Builder
    // `?pipeline=demo` loads the one document the canvas boots with; it is my-pipeline.
    if (url === '/api/pipelines/demo') return route.fulfill(json(PIPELINE));
    if (url === '/api/modules') return route.fulfill(json(BUILDER_MODULES));
    if (url === '/api/samples') return route.fulfill(json(SAMPLES));
    if (url === '/api/dev/status') return route.fulfill(json({ dev: false }));
    if (url === '/api/workflow/validate') return route.fulfill(json(server.validate));
    if (url === '/api/wfc/cache-status') return route.fulfill(json(cacheStatusFor(req.postDataJSON() as Doc)));
    // Run
    if (url === '/api/workflow/run') return route.fulfill(json({ status: 'started', job_id: JOB_ID, message: 'started' }));
    if (url === `/api/workflow/status/${JOB_ID}`) return route.fulfill(json(RUNNING));
    if (url === '/api/wfc/samples') return route.fulfill(json(SAMPLE_INFO));
    return route.fulfill(json({}));
  });
}

// ---------------------------------------------------------------------------
// Shots
// ---------------------------------------------------------------------------

/** Let fonts and layout settle before a shot. */
async function settle(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
}

/** The full window. */
async function shotPage(page: Page, name: string): Promise<void> {
  if (!CAPTURE_GALLERY) return;
  await settle(page);
  await page.screenshot({ path: path.join(OUT, name) });
}

/** A region of the window: the boxes given, padded, clipped to the window. */
async function shotRegion(page: Page, boxes: Locator[], name: string, pad = 16, padY = pad): Promise<void> {
  if (!CAPTURE_GALLERY) return;
  await settle(page);
  const rects = await Promise.all(boxes.map(async b => (await b.boundingBox())!));
  const vp = page.viewportSize()!;
  const x = Math.max(0, Math.min(...rects.map(r => r.x)) - pad);
  const y = Math.max(0, Math.min(...rects.map(r => r.y)) - padY);
  const right = Math.min(vp.width, Math.max(...rects.map(r => r.x + r.width)) + pad);
  const bottom = Math.min(vp.height, Math.max(...rects.map(r => r.y + r.height)) + padY);
  await page.screenshot({ path: path.join(OUT, name), clip: { x, y, width: right - x, height: bottom - y } });
}

test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

test.beforeAll(() => {
  if (CAPTURE_GALLERY) expect(fs.existsSync(OUT), `${OUT} is the guide's image folder`).toBe(true);
});

async function boot(page: Page, server: Server): Promise<void> {
  await routes(page, server);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(4, { timeout: 15_000 });
  // The Input Selector lists the four samples with their registered type.
  const selector = page.locator('.svelte-flow__node[data-id="node_1"]');
  await expect(selector.locator('.sample-table-name')).toHaveText(SAMPLES);
  await expect(selector.locator('.sample-table .type-chip')).toHaveText(Array(4).fill('.csv'));
}

const nodeOf = (page: Page, id: string) => page.locator(`.svelte-flow__node[data-id="${id}"] .custom-node`);

test('Lock All\'s statuses, the lock summary Run opens, and the pipeline mid-run', async ({ page }) => {
  const server: Server = { validate: { valid: true, errors: [], draftRefusals: [] }, posted: [] };
  await boot(page, server);
  const preview = page.locator('.runs-preview-panel');
  const summary = page.getByTestId('lock-summary');

  // filter_data in the Inspector: its threshold, set to 0.7.
  await page.locator(`.svelte-flow__node[data-id="${NODE_FILTER}"] .node-title`).click();
  await expect(page.locator('.inspector [data-param="threshold"] input')).toHaveValue('0.7');
  await expect(preview.locator('.tally-unlocked')).toHaveText('12 runs · statuses appear after Lock All');

  // Lock All: the summary lists the row open in the Inspector and the two
  // parameters left at their defaults; Lock.
  await preview.getByTestId('preview-lock-all').click();
  await expect(summary.locator('.confirm-title')).toHaveText('Lock All');
  await expect(summary.getByTestId('lock-summary-unlocked').locator('li')).toHaveText(['filter_data · threshold']);
  await expect(summary.getByTestId('lock-summary-default')).toHaveText(['normalizemethodzscore', 'summarizestatmean']);
  await expect(summary.getByTestId('lock-summary-confirm')).toHaveText('Lock');
  await summary.getByTestId('lock-summary-confirm').click();
  await expect(summary).toBeHidden();
  expect(server.posted).toContain('/api/wfc/cache-status');

  // 1. The statuses: the All runs view's counts, then normalize's rows.
  const summaryRow = (id: string) => preview.locator(`tr.summary-row[data-method="${id}"]`);
  await expect(summaryRow(NODE_NORMALIZE).locator('td')).toHaveText(['normalize', '4', '3', '0', '1', '0', '0', 'details ›']);
  await expect(summaryRow(NODE_FILTER).locator('td')).toHaveText(['filter_data', '4', '0', '0', '0', '4', '0', 'details ›']);
  await expect(summaryRow(NODE_SUMMARIZE).locator('td')).toHaveText(['summarize', '4', '0', '0', '0', '4', '0', 'details ›']);
  await expect(preview.locator('.footer .tally > span')).toHaveText([
    '● 3 cached · local', '● 0 cached · remote', '● 1 outputs missing', '● 8 new', '● 0 blocked',
  ]);
  const run = preview.getByTestId('preview-run');
  await expect(run).toHaveText('Run 9 jobs');
  await expect(run).toBeEnabled();
  await expect(page.locator('.toolbar .btn-run')).toBeEnabled();
  // Each node's own view names every row's status; filter_data's and summarize's first.
  const view = preview.locator('.view-picker select').first();
  await expect(view.locator('option')).toHaveText(['All runs (12)', 'normalize (4)', 'filter_data (4)', 'summarize (4)']);
  const statuses = preview.locator('tr.run-row .col-status .pill');
  const actions = preview.locator('tr.run-row .col-action');
  await view.selectOption(NODE_FILTER);
  await expect(statuses).toHaveText(Array(4).fill('● new · this step changed'));
  await expect(actions).toHaveText(Array(4).fill('will run'));
  await expect(preview.locator('tr.run-row .p-val')).toHaveText(Array(4).fill('0.7'));
  await view.selectOption(NODE_SUMMARIZE);
  await expect(statuses).toHaveText(Array(4).fill('● new · upstream re-runs'));
  // normalize's: three cached with a link to the run they reuse, scores_d's outputs missing.
  await view.selectOption(NODE_NORMALIZE);
  await expect(statuses).toHaveText(['● cached · local', '● cached · local', '● cached · local', '● outputs missing']);
  await expect(actions).toHaveText(['skip', 'skip', 'skip', 're-run']);
  await expect(preview.locator('tr.run-row .col-where')).toHaveText(['1 local', '1 local', '1 local', '1 missing']);
  await expect(preview.locator('tr.run-row .nid-link')).toHaveText(['v1', 'v1', 'v1', 'v1']);
  await expect(preview.locator('tr.run-row .nid-link').first()).toHaveAttribute('title', 'Open the source run in History');
  await expect(preview.locator('tr.group-row')).toHaveText(/default · 4 runs · 3 cached · local · 1 outputs missing/);
  // scores_a's row open: its one output, read here.
  await preview.locator('tr.run-row').first().locator('.btn-chevron').click();
  await expect(preview.locator('tr.output-line td:not(:empty)')).toHaveText(['normalized', 'local', 'read here']);
  await shotPage(page, 'canvas-runs-preview-locked.png');

  // 2. An edit (threshold 0.8, not yet locked) clears the statuses; Run opens the summary.
  const thresholdRow = page.locator('.inspector [data-param="threshold"]');
  await thresholdRow.locator('button.act.edit[title="unlock to edit"]').click();
  await thresholdRow.locator('input').fill('0.8');
  await expect(preview.locator('.tally-unlocked')).toHaveText('12 runs · statuses appear after Lock All');
  await page.locator('.toolbar .btn-run').click();
  await expect(summary).toBeVisible();
  await expect(summary.locator('.confirm-title')).toHaveText('Lock the unlocked rows, then run');
  await expect(summary.locator('.section-title')).toHaveText([
    'Still unlocked (1)', "Runs the method's declared default (2)",
  ]);
  await expect(summary.getByTestId('lock-summary-unlocked').locator('li')).toHaveText(['filter_data · threshold']);
  await expect(summary.locator('thead th')).toHaveText(['Node', 'Parameter', 'Value']);
  await expect(summary.getByTestId('lock-summary-default')).toHaveText(['normalizemethodzscore', 'summarizestatmean']);
  await expect(summary.getByTestId('lock-summary-cancel')).toHaveText('Cancel');
  await expect(summary.getByTestId('lock-summary-confirm')).toHaveText('Lock and run');
  expect(server.posted).not.toContain('/api/workflow/run');
  await shotRegion(page, [summary.locator('.confirm-card')], 'canvas-lock-summary.png', 16);

  // 3. Lock and run: the run starts; Run becomes Stop.
  await summary.getByTestId('lock-summary-confirm').click();
  await expect(summary).toBeHidden();
  await expect.poll(() => server.posted).toContain('/api/workflow/run');
  const toolbar = page.locator('.toolbar');
  await expect(toolbar.locator('.btn-stop')).toHaveText('Stop');
  await expect(toolbar.locator('.btn-run')).toHaveCount(0);
  await expect(page.locator('.status-bar')).toContainText('▶ Running...');
  const footer = (id: string) => nodeOf(page, id).locator('.node-footer .status-label');
  await expect(footer(NODE_NORMALIZE)).toHaveText(/^◐\s*partial 3\/4 ✓\s*$/);
  await expect(footer(NODE_FILTER)).toHaveText(/^↻\s*running\.\.\.\s*$/);
  await expect(footer(NODE_SUMMARIZE)).toHaveText(/^●\s*queued\.\.\.\s*$/);
  // filter_data's Output tab follows its log as it runs.
  await page.locator('.inspector .tabs .tab', { hasText: 'Output' }).click();
  await expect(page.getByTestId('log-pane')).toHaveText(FILTER_LOG.join('\n') + '\n');
  await expect(page.getByTestId('log-count')).toHaveText(`${FILTER_LOG.length} lines`);
  await expect(page.locator('.inspector .output-run-id')).toHaveText(`#${FILTER_RUN}`);
  // The run cleared the statuses: the preview's All runs view, counts only.
  await view.selectOption('all');
  await expect(preview.locator('tr.summary-row .col-total')).toHaveText(['4', '4', '4']);
  await expect(preview.locator('.tally-unlocked')).toHaveText('12 runs · statuses appear after Lock All');
  await shotPage(page, 'canvas-pipeline-running.png');
});

test('Validate refuses summarize once its input is disconnected', async ({ page }) => {
  const server: Server = { validate: { valid: true, errors: [], draftRefusals: [] }, posted: [] };
  await boot(page, server);

  // Remove the link into summarize's `data` input.
  const into = page.locator(`.svelte-flow__edge[data-id="e_2"]`);
  await into.hover();
  await into.locator('.edge-delete-btn').click({ force: true });
  await expect(page.locator('.svelte-flow__edge')).toHaveCount(2);

  // 4. Validate: the server refuses summarize; its node and the Inspector say so.
  server.validate = { valid: false, errors: [REFUSAL], draftRefusals: [] };
  await page.getByRole('button', { name: 'Validate' }).click();
  const node = nodeOf(page, NODE_SUMMARIZE);
  const refused = node.locator('.node-footer').getByTestId('node-refused');
  await expect(refused).toHaveText('⚠ refused · 1 issue');
  await expect(refused).toHaveAttribute('title', REFUSAL);
  await expect(node).toHaveCSS('border-top-color', await page.evaluate(() => {
    const probe = document.createElement('div');
    probe.style.color = 'var(--status-failed)';
    document.body.appendChild(probe);
    const c = getComputedStyle(probe).color;
    probe.remove();
    return c;
  }));
  await page.locator(`.svelte-flow__node[data-id="${NODE_SUMMARIZE}"] .node-title`).click();
  const box = page.getByTestId('inspector-refusals');
  await expect(box.locator('.refusal-head')).toHaveText('✗ The server refused this step');
  await expect(box.locator('li')).toHaveText([REFUSAL]);
  await expect(box.locator('.refusal-hint')).toHaveText(
    'Fix it in the pipeline, or in the method\'s files if the message says so, then Validate or Run again.');
  await expect(page.getByTestId('pipeline-error-banner').locator('li')).toHaveText([REFUSAL]);
  // The footer's hover text is the browser's own tooltip, which a screenshot
  // does not draw: the shot pairs the node with the Inspector's box, the same
  // lines, down to the Inspector's tabs. The pointer leaves the node so its
  // delete button is hidden.
  await page.mouse.move(700, 150);
  await shotRegion(page, [node, box, page.locator('.inspector .tabs')], 'canvas-node-refused.png', 28, 6);
});
