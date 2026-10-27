/**
 * Browser witness for the register modal's check: the files left out, shown
 * before anything is written.
 *
 * Drives the real canvas through the DOM (Builder → Registry → Methods →
 * Register), with the registry endpoints route-mocked (no FastAPI process,
 * per the project's e2e model). Register method runs the check; its plan
 * comes back as the Registering card, whose "v1 holds" group lists the
 * source files the registered copy leaves out as Ignored. Confirm sends the
 * plan's token back and closes the modal.
 */
import { expect, test } from '@playwright/test';

const PLAN_MESSAGE = 'demo/transform is not registered yet; this registers it as v1.';
const PLAN_TOKEN = 'c'.repeat(64);
const PLAN = {
  outcome: 'new', message: PLAN_MESSAGE, token: PLAN_TOKEN, label: 1, newestLabel: null,
  changes: {
    code: { changed: [], added: ['transform.py'], removed: [] },
    slots: [{ name: 'data', kind: 'input', change: 'added', before: null, after: { type: '.csv', required: true } }],
    params: [],
    run: { executor: [null, 'python'], gpus: [null, false] },
    env: { before: null, after: 'demo-env', rebuilt: false },
  },
  holds: { script: 'transform.py', helpers: [], ignored: ['genes.csv', 'notes.md'] },
};

test('registering a method lists the files the registered copy leaves out before it writes', async ({ page }) => {
  const registerBodies: any[] = [];
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = req.url();
    if (req.method() === 'POST' && url.endsWith('/api/registry/methods?dryRun=true')) {
      return route.fulfill(json({
        ok: true,
        preChecks: [{ status: 'ok', label: 'directory exists' }],
        plan: PLAN,
      }));
    }
    if (req.method() === 'POST' && url.endsWith('/api/registry/methods')) {
      // Register sends back the token of the plan the dry run showed.
      registerBodies.push(req.postDataJSON());
      return route.fulfill(json({
        ok: true,
        preChecks: [{ status: 'ok', label: 'directory exists' }],
        method: { name: 'transform' },
        notCopied: ['genes.csv', 'notes.md'],
        message: PLAN_MESSAGE,
      }));
    }
    if (url.endsWith('/api/registry/modules')) return route.fulfill(json({ modules: [] }));
    if (url.endsWith('/api/registry/methods')) return route.fulfill(json({ methods: [] }));
    if (url.endsWith('/api/registry/samples')) return route.fulfill(json({ samples: [] }));
    return route.fulfill(json({}));
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Registry' }).click();
  await page.getByRole('button', { name: /^Methods/ }).click();
  await page.getByRole('button', { name: '+ Register method' }).click();
  await page.getByTestId('register-folder').fill('src/transform');
  await page.getByTestId('register-module').fill('demo');

  await page.getByRole('button', { name: 'Register method', exact: true }).click();
  const card = page.getByTestId('confirm-card');
  await expect(card).toContainText('Registering');
  await expect(card.getByTestId('holds-script')).toHaveText('transform.py');
  await expect(card.getByTestId('holds-ignored')).toHaveText('genes.csv, notes.md');
  expect(registerBodies).toHaveLength(0);

  await page.getByRole('button', { name: 'Confirm: register v1' }).click();
  await expect(page.getByTestId('register-modal')).toHaveCount(0);
  expect(registerBodies).toHaveLength(1);
  expect(registerBodies[0].plan_token).toBe(PLAN_TOKEN);
});
