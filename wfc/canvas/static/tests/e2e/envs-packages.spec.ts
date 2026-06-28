/**
 * Browser smoke for the Envs-tab Packages panel.
 *
 * Drives the real canvas through the DOM (Builder → Registry → Envs),
 * with the registry endpoints route-mocked (no FastAPI process, per the
 * project's e2e model). Asserts the two panel outcomes in a real
 * browser: a captured env expands to its source-tagged package list, and
 * a byo env expands to the bring-your-own empty state.
 */
import { expect, test } from '@playwright/test';

const ENVS = {
  envs: [
    {
      spec: 'container:analysis',
      methods: ['seg.segment'],
      backend: 'pixi',
      has_packages: true,
      last_run_at: null,
      run_count: 2,
    },
    {
      spec: 'container:vendor',
      methods: ['vendor.run'],
      backend: 'byo',
      has_packages: false,
      last_run_at: null,
      run_count: 0,
    },
  ],
};

const PACKAGES = {
  spec: 'container:analysis',
  backend: 'pixi',
  captured: true,
  packages: [
    { name: 'numpy', version: '1.26.4', source: 'conda' },
    { name: 'pandas', version: '2.1.0', source: 'pip' },
  ],
};

test('Envs tab expands a captured env to its package list and a byo env to the empty state', async ({ page }) => {
  // Single switch handler over /api/** — unambiguous routing (avoids glob
  // precedence races between the list and nested /packages routes). Any
  // unmatched API call gets an empty object so the app still boots.
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/api/registry/envs/') && url.endsWith('/packages')) return route.fulfill(json(PACKAGES));
    if (url.endsWith('/api/registry/envs')) return route.fulfill(json(ENVS));
    if (url.endsWith('/api/registry/modules')) return route.fulfill(json({ modules: [] }));
    if (url.endsWith('/api/registry/methods')) return route.fulfill(json({ methods: [] }));
    if (url.endsWith('/api/registry/samples')) return route.fulfill(json({ samples: [] }));
    return route.fulfill(json({}));
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Registry' }).click();
  await page.getByRole('button', { name: 'Envs' }).click();

  const rows = page.getByTestId('env-row');
  await expect(rows).toHaveCount(2);

  // Captured env → package list. Click the spec cell (the Methods cell
  // stops propagation to show its tooltip, so the row toggle lives on the
  // spec column the user reads first).
  await rows.first().locator('.chip-env').click();
  const panel = page.getByTestId('packages-panel');
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId('package-row')).toHaveCount(2);
  await expect(panel).toContainText('numpy==1.26.4');

  // byo env → bring-your-own empty state.
  await rows.nth(1).locator('.chip-env').click();
  await expect(page.getByTestId('packages-empty')).toContainText('bring-your-own image');
});
