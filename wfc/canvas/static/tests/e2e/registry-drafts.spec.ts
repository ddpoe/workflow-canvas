/**
 * Browser smoke for the Registry's Drafts group (case 23).
 *
 * The registry endpoints are route-mocked (no FastAPI process) in task 1's
 * draft shapes. The Methods tab ends with the Drafts group; a draft row
 * reads like a method row, marked only by the grey `version | DRAFT | …`
 * chip, with Update and ⋯; an open draft shows its card, the Registration
 * check (a failed one disables Register) and the Parameters | Draft run
 * tabs. The Envs tab's pointer to the command line, the other part of
 * case 23, is witnessed by `registry-module-colour.spec.ts`.
 *
 * The Draft run tab hands its draft run to History by draft string: Open in
 * History ↗ highlights the draft's own step in the Pipelines view, a node
 * line opens that node's draft step in its run panel.
 */
import { expect, test, type Page } from '@playwright/test';

const FOLDER = 'C:\\Users\\dap182\\Documents\\projects\\lab-analysis-2026\\src\\__demo__\\__demo__label';

const MODULES = [
  { name: '__demo__', description: 'Demo pipeline methods shipped with wfc', methods: 1,
    source: 'src/__demo__', color: '#6e5cd6', contracts: [] },
];
const METHODS = [
  { name: '__demo__label', module: '__demo__', env: 'wfc-demo-env', runCount: 0, source: null,
    versions: [{ label: 3, identity: 'a'.repeat(64), registered_at: null, env: 'wfc-demo-env',
                 newest: true, runCount: 0, commit: null, envFingerprint: '', image: '', codeTree: '' }] },
];
const LABEL = {
  module: '__demo__', method: '__demo__label', kind: 'version', folder: FOLDER, env: 'wfc-demo-env',
  newestLabel: 3, registersAs: 4, filesUpdated: true, stale: false, valid: true, reasons: [],
  contract: { inputs: { data: { type: '.csv' } }, outputs: { labeled: { type: '.csv' } },
              params_schema: { k: { type: 'int', default: 3 } }, executor: 'python' },
  check: { ok: true, lines: [{ ok: true, text: '`method.yaml` read · script `label.py` found' }] },
  draftRun: { startedAt: '2026-11-04T14:31:00Z', pipelineId: 'p_demo', pipelineName: 'demo', status: 'running', running: true },
};
const QC = {
  module: '__demo__', method: 'qc_report', kind: 'method', folder: 'src/__demo__/qc_report', env: 'wfc-qc-env',
  newestLabel: null, registersAs: 1, filesUpdated: false, stale: false, valid: false,
  reasons: ['env wfc-qc-env is not registered'], contract: {}, draftRun: null,
  check: { ok: false, lines: [
    { ok: true, text: '`method.yaml` read · script `qc_report.py` found' },
    { ok: false, text: 'env `wfc-qc-env` is not registered: register it with `wfc register-env`' },
  ] },
};
const ref = (node: string, sample: string) => `draft:__demo__.__demo__label/${node}@${sample}`;
/** One step of a Draft run node, as `draft_run_detail` spells it. */
function runStep(node: string, sample: string, status: string, sizeBytes: number | null = null) {
  return { sample, status, sizeBytes, draftString: ref(node, sample) };
}

// A draft run still running, in `draft_run_detail`'s shape: every node lists
// one step per sample, the ones that have not run yet as pending. Sample s1
// has not run anywhere; __demo__plot has run nowhere.
const RUN = {
  module: '__demo__', method: '__demo__label', startedAt: new Date(Date.now() - 120_000).toISOString(),
  pipelineId: 'p_demo', pipelineName: 'demo', samples: ['s1', 's2', 's3'], status: 'running',
  tally: { pending: 5, success: 2, failed: 1, running: 1 }, totalBytes: 2516583,
  keptUntil: "__demo__label's draft runs again",
  nodes: [
    { nodeId: 'n1', module: '__demo__', method: '__demo__label', isDraft: true, status: 'pending', sizeBytes: 1153434,
      steps: [runStep('n1', 's1', 'pending'), runStep('n1', 's2', 'success', 1153434), runStep('n1', 's3', 'failed')] },
    { nodeId: 'n2', module: '__demo__', method: '__demo__summarize', isDraft: false, status: 'running', sizeBytes: 1363149,
      steps: [runStep('n2', 's1', 'pending'), runStep('n2', 's2', 'success', 1363149), runStep('n2', 's3', 'running')] },
    { nodeId: 'n3', module: '__demo__', method: '__demo__plot', isDraft: false, status: 'pending', sizeBytes: 0,
      steps: [runStep('n3', 's1', 'pending'), runStep('n3', 's2', 'pending'), runStep('n3', 's3', 'pending')] },
  ],
};

// History's side of the same draft run: the draft steps that have run, as
// /api/wfc/pipelines summaries, /api/wfc/draft-steps and /api/wfc/draft-step
// report them. The pipeline recorded nothing; it is listed for its draft steps.
const STEP_N1 = {
  draftString: ref('n1', 's2'), draft: '__demo__.__demo__label', isDraft: true,
  module: '__demo__', method: '__demo__label', nodeId: 'n1', sample: 's2', status: 'success',
  timestamp: Date.now() - 120_000, duration: 2.4, pipelineId: 'p_demo', pipelineName: 'demo',
  nid: 'v1', version: null, parentRunIds: [], parentDraftSteps: [],
};
const STEP_N1_S3 = { ...STEP_N1, draftString: ref('n1', 's3'), sample: 's3', status: 'failed' };
const STEP_N2 = {
  ...STEP_N1, draftString: ref('n2', 's2'), isDraft: false, method: '__demo__summarize',
  nodeId: 'n2', version: 1, parentDraftSteps: [STEP_N1.draftString],
};
const STEP_N2_S3 = { ...STEP_N2, draftString: ref('n2', 's3'), sample: 's3', status: 'running', duration: null };
const STEPS = [STEP_N1, STEP_N1_S3, STEP_N2, STEP_N2_S3];
const SUMMARIES = [
  { pipelineId: 'p_demo', name: 'demo', status: 'running', expected: 0, nodeOrder: ['n1', 'n2', 'n3'],
    draftMode: true, drafts: ['__demo__.__demo__label'], draftStepCount: STEPS.length, draftSteps: STEPS, cleared: null },
];
/** The draft-step panel, or the server's 404 for a step that has not run. */
function stepDetail(draftString: string): { status: number; body: Record<string, unknown> } {
  const step = STEPS.find(s => s.draftString === draftString);
  if (!step) return { status: 404, body: { detail: `${draftString} names no draft step.` } };
  return { status: 200, body: { step, versionText: step.isDraft ? 'DRAFT' : 'v1', folder: FOLDER,
           keptUntil: RUN.keptUntil, params: {}, metrics: {}, inputs: [], outputs: [], artifacts: [], log: null } };
}

/** Keep a screenshot for review when `WFC_SHOTS_DIR` names a folder. */
async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.WFC_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

/** The server's delete plan (`DraftDeletePlan`), for Remove draft or Delete outputs. */
function deletePlan(outputsOnly: boolean) {
  const deletes = outputsOnly
    ? ['the draft run of 2026-11-04 14:31 and its results (2.4 MB, 3 steps)', 'draft results shown in pipelines: demo']
    : ['the draft and everything wfc keeps for it: its link to your method folder, its draft-run record of '
         + '2026-11-04 14:31 and its outputs (2.4 MB)',
       "draft results shown in pipelines: demo (they read 'draft removed')"];
  const keeps = outputsOnly
    ? ['the draft itself (its script and method.yaml) — run it again any time',
       'registered versions v1..v3 and their recorded runs', `your method folder ${FOLDER}`]
    : [`your method folder and every file the draft used ${FOLDER} (never touched — start a draft from it again any time)`,
       'registered versions and their recorded runs'];
  const hint = outputsOnly
    ? "Want wfc to forget this draft's script and contract too? Use Remove draft instead."
    : 'Only want to free the results? Use Delete outputs.';
  const title = outputsOnly ? 'Delete the outputs of __demo__/__demo__label' : 'Remove the draft of __demo__/__demo__label';
  const message = [title, 'Deletes:', ...deletes.map(l => `  - ${l}`), 'Keeps:', ...keeps.map(l => `  - ${l}`), hint].join('\n');
  return { module: '__demo__', method: '__demo__label', outputsOnly, folder: '.wfc/drafts/__demo__/__demo__label',
           source: FOLDER, draftRun: 'the draft run of 14:31:00', laterDrafts: [],
           pipelines: [{ pipelineId: 'p_demo', name: 'demo' }], token: outputsOnly ? 'tok-out' : 'tok-rm',
           deletes, keeps, hint, message };
}

async function mockRoutes(page: Page, writes: Array<{ method: string; url: string }>): Promise<void> {
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const full = new URL(req.url());
    const url = full.pathname;
    if (req.method() !== 'GET') writes.push({ method: req.method(), url: url + full.search });
    if (req.method() === 'DELETE' && full.searchParams.get('dryRun') === 'true') {
      const plan = deletePlan(url.endsWith('/draft-run'));
      return route.fulfill(json({ ok: true, message: plan.message, plan }));
    }
    if (req.method() === 'DELETE') return route.fulfill(json({ ok: true, message: 'Deleted.' }));
    if (url === '/api/wfc/runs') return route.fulfill(json([]));
    if (url === '/api/wfc/pipelines') return route.fulfill(json(SUMMARIES));
    if (url === '/api/wfc/draft-steps') return route.fulfill(json({ steps: STEPS }));
    if (url === '/api/wfc/draft-step') {
      const detail = stepDetail(full.searchParams.get('ref') ?? '');
      return route.fulfill(json(detail.body, detail.status));
    }
    if (url === '/api/wfc/modules') return route.fulfill(json(['__demo__']));
    if (url === '/api/wfc/methods') return route.fulfill(json([]));
    if (url === '/api/registry/drafts') return route.fulfill(json({ drafts: [LABEL, QC] }));
    if (url.endsWith('/__demo__label/update')) {
      return route.fulfill(json({ draft: LABEL, changed: true, message: 'The contract changed: 1 param added.' }));
    }
    if (url.endsWith('/__demo__label/draft-run')) return route.fulfill(json(RUN));
    if (url.endsWith('/qc_report/draft-run')) return route.fulfill(json({ detail: 'qc_report has no draft run yet.' }, 404));
    if (url.startsWith('/api/registry/drafts/') && url.endsWith('/detail')) {
      return route.fulfill(json({ draft: LABEL, files: [{ name: 'label.py', language: 'python', content: 'print(1)' }],
                                  contract: LABEL.contract }));
    }
    if (url === '/api/registry/modules') return route.fulfill(json({ modules: MODULES }));
    if (url === '/api/registry/methods') return route.fulfill(json({ methods: METHODS }));
    if (url === '/api/registry/samples') return route.fulfill(json({ samples: [] }));
    if (url === '/api/registry/envs') return route.fulfill(json({ envs: [] }));
    return route.fulfill(json({}));
  });
}

test('the Methods tab ends with the Drafts group: a draft row with Update, its Registration check and its Draft run', async ({ page }) => {
  const writes: Array<{ method: string; url: string }> = [];
  await mockRoutes(page, writes);

  await page.goto('/');
  await page.getByRole('button', { name: 'Registry' }).click();
  await page.getByRole('button', { name: /^Methods/ }).click();

  // The list ends with the Drafts group, sub-grouped by module.
  const group = page.getByTestId('drafts-group');
  await expect(page.locator('.methods-list > :last-child')).toHaveAttribute('data-testid', 'drafts-group');
  await expect(group.getByTestId('drafts-count')).toHaveText('2 · each runs its folder as it is now');
  await expect(group.getByTestId('draft-module-group')).toHaveCount(1);

  // Draft rows read like method rows: the grey chip is the only marker, no dashes.
  const label = page.getByTestId('draft-row').filter({ hasText: '__demo__label' });
  const qc = page.getByTestId('draft-row').filter({ hasText: 'qc_report' });
  await expect(label.getByTestId('draft-version-chip')).toHaveText('versionDRAFTversion');
  await expect(qc.getByTestId('draft-version-chip')).toHaveText('versionDRAFTmethod');
  await expect(label.getByTestId('draft-version-chip')).toHaveCSS('border-top-style', 'solid');
  await expect(label).toHaveCSS('border-top-style', 'solid');
  await expect(qc.getByTestId('draft-invalid')).toHaveText('⚠ INVALID');

  // Update reads method.yaml again and says what changed; it never opens the row.
  await label.getByTestId('draft-update').click();
  await expect(page.getByTestId('draft-notice')).toHaveText('The contract changed: 1 param added.');
  await expect(page.getByTestId('draft-panel')).toHaveCount(0);

  // ⋯ lists Copy path, Refresh from disk, Remove draft.
  await label.getByTestId('draft-menu-button').click();
  const menu = page.getByTestId('draft-menu');
  await expect(menu.getByTestId('menu-copy-path')).toContainText('Copy path');
  await expect(menu.getByTestId('menu-refresh-draft')).toContainText('Refresh from disk');
  await expect(menu.getByTestId('menu-remove-draft')).toContainText('Remove draft');
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();

  // A failed Registration check: its lines ✓ / ✗, nothing written, Register disabled.
  await qc.click();
  let panel = page.getByTestId('draft-panel');
  await expect(panel.getByTestId('draft-card')).toContainText('DRAFT METHOD');
  await expect(panel.getByTestId('draft-check-summary')).toHaveText('✓ 1 ok · ✗ 1 failed · nothing written');
  await expect(panel.getByTestId('draft-check-line')).toHaveCount(2);
  await expect(panel.getByTestId('draft-register')).toBeDisabled();
  await panel.getByTestId('draft-tab-run').click();
  await expect(panel.getByTestId('draft-run-none')).toHaveText('qc_report has no draft run yet.');
  await qc.click();

  // A passing one: the card's path clipped in the middle, the full path on hover, Register enabled.
  await label.click();
  panel = page.getByTestId('draft-panel');
  const card = panel.getByTestId('draft-card');
  await expect(card).toContainText('DRAFT VERSION OF __demo__label');
  await expect(card.getByTestId('draft-files-updated')).toHaveText('Files updated since v3.');
  await expect(card.getByTestId('draft-path')).toHaveText('C:\\…\\src\\__demo__\\__demo__label');
  await expect(card.getByTestId('draft-path')).toHaveAttribute('title', FOLDER);
  await expect(card.getByTestId('draft-register')).toBeEnabled();
  await expect(panel.getByTestId('draft-tab-parameters')).toHaveAttribute('aria-selected', 'true');

  // The Draft run tab: when, status, size and kept-until, one line per node, the actions.
  await panel.getByTestId('draft-tab-run').click();
  const tab = panel.getByTestId('draft-run-tab');
  await expect(tab.getByTestId('draft-run-when')).toHaveText('2 min ago · pipeline demo · 3 samples');
  await expect(tab.getByTestId('draft-run-status')).toHaveText(
    '✓ 2 completed · ✗ 1 failed · ● 1 running · ○ 5 pending · latest kept, not recorded');
  await expect(tab.getByTestId('draft-run-size')).toHaveText("2.4 MB · kept until __demo__label's draft runs again");
  await expect(tab.getByTestId('draft-run-node')).toHaveCount(3);
  await expect(tab.getByTestId('draft-run-zip')).toHaveAttribute('href', '/api/registry/drafts/__demo__/__demo__label/draft-run/zip');
  // Only Update wrote anything.
  expect(writes).toEqual([{ method: 'POST', url: '/api/registry/drafts/__demo__/__demo__label/update' }]);

  // Open in History ↗ leaves the Registry for History.
  await tab.getByTestId('draft-open-history').click();
  await expect(group).toBeHidden();
});

test('the Draft run tab hands the draft run to History: Open in History ↗ highlights it, a node line opens its step', async ({ page }) => {
  await mockRoutes(page, []);
  await page.goto('/');
  await page.getByRole('button', { name: 'Registry' }).click();
  await page.getByRole('button', { name: /^Methods/ }).click();
  await page.getByTestId('draft-row').filter({ hasText: '__demo__label' }).click();
  const tab = page.getByTestId('draft-panel').getByTestId('draft-run-tab');
  await page.getByTestId('draft-panel').getByTestId('draft-tab-run').click();
  await expect(tab.getByTestId('draft-run-node')).toHaveCount(3);

  // Open in History ↗: the Pipelines view, the pipeline open, the draft's own
  // first step that has run highlighted (its s1 step is pending); no run panel.
  await tab.getByTestId('draft-open-history').click();
  const history = page.locator('.history-view');
  const draftRow = (ref: string) => history.locator(`[data-testid="draft-row"][data-draft-string="${ref}"]`);
  await expect(history.locator('.view-switcher .seg.active')).toHaveText('Pipelines');
  await expect(draftRow(STEP_N1.draftString)).toHaveClass(/highlighted/);
  await expect(draftRow(STEP_N2.draftString)).not.toHaveClass(/highlighted/);
  await expect(history.getByTestId('draft-step-panel')).toHaveCount(0);

  // Back in the Registry, a node line opens that node's first step that has
  // run in History, its run panel open.
  await page.getByRole('button', { name: 'Registry' }).click();
  await tab.getByTestId('draft-run-node').filter({ hasText: '__demo__summarize' }).click();
  await expect(draftRow(STEP_N2.draftString)).toHaveClass(/highlighted/);
  const panel = history.getByTestId('draft-step-panel');
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId('draft-string')).toHaveText(STEP_N2.draftString);

  // A node none of whose steps has run does what Open in History ↗ does.
  // (The open draft-step panel has an "Open in Registry ↗" button too.)
  await page.getByRole('button', { name: 'Registry', exact: true }).click();
  await tab.getByTestId('draft-run-node').filter({ hasText: '__demo__plot' }).click();
  await expect(draftRow(STEP_N1.draftString)).toHaveClass(/highlighted/);
  await expect(draftRow(STEP_N2.draftString)).not.toHaveClass(/highlighted/);
});

test('Remove draft and Delete outputs confirm with the server\'s plan and send its token', async ({ page }) => {
  const writes: Array<{ method: string; url: string }> = [];
  await mockRoutes(page, writes);
  await page.goto('/');
  await page.getByRole('button', { name: 'Registry' }).click();
  await page.getByRole('button', { name: /^Methods/ }).click();
  const label = page.getByTestId('draft-row').filter({ hasText: '__demo__label' });
  const dialog = page.getByTestId('draft-confirm');

  // Delete outputs: the dry run's plan, nothing deleted until confirmed.
  await label.click();
  await page.getByTestId('draft-panel').getByTestId('draft-tab-run').click();
  await page.getByTestId('draft-run-tab').getByTestId('draft-delete-outputs').click();
  await expect(dialog.getByTestId('draft-confirm-deletes').getByRole('listitem')).toHaveText(deletePlan(true).deletes);
  await expect(dialog.getByTestId('draft-confirm-keeps').getByRole('listitem')).toHaveText(deletePlan(true).keeps);
  await expect(dialog.getByTestId('draft-confirm-hint')).toHaveText(deletePlan(true).hint);
  await shot(page, 'c-delete-outputs-dialog');
  await dialog.getByTestId('draft-confirm-action').click();
  await expect(dialog).toBeHidden();

  // Remove draft from the row's ⋯ menu: its plan names the pipelines it clears.
  await label.getByTestId('draft-menu-button').click();
  await page.getByTestId('draft-menu').getByTestId('menu-remove-draft').click();
  await expect(dialog.getByTestId('draft-confirm-deletes').getByRole('listitem')).toHaveText(deletePlan(false).deletes);
  await expect(dialog.getByTestId('draft-confirm-keeps').getByRole('listitem')).toHaveText(deletePlan(false).keeps);
  await expect(dialog.getByTestId('draft-confirm-hint')).toHaveText(deletePlan(false).hint);
  await shot(page, 'd-remove-draft-dialog');
  await dialog.getByTestId('draft-confirm-action').click();
  await expect(dialog).toBeHidden();

  const base = '/api/registry/drafts/__demo__/__demo__label';
  expect(writes.filter(w => w.method === 'DELETE').map(w => w.url)).toEqual([
    `${base}/draft-run?dryRun=true`, `${base}/draft-run?planned=tok-out`,
    `${base}?dryRun=true`, `${base}?planned=tok-rm`,
  ]);
});
