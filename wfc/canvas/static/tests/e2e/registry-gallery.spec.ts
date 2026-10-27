/**
 * The Registry's Methods view and its Register modal, photographed.
 *
 * Demo-like data (the TP132 cohort's module, a second module, long param
 * defaults and More fields), served by mocked `/api/registry/*` routes. Each
 * screen the view has is shown in turn: the list, an open method, the method
 * and version ⋯ menus, the version details card, the Register modal's two
 * forms, a refused check, the new-version and new-method confirm cards, and
 * the same-as-newest state. The test asserts each screen is there on every
 * run, and writes one full-window PNG per screen to
 * `tests/e2e/gallery/registry/`, the committed review gallery of the view.
 * The write is OPT-IN (`CAPTURE_GALLERY`, see playwright.config.ts):
 * `npm run test:e2e:gallery`.
 */
import { expect, test, type Page } from '@playwright/test';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CAPTURE_GALLERY } from '../../playwright.config';

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'gallery', 'registry');
const LONG = 'Infiltrating Lobular Carcinoma';

function version(label: number, newest: boolean, commit: string | null, runs: number) {
  return {
    label, identity: (label === 1 ? '498273c2b8f9c99b' : '6b605af8fec3a816').repeat(4),
    registered_at: label === 1 ? '2026-10-21T13:18:00+00:00' : '2026-10-21T17:55:00+00:00',
    env: 'tp132', runCount: runs, newest, commit,
    envFingerprint: '7741df00c4' + '0'.repeat(54),
    image: 'docker://local/tp132-byo@sha256:218f209c' + '1'.repeat(56),
    codeTree: label === 1 ? '66e66d246a' + '2'.repeat(30) : 'e344fd3783' + '3'.repeat(30),
  };
}

const one = (name: string, mod: string, env: string) => ({
  name, module: mod, env, runCount: 3, source: null,
  versions: [{ ...version(1, true, '52ca89ba21' + '4'.repeat(30), 3), env }],
});

const METHODS = [
  one('prepare_cohort', 'tp132', 'tp132'),
  one('deseq2_contrast', 'tp132', 'tp132'),
  one('tf_activity_ulm', 'tp132', 'tp132'),
  { name: 'plot_tf_activity', module: 'tp132', env: 'tp132', runCount: 3, source: null,
    versions: [version(1, false, null, 2), version(2, true, '3b8712511b' + '5'.repeat(30), 1)] },
  one('count_qc', 'qc_tools', 'scanpy'),
  one('mito_filter', 'qc_tools', 'scanpy'),
];

const DETAIL = {
  files: [
    { name: 'plot_tf_activity.py', language: 'python', content:
      '"""Draw the notebook\'s bar plot of the top significant TF activities."""\n\n'
      + 'import matplotlib\nimport decoupler as dc\nimport pandas as pd\nimport wfc_client as wfc\n\n\n'
      + '@wfc.method\ndef plot_tf_activity(ctx):\n    params = ctx.params\n'
      + '    contrast_name = params.get("contrast_name", "ILC_vs_NST")\n    top = int(params.get("top", 30))\n' },
    { name: 'method.yaml', language: 'yaml', content: 'env: tp132\n' },
  ],
  contract: {
    input_slots: { tf_acts: { type: '.csv', required: true, description: 'ULM TF activity scores per sample' } },
    output_slots: { barplot: { type: '.pdf', required: true } },
    params_schema: {
      contrast_name: { type: 'str', default: 'ILC_vs_NST', required: false,
                       description: 'The DESeq2 contrast to plot' },
      subtype: { type: 'str', default: LONG, required: false,
                 description: 'The cohort subtype the plot is limited to; the notebook used the two largest groups' },
      top: { type: 'int', default: 30, required: false, constraints: { min: 1, max: 200 } },
      label_col: { type: 'str', column_of_input: 'tf_acts', new_column: 'label' },
      title: { type: 'str', default: '', required: false },
      minimum_normalised_expression_threshold_per_sample: {
        type: 'float', default: 0.25, required: false,
        description: 'Genes below this normalised expression in a sample are left out of its TF scores' },
    },
    executor: 'python',
  },
  env: 'tp132',
  version: version(2, true, '3b8712511b' + '5'.repeat(30), 1),
};

const CHANGED_PLAN = {
  outcome: 'changed', label: 3, newestLabel: 2, token: 'a'.repeat(64),
  message: 'tp132/plot_tf_activity is already registered (newest v2); this adds v3.',
  changes: {
    code: { changed: ['plot_tf_activity.py'], added: ['utils.py'], removed: [] },
    slots: [
      { name: 'tf_acts', kind: 'input', change: 'changed',
        before: { type: '.csv', required: true }, after: { type: '.csv', required: false } },
      { name: 'barplot_png', kind: 'output', change: 'added', before: null, after: { type: '.png' } },
    ],
    params: [
      { name: 'contrast_name', change: 'removed', before: { type: 'str', default: 'ILC_vs_NST', required: false }, after: null },
      { name: 'n_bins', change: 'changed',
        before: { type: 'int', default: 50, required: false, constraints: { min: 1, max: 100 } },
        after: { type: 'int', default: 50, required: false, constraints: { min: 1, max: 500 } } },
      { name: 'palette', change: 'added', before: null,
        after: { type: 'str', default: 'RdBu', required: false, constraints: { enum: ['RdBu', 'viridis'] } } },
      { name: 'subtype', change: 'changed', before: { type: 'str', default: LONG },
        after: { type: 'str', default: `${LONG} and mixed` } },
      { name: 'top', change: 'changed', before: { type: 'int', default: 30, required: false },
        after: { type: 'int', default: 20, required: false } },
    ],
    run: {},
    env: { before: 'tp132', after: 'tp132', rebuilt: false },
  },
  holds: { script: 'plot_tf_activity.py', helpers: ['utils.py'], ignored: ['notes.md', 'test.csv'] },
};

const NEW_PLAN = {
  outcome: 'new', label: 1, newestLabel: null, token: 'b'.repeat(64),
  message: 'tp132/volcano_plot is not registered yet; this registers it as v1.',
  changes: {
    code: { changed: [], added: ['volcano_plot.py'], removed: [] },
    slots: [
      { name: 'deseq2_results', kind: 'input', change: 'added', before: null,
        after: { type: '.csv', required: true, columns: { strict: ['log2FoldChange', 'padj'] } } },
      { name: 'volcano', kind: 'output', change: 'added', before: null, after: { type: '.pdf' } },
      { name: 'top_genes', kind: 'output', change: 'added', before: null, after: { type: '.csv' } },
    ],
    params: [
      { name: 'padj_cutoff', change: 'added', before: null,
        after: { type: 'float', default: 0.05, required: false, constraints: { min: 0, max: 1 } } },
      { name: 'label_top', change: 'added', before: null, after: { type: 'int', required: true } },
      { name: 'title', change: 'added', before: null, after: { type: 'str', default: LONG, required: false } },
    ],
    run: { executor: [null, 'python'], gpus: [null, false] },
    env: { before: null, after: 'tp132', rebuilt: false },
  },
  holds: { script: 'volcano_plot.py', helpers: [], ignored: ['README.md'] },
};

const SAME_PLAN = { ...CHANGED_PLAN, outcome: 'same', label: 2,
  changes: { ...CHANGED_PLAN.changes, slots: [], params: [], code: { changed: [], added: [], removed: [] } } };

const REFUSED = {
  ok: false,
  preChecks: [
    { status: 'ok', label: 'directory exists' },
    { status: 'fail', label: 'registration plan', detail:
      'Cannot register from src/tp132/plot_tf_activity: it holds more than one script '
      + '(old/plot_v1.py, plot_tf_activity.py, utils.py) and method.yaml does not say which is the '
      + 'main one. Add `script: plot_tf_activity.py` to method.yaml (naming the main script); every '
      + 'other script is then registered as a helper.' },
  ],
};

async function routes(page: Page, dry: () => unknown): Promise<void> {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = req.url();
    if (req.method() === 'POST' && url.endsWith('/api/registry/methods?dryRun=true')) return route.fulfill(json(dry()));
    if (url.includes('/detail')) return route.fulfill(json(DETAIL));
    if (url.endsWith('/api/registry/modules')) {
      return route.fulfill(json({ modules: [
        { name: 'tp132', description: '', contracts: [], methods: 4, source: '' },
        { name: 'qc_tools', description: '', contracts: [], methods: 2, source: '' },
      ] }));
    }
    if (url.endsWith('/api/registry/methods')) return route.fulfill(json({ methods: METHODS }));
    if (url.endsWith('/api/registry/samples')) return route.fulfill(json({ samples: [] }));
    return route.fulfill(json({}));
  });
}

test.use({ viewport: { width: 1440, height: 900 } });

test('every Registry methods screen, photographed', async ({ page }) => {
  let dry: unknown = { ok: true, preChecks: [], plan: CHANGED_PLAN };
  await routes(page, () => dry);
  const shot = async (name: string) => {
    if (!CAPTURE_GALLERY) return;
    await page.waitForTimeout(200);
    await page.screenshot({ path: path.join(OUT, name) });
  };

  await page.goto('/');
  await page.getByRole('button', { name: 'Registry' }).click();
  await page.getByRole('button', { name: /^Methods/ }).click();
  await expect(page.getByTestId('method-row')).toHaveCount(6);
  await shot('01-list.png');

  const plot = page.getByTestId('method-row').filter({ hasText: 'plot_tf_activity' });
  await plot.click();
  await expect(page.getByTestId('contract-table')).toHaveCount(3);
  await page.locator('[data-testid="contract-row"][data-name="contrast_name"]').getByTestId('row-toggle').click();
  await page.locator('[data-testid="contract-row"][data-name="top"]').getByTestId('row-toggle').click();
  await page.locator('[data-testid="contract-row"][data-name="label_col"]').getByTestId('row-toggle').click();
  await page.locator('[data-testid="contract-row"][data-name="subtype"]').getByTestId('cell-default').click();
  await expect(page.getByTestId('default-box-full')).toContainText(LONG);
  // subtype open too: its long description is cut, and opens whole.
  const subtypeRow = page.locator('[data-testid="contract-row"][data-name="subtype"]');
  await subtypeRow.getByTestId('row-toggle').click({ position: { x: 20, y: 8 } });
  await subtypeRow.getByTestId('contract-description').click();
  await expect(page.getByTestId('field-box-full')).toContainText('two largest groups');
  await shot('02-open-method.png');

  // The divider dragged right: the contract is wider and the default no longer cut.
  await page.getByTestId('default-box-close').click();
  await page.getByTestId('field-box-close').click();
  const divider = (await page.getByTestId('split-divider').boundingBox())!;
  await page.mouse.move(divider.x + divider.width / 2, divider.y + 40);
  await page.mouse.down();
  await page.mouse.move(divider.x + 260, divider.y + 40, { steps: 6 });
  await page.mouse.up();
  await expect(page.locator('[data-testid="contract-row"][data-name="subtype"]').getByTestId('cell-default'))
    .toBeDisabled();
  await shot('02b-open-method-wider-contract.png');
  await page.evaluate(() => localStorage.removeItem('wfc.registry.contractWidth'));

  // A long param name: cut on its closed row (whole on hover), whole once the row is open.
  await page.reload();
  await page.getByRole('button', { name: 'Registry' }).click();
  await page.getByRole('button', { name: /^Methods/ }).click();
  await page.getByTestId('method-row').filter({ hasText: 'plot_tf_activity' }).click();
  const params = page.locator('[data-testid="contract-table"][data-kind="param"]');
  const longRow = params.locator('[data-testid="contract-row"][data-name="minimum_normalised_expression_threshold_per_sample"]');
  await expect(longRow.getByTestId('cell-name')).toHaveAttribute('title', 'minimum_normalised_expression_threshold_per_sample');
  if (CAPTURE_GALLERY) await params.screenshot({ path: path.join(OUT, '02c-long-name-closed.png') });
  await longRow.getByTestId('row-toggle').click({ position: { x: 20, y: 8 } });
  await expect(longRow.getByTestId('cell-name')).not.toHaveAttribute('title');
  if (CAPTURE_GALLERY) await params.screenshot({ path: path.join(OUT, '02d-long-name-open.png') });

  // The whole open method on one page: a param and the long-named one open,
  // a long description open too, the divider at its default.
  await params.locator('[data-testid="contract-row"][data-name="top"]').getByTestId('row-toggle').click({ position: { x: 20, y: 8 } });
  const sub = params.locator('[data-testid="contract-row"][data-name="subtype"]');
  await sub.getByTestId('row-toggle').click({ position: { x: 20, y: 8 } });
  await sub.getByTestId('contract-description').click();
  // The Registry scrolls inside its own panel, so a taller window shows it all.
  if (CAPTURE_GALLERY) {
    await page.setViewportSize({ width: 1440, height: 1500 });
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(OUT, '00-open-method-full-page.png') });
    await page.setViewportSize({ width: 1440, height: 900 });
  }

  await plot.getByTestId('method-menu-button').click();
  await expect(page.getByTestId('method-menu')).toBeVisible();
  await shot('03-method-menu.png');
  await page.keyboard.press('Escape');

  await page.getByTestId('version-picker').click();
  await page.getByTestId('version-option').first().hover();
  await page.getByTestId('version-menu-button').nth(1).click();
  await expect(page.getByTestId('version-menu')).toBeVisible();
  await shot('04-version-picker-and-menu.png');
  await page.getByTestId('menu-details').click();
  await expect(page.getByTestId('version-details')).toContainText('not recorded');
  await shot('05-version-details-not-recorded.png');
  await page.keyboard.press('Escape');

  await plot.getByTestId('method-menu-button').click();
  await page.getByTestId('menu-details').click();
  await expect(page.getByTestId('version-details')).toContainText('3b8712511b');
  await shot('06-version-details.png');
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: '+ Register method' }).click();
  const modal = page.getByTestId('register-modal');
  await modal.getByTestId('register-folder').fill('src/tp132/plot_tf_activity');
  await modal.getByTestId('register-module').selectOption('tp132');
  await expect(modal.getByTestId('register-taken')).toBeVisible();
  await shot('07-new-method-name-taken.png');
  await modal.getByTestId('switch-to-version').click();
  await expect(modal.getByTestId('register-now-next')).toContainText('v3');
  await shot('08-new-version-form.png');

  dry = REFUSED;
  await page.getByRole('button', { name: 'Register version', exact: true }).click();
  await expect(modal.getByTestId('register-refusal')).toBeVisible();
  await shot('09-refused.png');

  dry = { ok: true, preChecks: [], plan: CHANGED_PLAN };
  await page.getByRole('button', { name: 'Check again' }).click();
  const card = modal.getByTestId('confirm-card');
  await expect(card).toBeVisible();
  for (const part of ['code', 'slots', 'params']) {
    await card.locator(`[data-testid="confirm-row"][data-part="${part}"]`).getByTestId('confirm-count').click();
  }
  await card.getByTestId('more-toggle').nth(1).click();
  await shot('10-confirm-new-version.png');
  await modal.locator('.modal-body').evaluate(el => { el.scrollTop = el.scrollHeight; });
  await shot('11-confirm-new-version-end.png');

  dry = { ok: true, preChecks: [], plan: SAME_PLAN };
  await modal.getByTestId('register-back').click();
  await page.getByRole('button', { name: 'Register version', exact: true }).click();
  await expect(modal.getByTestId('register-same')).toBeVisible();
  await shot('12-same.png');

  dry = { ok: true, preChecks: [], plan: NEW_PLAN };
  await modal.getByTestId('register-back').click();
  await modal.getByTestId('mode-new').click();
  await modal.getByTestId('register-folder').fill('src/tp132/volcano_plot');
  await page.getByRole('button', { name: 'Register method', exact: true }).click();
  await expect(modal.getByTestId('confirm-card')).toHaveAttribute('data-outcome', 'new');
  await shot('13-confirm-new-method.png');
});
