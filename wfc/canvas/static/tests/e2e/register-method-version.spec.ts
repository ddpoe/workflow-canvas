/**
 * Browser witness for registering a new version from the Registry, and for
 * nothing spilling out of its cell.
 *
 * Drives the real canvas through the DOM with the registry endpoints
 * route-mocked (no FastAPI process, per the project's e2e model): the
 * method's ⋯ → Register a new version… opens the modal on New method
 * version with the module and method chosen; Register version runs the
 * check; the confirm card shows the change; ← Back returns to the filled
 * form; Confirm sends the checked plan's token and closes. At desktop
 * width the open method's contract tables and the confirm card keep every
 * value inside its own cell, the contract never runs into the code beside
 * it, and the modal's × sits in its header bar, clear of everything else.
 */
import { expect, test, type Locator, type Page } from '@playwright/test';

const LONG = 'Infiltrating Lobular Carcinoma';
const TOKEN = 'd'.repeat(64);

function version(label: number, newest: boolean) {
  return {
    label, identity: `${label}`.repeat(64), registered_at: '2026-10-21T13:18:00+00:00', env: 'tp132',
    runCount: 1, newest, commit: newest ? '3b8712511b' + '0'.repeat(30) : null,
    envFingerprint: 'e'.repeat(64), image: 'docker://local/tp132-byo@sha256:' + 'f'.repeat(64),
    codeTree: `${label}`.repeat(40),
  };
}

const METHOD = { name: 'plot_tf_activity', module: 'tp132', env: 'tp132', runCount: 2, source: null,
                 versions: [version(1, false), version(2, true)] };

const DETAIL = {
  files: [
    { name: 'plot_tf_activity.py', language: 'python', content: 'import wfc_client as wfc\n' },
    { name: 'method.yaml', language: 'yaml', content: 'env: tp132\n' },
  ],
  contract: {
    input_slots: { tf_acts: { type: '.csv', required: true } },
    output_slots: { barplot: { type: '.pdf', required: true } },
    params_schema: {
      subtype: { type: 'str', default: LONG, description: 'The cohort subtype' },
      n_bins: { type: 'int', default: 50, constraints: { min: 1 }, column_of_input: 'tf_acts' },
    },
    executor: 'python',
  },
  env: 'tp132',
  version: version(2, true),
};

const PLAN = {
  outcome: 'changed', label: 3, newestLabel: 2, token: TOKEN,
  message: 'tp132/plot_tf_activity is already registered (newest v2); this adds v3.',
  changes: {
    code: { changed: ['plot_tf_activity.py'], added: [], removed: [] },
    slots: [],
    params: [
      { name: 'subtype', change: 'changed', before: { type: 'str', default: LONG },
        after: { type: 'str', default: `${LONG} (ILC)` } },
      { name: 'n_bins', change: 'changed',
        before: { type: 'int', default: 50, constraints: { min: 1 }, column_of_input: 'tf_acts' },
        after: { type: 'int', default: 50, constraints: { min: 2 }, column_of_input: 'tf_acts' } },
    ],
    run: {},
    env: { before: 'tp132', after: 'tp132', rebuilt: false },
  },
  holds: { script: 'plot_tf_activity.py', helpers: [], ignored: ['notes.md'] },
};

async function routes(page: Page, writes: any[], dryRuns: any[]): Promise<void> {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = req.url();
    if (req.method() === 'POST' && url.endsWith('/api/registry/methods?dryRun=true')) {
      dryRuns.push(req.postDataJSON());
      return route.fulfill(json({ ok: true, preChecks: [{ status: 'ok', label: 'directory exists' }], plan: PLAN }));
    }
    if (req.method() === 'POST' && url.endsWith('/api/registry/methods')) {
      writes.push(req.postDataJSON());
      return route.fulfill(json({ ok: true, preChecks: [], method: { name: METHOD.name }, message: PLAN.message }));
    }
    if (url.includes('/detail')) return route.fulfill(json(DETAIL));
    if (url.endsWith('/api/registry/modules')) {
      return route.fulfill(json({ modules: [{ name: 'tp132', description: '', contracts: [], methods: 1, source: '' }] }));
    }
    if (url.endsWith('/api/registry/methods')) return route.fulfill(json({ methods: [METHOD] }));
    if (url.endsWith('/api/registry/samples')) return route.fulfill(json({ samples: [] }));
    return route.fulfill(json({}));
  });
}

/** Every descendant of `cell` lies inside the cell's own box (1px tolerance). */
async function staysInside(cell: Locator): Promise<boolean> {
  return cell.evaluate((el) => {
    const box = el.getBoundingClientRect();
    return [...el.querySelectorAll('*')].every((child) => {
      const r = child.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return true;
      return r.left >= box.left - 1 && r.right <= box.right + 1;
    });
  });
}

test.use({ viewport: { width: 1440, height: 900 } });

test('New method version from ⋯ through Confirm, nothing spilling out of its cell', async ({ page }) => {
  const writes: any[] = [];
  const dryRuns: any[] = [];
  await routes(page, writes, dryRuns);
  await page.goto('/');
  await page.getByRole('button', { name: 'Registry' }).click();
  await page.getByRole('button', { name: /^Methods/ }).click();

  // The open method: the long default stays in its cell; the contract stays clear of the code.
  await page.getByTestId('method-row').click();
  const subtype = page.locator('[data-testid="contract-row"][data-name="subtype"]');
  await expect(subtype.getByTestId('cell-default')).toContainText(LONG);
  await subtype.getByTestId('row-toggle').click();
  for (const row of await page.getByTestId('contract-row').all()) {
    expect(await staysInside(row)).toBe(true);
  }
  const contractRight = await page.getByTestId('contract-tables').evaluate(el => el.getBoundingClientRect().right);
  const codeLeft = await page.locator('.code-block').evaluate(el => el.getBoundingClientRect().left);
  expect(contractRight).toBeLessThanOrEqual(codeLeft);

  // A default wider than its column is cut and opens whole; ✕ closes it.
  const subtypeDefault = subtype.getByTestId('cell-default');
  await expect(subtypeDefault).toBeEnabled();
  await subtypeDefault.click();
  await expect(subtype.getByTestId('default-box-full')).toHaveText(`'${LONG}'`);
  await subtype.getByTestId('default-box-close').click();
  await expect(subtype.getByTestId('default-box')).toHaveCount(0);
  // Dragged wider, the contract fits the default: it is whole and opens nothing.
  const divider = (await page.getByTestId('split-divider').boundingBox())!;
  await page.mouse.move(divider.x + divider.width / 2, divider.y + 30);
  await page.mouse.down();
  await page.mouse.move(divider.x + 300, divider.y + 30, { steps: 6 });
  await page.mouse.up();
  await expect(subtypeDefault).toBeDisabled();
  await page.evaluate(() => localStorage.removeItem('wfc.registry.contractWidth'));

  // ⋯ → Register a new version… opens with the module and method chosen.
  await page.getByTestId('method-menu-button').click();
  await page.getByTestId('menu-register-version').click();
  const modal = page.getByTestId('register-modal');
  await expect(modal.getByTestId('mode-version')).toHaveAttribute('aria-pressed', 'true');
  await expect(modal.getByTestId('register-method')).toHaveValue('plot_tf_activity');
  await expect(modal.getByTestId('register-now-next')).toContainText('v3');
  await modal.getByTestId('register-folder').fill('src/tp132/plot_tf_activity');

  await page.getByRole('button', { name: 'Register version', exact: true }).click();
  const card = modal.getByTestId('confirm-card');
  await expect(card).toContainText('Changed');
  await card.locator('[data-testid="confirm-row"][data-part="params"]').getByTestId('confirm-count').click();
  await card.getByTestId('more-toggle').first().click();
  await expect(card.getByTestId('more-diff')).toBeVisible();
  for (const cell of await card.getByTestId('ba-cell').all()) {
    expect(await staysInside(cell)).toBe(true);
  }
  for (const more of await card.getByTestId('more-toggle').all()) {
    expect(await staysInside(more.locator('..'))).toBe(true);
  }

  // The × sits in the header bar and overlaps nothing.
  const close = await modal.getByTestId('register-close').boundingBox();
  const header = await modal.locator('.modal-header').boundingBox();
  const toggle = await modal.getByTestId('register-mode').boundingBox();
  expect(close!.y).toBeGreaterThanOrEqual(header!.y);
  expect(close!.y + close!.height).toBeLessThanOrEqual(header!.y + header!.height);
  expect(close!.y + close!.height).toBeLessThanOrEqual(toggle!.y);

  // ← Back keeps every choice; Check again and Confirm apply the checked plan.
  await modal.getByTestId('register-back').click();
  await expect(modal.getByTestId('register-folder')).toHaveValue('src/tp132/plot_tf_activity');
  await page.getByRole('button', { name: 'Register version', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm: register v3' }).click();
  await expect(modal).toHaveCount(0);
  expect(dryRuns).toHaveLength(2);
  expect(writes).toEqual([{ directory: 'src/tp132/plot_tf_activity', module: 'tp132',
                            method_name: 'plot_tf_activity', plan_token: TOKEN }]);
});
