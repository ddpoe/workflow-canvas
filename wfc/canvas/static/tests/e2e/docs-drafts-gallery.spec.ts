/**
 * The canvas's draft screens, photographed for the user guide.
 *
 * One small project, served by mocked `/api/**` routes: module `my_analysis`
 * with `normalize`, `filter_data` and `summarize` registered at v1; a draft
 * VERSION of `filter_data` (its folder's files updated since v1, registering
 * it adds v2) and a draft METHOD `plot_scores` whose Registration check fails
 * on its env. The pipeline `my-pipeline` runs normalize → filter_data (on
 * ● DRAFT) → summarize over three samples, and its last draft run is kept.
 *
 * Each screen is asserted on every run, so the spec fails when the UI it
 * photographs changes. The PNG writes are OPT-IN (`CAPTURE_GALLERY`, see
 * playwright.config.ts: `npm run test:e2e:gallery`) and go to
 * `userdocs/guide/_images/`, where the guide's pages link them:
 *
 *   canvas-drafts-group.png      Registry › Methods, the Drafts group
 *   canvas-draft-from-folder.png the Draft from folder dialog (crop)
 *   canvas-draft-panel.png       filter_data's open draft (crop)
 *   canvas-draft-invalid.png     plot_scores's failed check (crop)
 *   canvas-draft-palette.png     the Builder palette's Drafts group (crop)
 *   canvas-draft-mode.png        the Builder in draft mode, Inspector open
 *   canvas-draft-refused.png     filter_data's node refused by Validate (crop)
 *   canvas-draft-run.png         filter_data's Draft run tab (crop)
 *   history-draft-runs.png       History › Descendants with the draft steps
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CAPTURE_GALLERY } from '../../playwright.config';

const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../../userdocs/guide/_images');

// ---------------------------------------------------------------------------
// The project
// ---------------------------------------------------------------------------

const MODULE = 'my_analysis';
const ENV = 'scores-env';
const SAMPLES = ['scores_a', 'scores_b', 'scores_c'];
const FOLDER = '/home/dana/projects/scores-study/src/my_analysis/filter_data';
const PLOT_FOLDER = '/home/dana/projects/scores-study/src/my_analysis/plot_scores';
const PIPELINE_ID = 'p_3f9c2a71';
const PIPELINE_NAME = 'my-pipeline';
const REGISTERED_AT = '2026-10-28T09:14:00+00:00';
const MIN = 60_000;
// Times relative to now so the relative stamps read like a run of this morning.
const DRAFT_RUN_AT = Date.now() - 12 * MIN;
const NORMALIZE_AT = Date.now() - 13 * MIN;

/** One registered v1, in the Registry's `/api/registry/methods` shape. */
function regVersion(identity: string, runCount: number) {
  return {
    label: 1, identity: identity.repeat(8), registered_at: REGISTERED_AT, env: ENV, runCount, newest: true,
    commit: '9b2e41c07d' + '0'.repeat(30), envFingerprint: '4c1d9e2a7b' + '0'.repeat(54),
    image: 'docker://local/scores-env@sha256:1e7a9c33' + '0'.repeat(56), codeTree: '5f0c7a21d4' + '0'.repeat(30),
  };
}
const REG_METHODS = [
  { name: 'normalize', module: MODULE, env: ENV, runCount: 6, source: null, versions: [regVersion('a1c4e702', 6)] },
  { name: 'filter_data', module: MODULE, env: ENV, runCount: 6, source: null, versions: [regVersion('b7d2f915', 6)] },
  { name: 'summarize', module: MODULE, env: ENV, runCount: 6, source: null, versions: [regVersion('c3e8a046', 6)] },
];
const REG_MODULES = [
  { name: MODULE, description: 'Score tables: normalize, filter and summarize', methods: 3,
    source: 'src/my_analysis', contracts: [] },
];

// filter_data's draft: the folder as it is now. Its contract is v1's.
const FILTER_CONTRACT = {
  inputs: { data: { type: '.csv', required: true, description: 'Normalized scores, one row per item' } },
  outputs: { filtered: { type: '.csv', required: true, description: 'The rows whose score passes the threshold' } },
  params_schema: { threshold: { type: 'float', default: 0.5, required: false,
                                description: 'Keep rows whose score is at least this' } },
  executor: 'python',
};
const FILTER_YAML = `# Contract for filter_data:
#   inputs.data       <-> ctx.input("data")
#   outputs.filtered  <-> ctx.save_artifact("filtered", ...)
#   params.threshold  <-> ctx.params["threshold"]
inputs:
  data:
    type: .csv
    required: true
    description: "Normalized scores, one row per item"

outputs:
  filtered:
    type: .csv
    required: true
    description: "The rows whose score passes the threshold"

params:
  threshold:
    type: float
    default: 0.5
    description: "Keep rows whose score is at least this"

executor: python
env: ${ENV}
`;
const FILTER_PY = `"""filter_data: keep the rows whose score is at least the threshold."""
import csv

import wfc_client as wfc


@wfc.method
def filter_data(ctx):
    with open(ctx.input("data"), newline="") as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys()) if rows else ["id", "score"]

    threshold = float(ctx.params["threshold"])
    kept = [row for row in rows if float(row["score"]) >= threshold]

    out_path = ctx.workdir / "filtered.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(kept)
    ctx.save_artifact("filtered", out_path)

    ctx.log_metric("rows_in", len(rows))
    ctx.log_metric("rows_kept", len(kept))


if __name__ == "__main__":
    wfc.run()
`;
const FILTER_CHECK = [
  { ok: true, text: `Names \`${MODULE}/filter_data\` can be registered` },
  { ok: true, text: `Module \`${MODULE}\` is registered` },
  { ok: true, text: 'Folder `src/my_analysis/filter_data` exists' },
  { ok: true, text: `\`method.yaml\` reads · env \`${ENV}\` is registered` },
  { ok: true, text: 'Script `filter_data.py` found' },
  { ok: true, text: "Contract checks pass: inputs, parameter defaults, options forms and the script's names" },
];
const FILTER_DRAFT = {
  module: MODULE, method: 'filter_data', kind: 'version', folder: FOLDER, env: ENV,
  newestLabel: 1, registersAs: 2, filesUpdated: true, stale: false, valid: true, reasons: [],
  contract: FILTER_CONTRACT, check: { ok: true, lines: FILTER_CHECK },
  draftRun: { startedAt: new Date(DRAFT_RUN_AT).toISOString(), pipelineId: PIPELINE_ID,
              pipelineName: PIPELINE_NAME, status: 'success', running: false },
};

// plot_scores: a new method whose env is not registered.
const PLOT_REASON = 'env `plots-env` is not registered';
const PLOT_CONTRACT = {
  inputs: { summary: { type: '.csv', required: true } },
  outputs: { plot: { type: '.png', required: true } },
  params_schema: { bins: { type: 'int', default: 20, required: false } },
  executor: 'python',
};
const PLOT_DRAFT = {
  module: MODULE, method: 'plot_scores', kind: 'method', folder: PLOT_FOLDER, env: 'plots-env',
  newestLabel: null, registersAs: 1, filesUpdated: false, stale: false, valid: false,
  reasons: [PLOT_REASON], contract: PLOT_CONTRACT, draftRun: null,
  check: { ok: false, lines: [
    { ok: true, text: `Names \`${MODULE}/plot_scores\` can be registered` },
    { ok: true, text: `Module \`${MODULE}\` is registered` },
    { ok: true, text: 'Folder `src/my_analysis/plot_scores` exists' },
    { ok: true, text: '`method.yaml` reads' },
    { ok: false, text: PLOT_REASON },
    { ok: true, text: 'Script `plot_scores.py` found' },
  ] },
};
const PLOT_PY = `"""plot_scores: a histogram of the summarized scores."""
import csv

import matplotlib.pyplot as plt
import wfc_client as wfc


@wfc.method
def plot_scores(ctx):
    with open(ctx.input("summary"), newline="") as f:
        scores = [float(row["score"]) for row in csv.DictReader(f)]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(scores, bins=int(ctx.params["bins"]))
    out_path = ctx.workdir / "plot.png"
    fig.savefig(out_path, dpi=150)
    ctx.save_artifact("plot", out_path)
`;
const PLOT_YAML = `inputs:
  summary:
    type: .csv
    required: true
outputs:
  plot:
    type: .png
    required: true
params:
  bins:
    type: int
    default: 20
executor: python
env: plots-env
`;

const DRY_RUN = {
  ok: true, kind: 'version', module: MODULE, method: 'filter_data', newestLabel: 1, registersAs: 2,
  message: `Module ${MODULE} already has a method called filter_data (newest version v1). `
    + 'This folder becomes its DRAFT version; registering it adds v2.',
  draft: null,
};

// The last draft run: filter_data's own step and summarize's after it, per sample.
const NODE_FILTER = 'node_3';
const NODE_SUMMARIZE = 'node_4';
const draftString = (node: string, sample: string) => `draft:${MODULE}.filter_data/${node}@${sample}`;
const SIZES: Record<string, number[]> = { [NODE_FILTER]: [412_330, 398_812, 405_117], [NODE_SUMMARIZE]: [18_204, 17_966, 18_530] };
function runNode(nodeId: string, method: string, isDraft: boolean) {
  const steps = SAMPLES.map((sample, i) => ({
    sample, status: 'success', sizeBytes: SIZES[nodeId][i], draftString: draftString(nodeId, sample) }));
  return { nodeId, module: MODULE, method, isDraft, status: 'success',
           sizeBytes: steps.reduce((n, s) => n + s.sizeBytes, 0), steps };
}
const RUN_NODES = [runNode(NODE_FILTER, 'filter_data', true), runNode(NODE_SUMMARIZE, 'summarize', false)];
const DRAFT_RUN = {
  module: MODULE, method: 'filter_data', startedAt: new Date(DRAFT_RUN_AT).toISOString(),
  pipelineId: PIPELINE_ID, pipelineName: PIPELINE_NAME, samples: SAMPLES, status: 'success',
  tally: { success: 6 }, totalBytes: RUN_NODES.reduce((n, x) => n + x.sizeBytes, 0),
  keptUntil: "filter_data's draft runs again", nodes: RUN_NODES,
};

// History: normalize's recorded runs, and the draft steps after them.
function recordedRun(sample: string, i: number) {
  return {
    id: `run_${['5e1f0a9c', '8b3d7c12', 'c40a6e58'][i]}`, module: MODULE, method: 'normalize', version: 1,
    timestamp: NORMALIZE_AT + i * 4_000, duration: 1.8 + i * 0.3, status: 'success', inputs: {}, outputs: {},
    outputAvailability: {}, metrics: {}, dataSource: sample, parentRunIds: [], parents: [],
    versionIdentity: 'a1c4e702'.repeat(8), codeFingerprint: null, envName: ENV, envFingerprint: null,
    gitCommit: null, wfcVersion: null, pipelineDocHash: null, keyedUnderEarlierRule: false,
    experimentId: 'exp_scores', runName: `${PIPELINE_NAME}/normalize`, nid: 'v1', user: 'dana',
    favorite: false, pipelineId: PIPELINE_ID, pipelineName: PIPELINE_NAME, scriptPath: null,
    nodeId: 'node_2', upstreamRunIds: [],
  };
}
const RUNS = SAMPLES.map(recordedRun);
const STEPS = SAMPLES.flatMap((sample, i) => {
  const own = {
    draftString: draftString(NODE_FILTER, sample), draft: `${MODULE}.filter_data`, isDraft: true,
    module: MODULE, method: 'filter_data', nodeId: NODE_FILTER, sample, status: 'success',
    timestamp: DRAFT_RUN_AT + 20_000 + i * 3_000, duration: 2.1 + i * 0.2, pipelineId: PIPELINE_ID,
    pipelineName: PIPELINE_NAME, nid: null, version: null, parentRunIds: [RUNS[i].id], parentDraftSteps: [],
  };
  const after = {
    ...own, draftString: draftString(NODE_SUMMARIZE, sample), isDraft: false, method: 'summarize',
    nodeId: NODE_SUMMARIZE, version: 1, timestamp: own.timestamp + 6_000, duration: 0.9,
    parentRunIds: [], parentDraftSteps: [own.draftString],
  };
  return [own, after];
});
const SUMMARIES = [
  { pipelineId: PIPELINE_ID, name: PIPELINE_NAME, status: 'succeeded', expected: 3,
    nodeOrder: ['node_1', 'node_2', NODE_FILTER, NODE_SUMMARIZE], draftMode: true,
    drafts: [`${MODULE}.filter_data`], draftStepCount: STEPS.length, draftSteps: STEPS, cleared: null },
];

// The Builder: the module catalogue and the pipeline document.
const contractOf = (input: string, output: string) => ({
  inputs: { [input]: { type: '.csv', required: true } }, outputs: { [output]: { type: '.csv' } },
  params_schema: {}, executor: 'python',
});
function catalogued(contract: Record<string, unknown>, identity: string) {
  return { ...contract, env: ENV,
           versions: [{ label: 1, identity: identity.repeat(8), registered_at: REGISTERED_AT, env: ENV, newest: true, ...contract }] };
}
const BUILDER_MODULES = {
  [MODULE]: {
    description: 'Score tables: normalize, filter and summarize',
    methods: {
      normalize: catalogued(contractOf('data', 'normalized'), 'a1c4e702'),
      filter_data: catalogued(FILTER_CONTRACT, 'b7d2f915'),
      summarize: catalogued(contractOf('data', 'summary'), 'c3e8a046'),
    },
    drafts: {
      filter_data: { kind: 'version', folder: FOLDER, valid: true, reasons: [], env: ENV, ...FILTER_CONTRACT },
      plot_scores: { kind: 'method', folder: PLOT_FOLDER, valid: false, reasons: [PLOT_REASON],
                     env: 'plots-env', ...PLOT_CONTRACT },
    },
  },
};
const PIPELINE = {
  name: PIPELINE_NAME,
  nodes: [
    { id: 'node_1', type: 'input_selector', method: '', module: '', params: {}, samples: SAMPLES,
      source: 'registered', fan_mode: 'out', keep_going: true, position: { x: 40, y: 240 } },
    { id: 'node_2', type: 'method', method: 'normalize', module: MODULE, params: {}, position: { x: 330, y: 240 } },
    { id: NODE_FILTER, type: 'method', method: 'filter_data', module: MODULE, version: 'draft', folder: FOLDER,
      params: { threshold: 0.5 }, position: { x: 620, y: 240 } },
    { id: NODE_SUMMARIZE, type: 'method', method: 'summarize', module: MODULE, params: {}, position: { x: 910, y: 240 } },
  ],
  links: [
    { source: 'node_1', target: 'node_2', sourceHandle: 'output', targetHandle: 'data' },
    { source: 'node_2', target: NODE_FILTER, sourceHandle: 'normalized', targetHandle: 'data' },
    { source: NODE_FILTER, target: NODE_SUMMARIZE, sourceHandle: 'filtered', targetHandle: 'data' },
  ],
  samples: SAMPLES,
};
// Validate's refusal after filter_data's method.yaml renamed its input.
const REFUSAL = `Node '${NODE_FILTER}': the link from 'node_2' names input 'data', which ${MODULE}.filter_data's `
  + "draft does not declare (its inputs: 'scores'). Link a declared input, or declare 'data' in the "
  + "method's method.yaml.";

interface Doc { nodes: { id: string; type?: string }[]; samples: string[] }

/** The Runs Preview rows: normalize as before, filter_data and summarize new in draft mode. */
function cacheStatusFor(doc: Doc) {
  const rows = [];
  for (const n of doc.nodes.filter(x => x.type === 'method')) {
    for (const s of doc.samples) {
      const draft = n.id === NODE_FILTER || n.id === NODE_SUMMARIZE;
      rows.push({
        key: `${n.id}::${s}::default`, node_id: n.id, sample: s, variant: 'default',
        status: n.id === NODE_FILTER ? 'new_step_changed' : draft ? 'new_upstream_reruns' : 'cached_local',
        reason: null, cache_key: null, draft_mode: draft,
        source_run_id: null, source_nid: null, outputs: [],
      });
    }
  }
  return { rows, blocked_reason: null };
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

/** What the mocked server answers; a test may change it mid-test. */
interface Server { validate: unknown }

async function routes(page: Page, server: Server): Promise<void> {
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const full = new URL(req.url());
    const url = full.pathname;
    const post = req.method() === 'POST';
    // Registry
    if (post && url === '/api/registry/drafts' && full.searchParams.get('dryRun') === 'true') return route.fulfill(json(DRY_RUN));
    if (url === '/api/registry/drafts') return route.fulfill(json({ drafts: [FILTER_DRAFT, PLOT_DRAFT] }));
    if (url === '/api/registry/drafts/snapshot-check') {
      return route.fulfill(json({ drafts: [{ module: MODULE, method: 'filter_data', state: 'current',
                                             contractChanged: false, filesUpdated: true, message: null }] }));
    }
    if (url === `/api/registry/drafts/${MODULE}/filter_data/detail`) {
      return route.fulfill(json({ draft: FILTER_DRAFT, contract: FILTER_CONTRACT, files: [
        { name: 'filter_data.py', language: 'python', content: FILTER_PY },
        { name: 'method.yaml', language: 'yaml', content: FILTER_YAML },
      ] }));
    }
    if (url === `/api/registry/drafts/${MODULE}/plot_scores/detail`) {
      return route.fulfill(json({ draft: PLOT_DRAFT, contract: PLOT_CONTRACT, files: [
        { name: 'plot_scores.py', language: 'python', content: PLOT_PY },
        { name: 'method.yaml', language: 'yaml', content: PLOT_YAML },
      ] }));
    }
    if (url === `/api/registry/drafts/${MODULE}/filter_data/draft-run`) return route.fulfill(json(DRAFT_RUN));
    if (url === `/api/registry/drafts/${MODULE}/plot_scores/draft-run`) {
      return route.fulfill(json({ detail: `${MODULE}.plot_scores has no draft run: it has not run, or its outputs were deleted.` }, 404));
    }
    if (url === '/api/registry/modules') return route.fulfill(json({ modules: REG_MODULES }));
    if (url === '/api/registry/methods') return route.fulfill(json({ methods: REG_METHODS }));
    if (url === '/api/registry/samples') return route.fulfill(json({ samples: [] }));
    if (url === '/api/registry/envs') return route.fulfill(json({ envs: [] }));
    // Builder
    // `?pipeline=demo` loads the one document the canvas boots with; it is my-pipeline.
    if (url === '/api/pipelines/demo') return route.fulfill(json(PIPELINE));
    if (url === '/api/modules') return route.fulfill(json(BUILDER_MODULES));
    if (url === '/api/samples') return route.fulfill(json(SAMPLES));
    if (url === '/api/dev/status') return route.fulfill(json({ dev: false }));
    if (url === '/api/workflow/validate') return route.fulfill(json(server.validate));
    if (url === '/api/wfc/cache-status') return route.fulfill(json(cacheStatusFor(req.postDataJSON() as Doc)));
    // History
    if (url === '/api/wfc/runs') return route.fulfill(json(RUNS));
    if (url === '/api/wfc/pipelines') return route.fulfill(json(SUMMARIES));
    if (url === '/api/wfc/draft-steps') return route.fulfill(json({ steps: STEPS }));
    if (url === '/api/wfc/modules') return route.fulfill(json([MODULE]));
    if (url === '/api/wfc/methods') return route.fulfill(json([]));
    if (url === '/api/wfc/samples') return route.fulfill(json([]));
    if (url === '/api/wfc/status') return route.fulfill(json({ loaded: true, path: '/home/dana/projects/scores-study', modules: 1, runs: RUNS.length }));
    return route.fulfill(json({}));
  });
}

// ---------------------------------------------------------------------------
// Shots
// ---------------------------------------------------------------------------

/** Let fonts, highlighting and layout settle before a shot. */
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

/** One element. */
async function shotElement(target: Locator, name: string): Promise<void> {
  if (!CAPTURE_GALLERY) return;
  await target.scrollIntoViewIfNeeded();
  await settle(target.page());
  await target.screenshot({ path: path.join(OUT, name) });
}

/** An open draft's panel, with a margin, scrolled to the top of the Registry. */
async function shotPanel(panel: Locator, name: string): Promise<void> {
  if (!CAPTURE_GALLERY) return;
  await panel.evaluate(el => el.scrollIntoView({ block: 'start' }));
  await shotRegion(panel.page(), [panel], name, 14);
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

test('the Registry\'s draft screens: the Drafts group, Draft from folder, an open draft, a failed check, the Draft run tab', async ({ page }) => {
  await routes(page, { validate: { valid: true, errors: [], draftRefusals: [] } });
  // The canvas holds my-pipeline, so the toolbar names it as in the Builder shots.
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(4, { timeout: 15_000 });
  await page.getByRole('button', { name: 'Registry', exact: true }).click();
  await page.getByRole('button', { name: /^Methods/ }).click();

  // 1. The Drafts group at the end of Methods.
  const group = page.getByTestId('drafts-group');
  await expect(page.getByTestId('method-row')).toHaveCount(3);
  await expect(group.getByTestId('drafts-count')).toHaveText('2 · each runs its folder as it is now');
  const filterRow = group.getByTestId('draft-row').filter({ hasText: 'filter_data' });
  const plotRow = group.getByTestId('draft-row').filter({ hasText: 'plot_scores' });
  await expect(filterRow.getByTestId('draft-version-chip')).toHaveText('versionDRAFTversion');
  await expect(filterRow.getByTestId('draft-update')).toHaveText('↻ Update');
  await expect(filterRow.getByTestId('draft-invalid')).toHaveCount(0);
  await expect(plotRow.getByTestId('draft-version-chip')).toHaveText('versionDRAFTmethod');
  await expect(plotRow.getByTestId('draft-invalid')).toHaveText('⚠ INVALID');
  await expect(group.getByTestId('draft-from-folder')).toHaveText('+ Draft from folder…');
  await group.scrollIntoViewIfNeeded();
  await expect(group).toBeInViewport();
  await shotPage(page, 'canvas-drafts-group.png');

  // 2. + Draft from folder…: the module, the folder, what it becomes (the dry run).
  await group.getByTestId('draft-from-folder').click();
  const dialog = page.getByTestId('draft-folder-dialog');
  await dialog.getByTestId('draft-module').selectOption(MODULE);
  await dialog.getByTestId('draft-folder').fill('src/my_analysis/filter_data');
  await dialog.getByTestId('draft-folder').press('Enter');
  await expect(dialog.getByTestId('draft-kind-text'))
    .toContainText('This folder becomes its DRAFT version; registering it adds v2.');
  await expect(dialog.getByTestId('draft-create')).toBeEnabled();
  await shotElement(dialog, 'canvas-draft-from-folder.png');
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();

  // 3. filter_data open: its card, its check open with every line ✓, Parameters, its files.
  await filterRow.click();
  let panel = page.getByTestId('draft-panel');
  const card = panel.getByTestId('draft-card');
  await expect(card).toContainText('DRAFT VERSION OF filter_data');
  await expect(card).toContainText('registers as v2');
  await expect(card.getByTestId('draft-files-updated')).toHaveText('Files updated since v1.');
  await expect(card.getByTestId('draft-path')).toHaveAttribute('title', FOLDER);
  await expect(card.getByTestId('draft-register')).toHaveText('Register as v2');
  await expect(card.getByTestId('draft-register')).toBeEnabled();
  await panel.getByTestId('draft-check-toggle').click();
  await expect(panel.getByTestId('draft-check-summary')).toHaveText('✓ 6 ok · nothing written');
  await expect(panel.getByTestId('draft-check-line')).toHaveCount(FILTER_CHECK.length);
  await expect(panel.locator('[data-testid="draft-check-line"][data-ok="false"]')).toHaveCount(0);
  await expect(panel.getByTestId('draft-tab-parameters')).toHaveAttribute('aria-selected', 'true');
  await expect(panel.getByTestId('draft-parameters')).toContainText('threshold');
  await expect(panel.locator('.ftabs button')).toHaveText(['filter_data.py', 'method.yaml']);
  await expect(panel.locator('.ftabs button.on')).toHaveText('filter_data.py');
  await expect(panel.locator('.code')).toContainText('float(row["score"]) >= threshold');
  await shotPanel(panel, 'canvas-draft-panel.png');

  // 8. (taken here, on the same open draft) The Draft run tab, the check closed again.
  await panel.getByTestId('draft-check-toggle').click();
  await expect(panel.getByTestId('draft-check-line')).toHaveCount(0);
  await panel.getByTestId('draft-tab-run').click();
  const tab = panel.getByTestId('draft-run-tab');
  // The run started 12 minutes before the spec loaded; a slow run reads a minute later.
  await expect(tab.getByTestId('draft-run-when')).toHaveText(
    new RegExp(`^1[23] min ago · pipeline ${PIPELINE_NAME} · 3 samples$`));
  await expect(tab.getByTestId('draft-run-status')).toHaveText('✓ 6 completed · latest kept, not recorded');
  await expect(tab.getByTestId('draft-run-size')).toHaveText("1.2 MB · kept until filter_data's draft runs again");
  const nodes = tab.getByTestId('draft-run-node');
  await expect(nodes).toHaveCount(2);
  await expect(nodes.nth(0)).toContainText('filter_data');
  await expect(nodes.nth(0).locator('.pill')).toHaveText('DRAFT');
  await expect(nodes.nth(1)).toContainText('summarize');
  await expect(nodes.nth(1).locator('.pill')).toHaveCount(0);
  await expect(tab.getByTestId('draft-string-copy')).toHaveCount(2);
  for (const id of ['draft-open-history', 'draft-view-pipeline', 'draft-run-zip', 'draft-delete-outputs']) {
    await expect(tab.getByTestId(id)).toBeVisible();
  }
  await shotPanel(panel, 'canvas-draft-run.png');
  await filterRow.click();
  await expect(panel).toHaveCount(0);

  // 4. plot_scores open: its check fails on its env, so Register is disabled.
  await plotRow.click();
  panel = page.getByTestId('draft-panel');
  await expect(panel.getByTestId('draft-card')).toContainText('DRAFT METHOD');
  await expect(panel.getByTestId('draft-check-summary')).toHaveText('✓ 5 ok · ✗ 1 failed · nothing written');
  const failed = panel.locator('[data-testid="draft-check-line"][data-ok="false"]');
  await expect(failed).toHaveCount(1);
  await expect(failed).toContainText('env plots-env is not registered');
  await expect(panel.getByTestId('draft-register')).toHaveText('Register as v1');
  await expect(panel.getByTestId('draft-register')).toBeDisabled();
  await expect(panel.locator('.code')).toContainText('def plot_scores(ctx)');
  await shotPanel(panel, 'canvas-draft-invalid.png');
});

test('the Builder\'s draft screens: the palette\'s Drafts, draft mode, a refused draft node', async ({ page }) => {
  const server: Server = { validate: { valid: true, errors: [], draftRefusals: [] } };
  await routes(page, server);
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(4, { timeout: 15_000 });
  const node = (id: string) => page.locator(`.svelte-flow__node[data-id="${id}"] .custom-node`);

  // 5. The palette's Drafts group, filter_data under my_analysis.
  const palette = page.getByTestId('palette-drafts');
  await expect(palette.locator('.module-name')).toHaveText('▾ Drafts');
  await expect(palette.locator('.draft-sub')).toHaveText([MODULE]);
  await expect(palette.locator('[data-testid="palette-draft"][data-method="filter_data"]')).toContainText('DRAFTversion');
  await expect(palette.locator('[data-testid="palette-draft"][data-method="plot_scores"] .inv')).toHaveText('⚠ INVALID');
  await palette.scrollIntoViewIfNeeded();
  const sidebar = page.locator('.sidebar').first();
  await expect(sidebar).toBeVisible();
  // The sidebar's width, the Drafts group's height.
  if (CAPTURE_GALLERY) {
    const s = (await sidebar.boundingBox())!;
    const g = (await palette.boundingBox())!;
    await settle(page);
    const y = Math.max(0, g.y - 48);
    await page.screenshot({ path: path.join(OUT, 'canvas-draft-palette.png'),
                           clip: { x: s.x, y, width: s.width, height: Math.min(900, g.y + g.height + 16) - y } });
  }

  // 6. Draft mode: filter_data on ● DRAFT, summarize after it, normalize as before; the Inspector on filter_data.
  await expect(node(NODE_FILTER)).toContainText('● DRAFT');
  await expect(node(NODE_FILTER).getByTestId('draft-flag')).toHaveClass(/strong/);
  await expect(node(NODE_SUMMARIZE).getByTestId('draft-flag')).toContainText('DRAFT MODE');
  await expect(node(NODE_SUMMARIZE).getByTestId('draft-flag')).not.toHaveClass(/strong/);
  await expect(node('node_2').getByTestId('draft-flag')).toHaveCount(0);
  await expect(node('node_2')).not.toContainText('DRAFT');
  await page.locator(`.svelte-flow__node[data-id="${NODE_FILTER}"] .node-title`).click();
  await expect(page.locator('.inspector .inspector-version')).toHaveValue('draft');
  await expect(page.locator('.inspector .version-tag-draft')).toContainText('● DRAFT');
  await expect(page.getByTestId('draft-banner')).toContainText('this step and the 1 after it');
  await shotPage(page, 'canvas-draft-mode.png');

  // 7. Validate refuses filter_data: its input `data` is no longer declared by the draft.
  server.validate = { valid: false, errors: [REFUSAL], draftRefusals: [] };
  await page.getByRole('button', { name: 'Validate' }).click();
  const refused = node(NODE_FILTER).locator('.node-footer').getByTestId('node-refused');
  await expect(refused).toHaveText('⚠ refused · 1 issue');
  await expect(refused).toHaveAttribute('title', REFUSAL);
  await expect.poll(() => node(NODE_FILTER).evaluate(el => (el as HTMLElement).style.getPropertyValue('--dash-color')))
    .toBe('var(--status-failed)');
  await expect(page.getByTestId('inspector-refusals').locator('li')).toHaveText([REFUSAL]);
  // The fix may be in the pipeline or in the method's files (this one offers
  // both), so the hint points at both instead of only "here".
  await expect(page.getByTestId('inspector-refusals').locator('.refusal-hint')).toHaveText(
    'Fix it in the pipeline, or in the method\'s files if the message says so, then Validate or Run again.');
  await expect(page.getByTestId('pipeline-error-banner').locator('li')).toHaveText([REFUSAL]);
  await expect(page.getByTestId('pipeline-error-banner').locator('.pe-hint')).toHaveText(
    'Fix each issue in the pipeline, or in the method\'s files if it says so, then click Validate again.');
  // The footer's hover text is the browser's own tooltip, which a screenshot
  // does not draw: the shot pairs the node with the Inspector's list, the same lines.
  await refused.hover();
  await shotRegion(page, [node(NODE_FILTER), page.getByTestId('inspector-refusals')], 'canvas-draft-refused.png', 28, 6);
});

test('History\'s Descendants: normalize\'s recorded runs, then the draft steps of filter_data and summarize', async ({ page }) => {
  await routes(page, { validate: { valid: true, errors: [], draftRefusals: [] } });
  // The canvas holds my-pipeline, so the toolbar names it as in the Builder shots.
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(4, { timeout: 15_000 });
  await page.locator('.tabs button.tab', { hasText: 'History' }).click();
  await page.waitForSelector('.view-switcher', { timeout: 10_000 });
  await page.locator('.view-switcher .seg', { hasText: 'Descendants' }).click();

  // 9. Three recorded normalize runs, each with filter_data's draft step and summarize's after it.
  const recorded = page.locator('.tree-card:not([data-testid="draft-tree-card"])');
  await expect(recorded).toHaveCount(3);
  await expect(recorded.locator('.card-method')).toHaveText(['normalize', 'normalize', 'normalize']);
  const draftCard = (method: string) => page.locator('[data-testid="draft-tree-card"]',
    { has: page.locator('.card-method', { hasText: new RegExp(`^${method}$`) }) });
  await expect(draftCard('filter_data')).toHaveCount(3);
  await expect(draftCard('summarize')).toHaveCount(3);
  await expect(draftCard('filter_data').first().locator('.card-version')).toHaveText('DRAFT');
  await expect(draftCard('filter_data').first().getByTestId('draft-strip')).toHaveClass(/on/);
  await expect(draftCard('summarize').first().getByTestId('draft-strip')).toHaveClass(/down/);
  // filter_data's own tip, over the summarize card below it: the hovered card
  // is drawn above its neighbours, so the tip is the topmost element where it is.
  const strip = draftCard('filter_data').first().getByTestId('draft-strip');
  await strip.hover();
  const tip = strip.locator('.strip-tip');
  await expect(tip).toBeVisible();
  await expect(tip).toContainText("Draft mode: this step runs your method's draft.");
  const box = (await tip.boundingBox())!;
  for (const [x, y] of [[box.x + box.width / 2, box.y + box.height / 2], [box.x + 10, box.y + box.height - 10]]) {
    expect(await page.evaluate(([px, py]) => !!document.elementFromPoint(px, py)?.closest('.strip-tip'), [x, y]),
      `the tip is on top at (${Math.round(x)}, ${Math.round(y)})`).toBe(true);
  }
  await shotPage(page, 'history-draft-runs.png');
});
