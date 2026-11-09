/**
 * Draft runs in History (case 22, History part; US-2).
 *
 * Route mocks in the shape of history-pipelines.spec.ts: the History view
 * reads /api/wfc/runs, /api/wfc/pipelines (each summary carries its draft
 * steps, or the cleared line once they are gone), /api/wfc/draft-steps and,
 * when a draft step is opened, /api/wfc/draft-step.
 *
 * The draft `demo.label` ran in pipeline `demo` on ctrl_01: the draft's own
 * step (label) read recorded run r1 (filter_cells); the step after it
 * (summarize) read the draft step. Pipeline `older` had its draft steps
 * cleared by a later draft run.
 */
import { expect, test, type Page } from '@playwright/test';

function mkRun(p: Record<string, unknown>): Record<string, unknown> {
  return {
    id: 'r1', module: 'demo', method: 'filter_cells', version: 1, timestamp: 1_700_000_000_000,
    duration: 1.5, status: 'success', inputs: {}, outputs: {}, outputAvailability: {}, metrics: {},
    dataSource: 'ctrl_01', parentRunIds: [], parents: [], versionIdentity: null, codeFingerprint: null,
    envName: null, envFingerprint: null, gitCommit: null, wfcVersion: null, pipelineDocHash: null,
    keyedUnderEarlierRule: false, experimentId: 'exp1', runName: 'demo/filter_cells', nid: 'v1',
    user: 'tester', favorite: false, pipelineId: 'p_demo', pipelineName: 'demo', scriptPath: null,
    nodeId: 'n1', upstreamRunIds: [], ...p,
  };
}

const RUNS = [
  mkRun({}),
  mkRun({ id: 'r9', pipelineId: 'p_older', pipelineName: 'older', runName: 'older/filter_cells' }),
];

const DRAFT = {
  draftString: 'draft:demo.label/n2@ctrl_01', draft: 'demo.label', isDraft: true,
  module: 'demo', method: 'label', nodeId: 'n2', sample: 'ctrl_01', status: 'success',
  timestamp: 1_700_000_100, duration: 2.4, pipelineId: 'p_demo', pipelineName: 'demo',
  nid: 'v1', version: null, parentRunIds: ['r1'], parentDraftSteps: [],
};
const DOWNSTREAM = {
  ...DRAFT, draftString: 'draft:demo.label/n3@ctrl_01', isDraft: false, method: 'summarize',
  nodeId: 'n3', version: 1, parentRunIds: [], parentDraftSteps: [DRAFT.draftString],
};
const STEPS = [DRAFT, DOWNSTREAM];
const CLEARED = '2 draft steps cleared: replaced by the draft run of 14:31.';

const SUMMARIES = [
  { pipelineId: 'p_demo', name: 'demo', status: 'succeeded', expected: 1, nodeOrder: ['n1', 'n2', 'n3'],
    draftMode: true, drafts: ['demo.label'], draftStepCount: 2, draftSteps: STEPS, cleared: null },
  { pipelineId: 'p_older', name: 'older', status: 'succeeded', expected: 1, nodeOrder: ['n1', 'n2', 'n3'],
    draftMode: false, drafts: [], draftStepCount: 0, draftSteps: [],
    cleared: { count: 2, text: CLEARED, drafts: [{ draft: 'demo.label', reason: 'replaced', at: '2026-11-04T14:31:00Z' }] } },
];

const DETAIL = {
  step: DRAFT, versionText: 'DRAFT', folder: 'C:\\lab\\src\\demo\\label', keptUntil: 'label’s draft runs again',
  params: {}, metrics: {}, inputs: [{ slot: 'data', source: 'run', runId: 'r1', sourceSlot: 'filtered' }],
  outputs: [{ slot: 'labeled', filename: 'labeled.csv', sizeBytes: 2048 }], artifacts: [], log: null,
};

async function json(page: Page, glob: string, body: unknown): Promise<void> {
  await page.route(glob, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }));
}

async function setupRoutes(page: Page): Promise<void> {
  await json(page, '**/api/wfc/pipelines', SUMMARIES);
  await json(page, '**/api/wfc/runs', RUNS);
  await json(page, '**/api/wfc/draft-steps', { steps: STEPS });
  await json(page, '**/api/wfc/draft-step?*', DETAIL);
  await json(page, '**/api/wfc/modules', ['demo']);
  await json(page, '**/api/wfc/methods', []);
  await json(page, '**/api/wfc/status', { loaded: true, path: '/x', modules: 1, runs: RUNS.length });
  await json(page, '**/api/dev/status', { dev: false });
  await json(page, '**/api/samples', []);
}

async function openHistory(page: Page, view: 'Pipelines' | 'Lineages' | 'Descendants'): Promise<void> {
  await setupRoutes(page);
  await page.goto('/?fixture=history-pipelines');
  await page.waitForSelector('.view-switcher', { timeout: 10_000 });
  await page.locator('.view-switcher .seg', { hasText: view }).click();
}

test.describe('History: draft runs (case 22)', () => {
  test('Pipelines lists the DRAFT rows beside the recorded run and one cleared line', async ({ page }) => {
    await openHistory(page, 'Pipelines');
    const demo = page.locator('.pipeline-row', { hasText: 'demo' }).first();
    await expect(demo.locator('[data-testid="draft-mode-pill"]')).toHaveText('draft mode');
    await demo.click();
    const rows = page.locator('[data-testid="draft-row"]');
    await expect(rows).toHaveCount(2);
    await expect(rows.first().locator('[data-testid="draft-pill"]')).toHaveText('● Draft · ok');
    await expect(rows.first().locator('[data-testid="draft-version"]')).toHaveText('● DRAFT');
    await expect(rows.first().locator('[data-testid="draft-copy"]')).toHaveText('draft');
    await expect(rows.first().locator('[data-testid="draft-copy"]')).toHaveAttribute('title', `Copy ${DRAFT.draftString}`);

    await page.locator('.pipeline-row', { hasText: 'older' }).first().click();
    await expect(page.locator('[data-testid="cleared-line"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="cleared-line"]')).toContainText(CLEARED);
    // The tally counts the kept draft steps.
    await expect(page.locator('[data-testid="summary-draft"]')).toContainText('2 draft');
  });

  test('a running draft-mode pipeline shows Stop and every draft status pill whole in its column', async ({ page }) => {
    // Routes added after setupRoutes win: the draft-mode pipeline is running
    // and its draft steps read each status, the longest words included.
    const statuses = ['running', 'pending', 'cancelled', 'failed', 'success'];
    const steps = statuses.map((status, i) => ({
      ...DOWNSTREAM, status, nodeId: `n${i + 3}`, draftString: `draft:demo.label/n${i + 3}@ctrl_01`,
    }));
    await setupRoutes(page);
    await json(page, '**/api/wfc/pipelines', [{ ...SUMMARIES[0], status: 'running', draftSteps: steps }, SUMMARIES[1]]);
    await json(page, '**/api/wfc/draft-steps', { steps });
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/?fixture=history-pipelines');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });
    await page.locator('.view-switcher .seg', { hasText: 'Pipelines' }).click();
    const demo = page.locator('.pipeline-row', { hasText: 'demo' }).first();
    await demo.locator('.name-cell').click();
    await expect(page.locator('[data-testid="draft-pill"]')).toHaveCount(statuses.length);

    // How far an element reaches past the grid cell (column) that holds it.
    const overhang = (sel: string) => page.locator(sel).evaluateAll(els => els.map(el => {
      let cell: Element = el;
      while (cell.parentElement && !/(pipeline|child)-grid/.test(cell.parentElement.className)) cell = cell.parentElement;
      const r = el.getBoundingClientRect(), c = cell.getBoundingClientRect();
      return { text: (el as HTMLElement).innerText, px: Math.max(0, c.left - r.left) + Math.max(0, r.right - c.right) };
    }));
    for (const sel of ['[data-testid="pipeline-stop"]', '[data-testid="pipeline-watch"]', '[data-testid="draft-pill"]']) {
      for (const { text, px } of await overhang(sel)) expect(px, `${text} is cut by ${px}px`).toBeLessThanOrEqual(0.5);
    }
  });

  test('Lineages marks the draft cards with DRAFT MODE and no star or run id', async ({ page }) => {
    await openHistory(page, 'Lineages');
    const cards = page.locator('[data-testid="draft-card"]');
    await expect(cards).toHaveCount(2);
    // A card is picked by its own method name: the downstream card's tip
    // also names the draft's method, so a plain text filter matches both.
    const card = (method: string) =>
      cards.filter({ has: page.locator('.method-name', { hasText: new RegExp(`^${method}$`) }) });
    const onDraft = card('label');
    await expect(onDraft).toHaveCount(1);
    await expect(onDraft.locator('[data-testid="draft-flag"]')).toHaveClass(/strong/);
    await expect(onDraft.locator('[data-testid="draft-nid"]')).toHaveText('draft');
    await expect(cards.locator('.star')).toHaveCount(0);
    await expect(card('summarize')).toHaveCount(1);
    await expect(card('summarize').locator('[data-testid="draft-flag"]')).not.toHaveClass(/strong/);
    // The cards' own fields carry no "not recorded" placeholder. (The
    // downstream card's help tip says its results are "not recorded": that is
    // the draft-mode copy, so the tip is left out of this check.)
    for (const c of [onDraft, card('summarize')]) {
      for (const field of ['.top', '[data-testid="draft-nid"]', '.sample']) {
        await expect(c.locator(field)).not.toContainText('not recorded');
      }
    }
  });

  test('a failed draft-mode step is dashed through the one mask in Lineages and Descendants', async ({ page }) => {
    // Routes added after setupRoutes win: the downstream draft step failed.
    const failed = { ...DOWNSTREAM, status: 'failed' };
    const steps = [DRAFT, failed];
    await setupRoutes(page);
    await json(page, '**/api/wfc/pipelines', [{ ...SUMMARIES[0], status: 'failed', draftSteps: steps }, SUMMARIES[1]]);
    await json(page, '**/api/wfc/draft-steps', { steps });
    await page.goto('/?fixture=history-pipelines');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });
    const maskOf = (sel: string) => page.locator(sel).first().evaluate(el => {
      const before = getComputedStyle(el, '::before');
      return before.maskImage || before.getPropertyValue('-webkit-mask-image');
    });
    const byMethod = (cardSel: string, nameSel: string, method: string) =>
      `${cardSel}:has(${nameSel}:text-is("${method}"))`;

    await page.locator('.view-switcher .seg', { hasText: 'Lineages' }).click();
    const lineageFailed = byMethod('[data-testid="draft-card"]', '.method-name', 'summarize');
    await expect(page.locator(lineageFailed)).toHaveClass(/dash-2/);
    expect(await maskOf(lineageFailed)).toContain('data:image/svg+xml');
    // A succeeded draft-mode step keeps its plain border.
    await expect(page.locator(byMethod('[data-testid="draft-card"]', '.method-name', 'label'))).not.toHaveClass(/dash-2/);

    await page.locator('.view-switcher .seg', { hasText: 'Descendants' }).click();
    const treeFailed = byMethod('[data-testid="draft-tree-card"]', '.card-method', 'summarize');
    await expect(page.locator(treeFailed)).toHaveClass(/dash-2/);
    expect(await maskOf(treeFailed)).toContain('data:image/svg+xml');
  });

  test('an exception border is 2px on recorded and draft-mode cards alike; a succeeded card keeps 1px', async ({ page }) => {
    // Routes added after setupRoutes win: the recorded filter_cells runs and
    // the draft step after the draft failed; the draft's own step succeeded.
    const failed = { ...DOWNSTREAM, status: 'failed' };
    const steps = [DRAFT, failed];
    await setupRoutes(page);
    await json(page, '**/api/wfc/runs', RUNS.map(r => ({ ...r, status: 'failed' })));
    await json(page, '**/api/wfc/pipelines', [{ ...SUMMARIES[0], status: 'failed', draftSteps: steps }, SUMMARIES[1]]);
    await json(page, '**/api/wfc/draft-steps', { steps });
    await page.goto('/?fixture=history-pipelines');
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });
    const widthOf = (sel: string) => page.locator(sel).first().evaluate(el => getComputedStyle(el).borderTopWidth);
    const byMethod = (cardSel: string, nameSel: string, method: string) =>
      `${cardSel}:has(${nameSel}:text-is("${method}"))`;

    await page.locator('.view-switcher .seg', { hasText: 'Lineages' }).click();
    await expect(page.locator(byMethod('.path-node', '.method-name', 'filter_cells'))).not.toHaveCount(0);
    expect(await widthOf(byMethod('.path-node', '.method-name', 'filter_cells'))).toBe('2px');
    expect(await widthOf(byMethod('[data-testid="draft-card"]', '.method-name', 'summarize'))).toBe('2px');
    expect(await widthOf(byMethod('[data-testid="draft-card"]', '.method-name', 'label'))).toBe('1px');

    await page.locator('.view-switcher .seg', { hasText: 'Descendants' }).click();
    await expect(page.locator(byMethod('.tree-card', '.card-method', 'filter_cells'))).not.toHaveCount(0);
    expect(await widthOf(byMethod('.tree-card', '.card-method', 'filter_cells'))).toBe('2px');
    expect(await widthOf(byMethod('[data-testid="draft-tree-card"]', '.card-method', 'summarize'))).toBe('2px');
    expect(await widthOf(byMethod('[data-testid="draft-tree-card"]', '.card-method', 'label'))).toBe('1px');
  });

  test('Descendants draws the strip and DRAFT METHOD pill; the draft step panel refuses the bundle', async ({ page }) => {
    await openHistory(page, 'Descendants');
    // Picked by the card's own method name (the downstream strip's tip names
    // the draft's method too).
    const card = (method: string) => page.locator('[data-testid="draft-tree-card"]',
      { has: page.locator('.card-method', { hasText: new RegExp(`^${method}$`) }) });
    const onDraft = card('label');
    await expect(onDraft).toHaveCount(1);
    await expect(onDraft.locator('[data-testid="draft-strip"]')).toHaveClass(/on/);
    await expect(onDraft.locator('.card-version')).toHaveText('DRAFT');
    await expect(card('summarize')).toHaveCount(1);
    await expect(card('summarize').locator('[data-testid="draft-strip"]')).toHaveClass(/down/);

    await onDraft.click();
    const panel = page.locator('[data-testid="draft-step-panel"]');
    await expect(panel.locator('.crumb')).toHaveText(/History\s*›\s*draft/);
    await expect(panel.locator('[data-testid="draft-string"]')).toHaveText(DRAFT.draftString);
    await expect(panel.locator('[data-testid="draft-method-version"]')).toHaveText('DRAFT');
    await expect(panel.locator('[data-testid="draft-download-zip"]')).toBeVisible();
    const exportRun = panel.locator('[data-testid="draft-export-run"]');
    await expect(exportRun).toBeDisabled();
    await expect(exportRun).toHaveAttribute('title',
      'This lineage has a draft step, label (DRAFT). Register it and run demo again to export a bundle.');
    await expect(panel.locator('[data-testid="draft-open-registry"]')).toHaveText('Open in Registry ↗');
    await expect(panel.locator('[data-testid="draft-view-pipeline"]')).toHaveText('View pipeline ↗');
  });
});
