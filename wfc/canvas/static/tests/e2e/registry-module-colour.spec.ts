/**
 * Browser smoke for the Registry Modules tab's Colour picker and the Envs
 * tab's pointer to the command line (part of case 23).
 *
 * The registry endpoints are route-mocked (no FastAPI process). The colour
 * check answers as the server does for a colour too close to the failed
 * red: refused, with the nearest passing shade and its CSS name as
 * separate lowercase words. The canvas runs no colour test itself.
 */
import { expect, test } from '@playwright/test';

const MODULES = [
  { name: '__demo__', description: 'Demo pipeline methods shipped with wfc', methods: 1,
    source: 'src/__demo__', color: '#6e5cd6',
    contracts: [{ type: 'output', name: 'labeled', value_type: '.csv', required: true }] },
];
const METHODS = [
  { name: '__demo__label', module: '__demo__', env: 'wfc-demo-env', runCount: 0, source: null,
    versions: [{ label: 3, identity: 'a'.repeat(64), registered_at: null, env: 'wfc-demo-env',
                 newest: true, runCount: 0, commit: null, envFingerprint: '', image: '', codeTree: '' }] },
];
const REFUSED = {
  ok: false, color: '#e06c75', failedTest: 'distance', reserved: 'failed',
  message: 'Too close to a colour Workflow Canvas uses to show status.',
  suggested: '#b25a7b', suggestedName: 'pale violet red',
};

test('a refused module colour suggests a shade; the saved one shows on the Methods tab; Envs Register points to the CLI', async ({ page }) => {
  const patches: unknown[] = [];
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = req.url();
    if (req.method() === 'POST' && url.endsWith('/api/registry/modules/color-check')) return route.fulfill(json(REFUSED));
    if (req.method() === 'PATCH' && url.endsWith('/api/registry/modules/__demo__')) {
      const body = req.postDataJSON();
      patches.push(body);
      return route.fulfill(json({ ...MODULES[0], color: body.color }));
    }
    if (url.endsWith('/api/registry/modules')) return route.fulfill(json({ modules: MODULES }));
    if (url.endsWith('/api/registry/methods')) return route.fulfill(json({ methods: METHODS }));
    if (url.endsWith('/api/registry/samples')) return route.fulfill(json({ samples: [] }));
    if (url.endsWith('/api/registry/envs')) return route.fulfill(json({ envs: [] }));
    return route.fulfill(json({}));
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Registry' }).click();
  const row = page.getByTestId('module-row').filter({ hasText: '__demo__' });
  await row.getByTestId('module-colour-button').click();
  const picker = page.getByTestId('colour-picker');
  await expect(picker.getByTestId('palette-swatch')).toHaveCount(16);

  // The reasons are collapsed until asked for.
  await expect(picker.getByTestId('reserved-colour').first()).toBeHidden();
  await picker.getByText("Why some colours can't be used").click();
  await expect(picker.getByTestId('reserved-colour')).toHaveCount(7);
  await expect(picker.getByTestId('reserved-colour').first()).toHaveText('green = completed');

  await picker.getByTestId('custom-colour').fill('#E06C75');
  await picker.getByTestId('custom-colour').press('Enter');
  const refusal = picker.getByTestId('colour-refusal');
  await expect(refusal).toContainText('Too close to a colour Workflow Canvas uses to show status.');
  await expect(refusal).toContainText('or pick from the palette.');
  await expect(picker.getByTestId('colour-apply')).toBeDisabled();

  await refusal.getByRole('button', { name: 'Use pale violet red' }).click();
  await expect(picker.getByTestId('custom-colour')).toHaveValue('#b25a7b');
  await picker.getByTestId('colour-apply').click();
  await expect(picker).toBeHidden();
  expect(patches).toEqual([{ color: '#b25a7b' }]);

  // Every view takes the saved colour without a reload.
  await expect(row.locator('.ltag')).toHaveCSS('border-color', 'rgb(178, 90, 123)');
  await page.getByRole('button', { name: /^Methods/ }).click();
  await expect(page.getByTestId('module-heading').locator('.ltag')).toHaveCSS('border-color', 'rgb(178, 90, 123)');

  // Envs: Register points to the command line.
  await page.getByRole('button', { name: /^Envs/ }).click();
  await page.getByRole('button', { name: '+ Register env' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading')).toHaveText('Envs are registered from the command line');
  await expect(dialog.getByTestId('env-cli-command')).toHaveText('wfc register-env --help');
  await expect(dialog.getByRole('link', { name: /Registering an Environment/ })).toHaveAttribute(
    'href', /tutorials\/registering-an-environment\.html$/);
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(dialog).toBeHidden();
});
