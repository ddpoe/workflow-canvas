/**
 * The version label, its menu and the What's new dialog, in the browser.
 *
 * `/api/about` and `/api/about/report` are mocked (`setupAboutRoutes`); a
 * test changes the answer between loads to play an update coming out, the
 * user seeing it, and an upgrade. Each test's browser storage starts empty.
 * Request cases: 9 (What's new after an upgrade, never on a first load),
 * 10 (the label and its dot), 11 (What's in <v>, canvas half), 12 (the URL
 * Report a bug opens, canvas half), 13 (the Toolbar on one line at 1280 px)
 * and 14 (the footer's sentences and link); a failing about route; and,
 * below the 1280 px layout (1100 and 1024 px), every Toolbar action and the
 * version label on-screen and clickable.
 */
import { expect, test, type Page } from '@playwright/test';
import {
  ABOUT_FOOTER_TEXT,
  ABOUT_LINKS,
  aboutAnswer,
  setupAboutRoutes,
  setupRouteReplay,
  type AboutAnswerOptions,
  type AboutServer,
} from '../../src/lib/__fixtures__/route-replay';

const LAST_SEEN = 'wfc.version.lastSeen';

function server(answer: AboutServer['answer']): AboutServer {
  return { answer, aboutCalls: [], reportCalls: 0 };
}

/** Every route answered: a catch-all, the replay defaults, then the about routes. */
async function routes(page: Page, about: AboutServer): Promise<void> {
  await page.route('**/api/**', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  await setupRouteReplay(page, []);
  await setupAboutRoutes(page, about);
}

async function open(page: Page, path = '/'): Promise<void> {
  await page.goto(path);
  await page.waitForSelector('.svelte-flow', { timeout: 10_000 });
}

const entry = (version: string, date: string) => ({
  version, date,
  sections: [
    { heading: 'Upgrading', intro: '', items: [`Upgrading note for ${version}.`] },
    { heading: 'Fixed', intro: '', items: [`A fix in ${version}.`] },
    { heading: 'Known issues', intro: '', items: [`Issue one of ${version}.`, `Issue two of ${version}.`] },
  ],
});

test.describe('What\'s new after an upgrade (case 9)', () => {
  test('a first-ever load records the version silently; a changed version opens the dialog once with every version since', async ({ page }) => {
    let installed = '0.8.0b2';
    const about = server(since => installed === '0.8.0b4' && since === '0.8.0b2'
      ? aboutAnswer({ version: installed, versions: [entry('0.8.0b4', '2026-11-08'), entry('0.8.0b3', '2026-11-07')] })
      : aboutAnswer({ version: installed, versions: [entry(installed, '2026-11-08')] }));
    await routes(page, about);

    // First-ever load: no dialog, the installed version recorded.
    await open(page);
    await expect(page.getByTestId('version-label')).toContainText('v0.8.0b2');
    await expect(page.getByTestId('whats-new-dialog')).toHaveCount(0);
    expect(await page.evaluate(k => localStorage.getItem(k), LAST_SEEN)).toBe('0.8.0b2');

    // The user upgrades and restarts the canvas: the next load opens What's new.
    installed = '0.8.0b4';
    await page.reload();
    const dialog = page.getByTestId('whats-new-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("What's new since 0.8.0b2");
    await expect(dialog.getByTestId('whats-new-version')).toHaveText([/0\.8\.0b4/, /0\.8\.0b3/]);
    // Upgrading first and Known issues last, in each version.
    const headings = await dialog.getByTestId('changelog-heading').allTextContents();
    expect(headings).toEqual(['Upgrading', 'Fixed', 'Known issues', 'Upgrading', 'Fixed', 'Known issues']);
    expect(about.aboutCalls).toContain('/api/about?since=0.8.0b2');

    // Centered, over a scrim that dims the canvas.
    const box = (await dialog.boundingBox())!;
    const width = page.viewportSize()!.width;
    expect(Math.abs(box.x + box.width / 2 - width / 2)).toBeLessThan(2);
    await expect(page.getByTestId('whats-new-scrim')).toBeVisible();
    expect(await page.getByTestId('whats-new-scrim').evaluate(el => getComputedStyle(el).backgroundColor))
      .toMatch(/rgba\(0, 0, 0, 0\.4\d*\)/);
    expect(await page.evaluate(k => localStorage.getItem(k), LAST_SEEN)).toBe('0.8.0b4');

    // Closed, and a reload does not open it again.
    await page.getByTestId('whats-new-close').click();
    await expect(dialog).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId('version-label')).toContainText('v0.8.0b4');
    await expect(page.getByTestId('whats-new-dialog')).toHaveCount(0);
  });

  test('from the menu, What\'s new shows the installed version only, and Known issues opens it at Known issues', async ({ page }) => {
    const long = entry('0.8.0b4', '2026-11-08');
    long.sections[1].items = Array.from({ length: 40 }, (_, i) => `Fix number ${i + 1}.`);
    await routes(page, server(() => aboutAnswer({ version: '0.8.0b4', versions: [long] })));
    await open(page);

    await page.getByTestId('version-label').click();
    await expect(page.getByTestId('known-issues-count')).toHaveText('2');
    await page.getByTestId('menu-whats-new').click();
    const dialog = page.getByTestId('whats-new-dialog');
    await expect(dialog).toContainText("What's new in 0.8.0b4");
    await expect(dialog.getByTestId('whats-new-version')).toHaveCount(1);
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);

    await page.getByTestId('version-label').click();
    await page.getByTestId('menu-known-issues').click();
    const ki = dialog.locator('[data-known-issues]');
    await expect(ki).toBeInViewport();
    await expect(dialog.getByTestId('ways-around')).toHaveAttribute('href', ABOUT_LINKS.known_issues);
  });
});

test.describe('the label and its dot (case 10)', () => {
  test('the dot shows for an unseen offer, goes once the menu is opened, and returns for a newer one; the update block stays until the upgrade', async ({ page }) => {
    let opts: AboutAnswerOptions = { version: '0.8.0' };
    await routes(page, server(() => aboutAnswer(opts)));
    await open(page);
    const label = page.getByTestId('version-label');
    await expect(label).toContainText('v0.8.0');
    await expect(page.getByTestId('version-dot')).toHaveCount(0);

    // The label shows on every tab.
    for (const tab of ['Registry', 'History', 'Builder']) {
      await page.locator('.tab', { hasText: tab }).click();
      await expect(label).toBeVisible();
    }

    // 0.8.1 is out: the dot, and the tooltip saying so.
    opts = { version: '0.8.0', offer: { version: '0.8.1' } };
    await page.reload();
    await expect(page.getByTestId('version-dot')).toBeVisible();
    await expect(label).toHaveAttribute('title', 'Update available: 0.8.1');

    // Opening the menu shows the update and records it as seen: the dot goes.
    await label.click();
    await expect(page.getByTestId('version-update')).toContainText('Update available: 0.8.1');
    await expect(page.getByTestId('update-command')).toHaveText('pip install -U workflow-canvas');
    await expect(page.getByTestId('update-plugin')).toHaveAttribute('href', ABOUT_LINKS.plugin_install);
    await expect(page.getByTestId('version-dot')).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('version-menu')).toHaveCount(0);

    // Not upgraded: after a reload the dot stays gone, the update block stays.
    await page.reload();
    await expect(label).toContainText('v0.8.0');
    await expect(page.getByTestId('version-dot')).toHaveCount(0);
    await label.click();
    await expect(page.getByTestId('version-update')).toBeVisible();
    await page.mouse.click(900, 500);
    await expect(page.getByTestId('version-menu')).toHaveCount(0);

    // A newer beta: the dot is back, with the pinned command.
    opts = { version: '0.8.0', offer: { version: '0.8.2b1', prerelease: true } };
    await page.reload();
    await expect(page.getByTestId('version-dot')).toBeVisible();
    await label.click();
    await expect(page.getByTestId('update-command')).toHaveText('pip install workflow-canvas==0.8.2b1');

    // Upgraded: no offer, no update block.
    opts = { version: '0.8.2b1' };
    await page.reload();
    await expect(label).toContainText('v0.8.2b1');
    await label.click();
    await expect(page.getByTestId('version-menu')).toBeVisible();
    await expect(page.getByTestId('version-update')).toHaveCount(0);
  });

  test('the page asks again when the tab is shown again', async ({ page }) => {
    let opts: AboutAnswerOptions = { version: '0.8.0' };
    const about = server(() => aboutAnswer(opts));
    await routes(page, about);
    await open(page);
    await expect(page.getByTestId('version-label')).toBeVisible();
    const asked = about.aboutCalls.length;

    opts = { version: '0.8.0', offer: { version: '0.8.1' } };
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.getByTestId('version-dot')).toBeVisible();
    expect(about.aboutCalls.length).toBeGreaterThan(asked);
  });

  test('a failing about route leaves the Toolbar as it was: no dot, no error', async ({ page }) => {
    await routes(page, server(() => null));
    await open(page);
    await expect(page.locator('.toolbar .title')).toHaveText('Workflow Canvas');
    await expect(page.getByTestId('version-dot')).toHaveCount(0);
    await expect(page.getByTestId('version-label')).toHaveCount(0);
    await expect(page.getByTestId('whats-new-dialog')).toHaveCount(0);
    await expect(page.locator('.flash-toast, .pipeline-error-banner')).toHaveCount(0);
  });
});

test.describe('What\'s in <v> (case 11, canvas half)', () => {
  test('with the public changelog fetched it expands to Upgrading and Known issues; otherwise it is a link', async ({ page }) => {
    let opts: AboutAnswerOptions = {
      version: '0.8.0',
      offer: { version: '0.8.1', sections: [
        { heading: 'Upgrading', intro: '', items: ['Run `wfc doctor` once after upgrading.'] },
        { heading: 'Known issues', intro: '', items: ['A **draft** node runs without a warning.'] },
      ] },
    };
    await routes(page, server(() => aboutAnswer(opts)));
    await open(page);
    await page.getByTestId('version-label').click();
    const notes = page.getByTestId('update-notes');
    await expect(page.getByTestId('update-notes-toggle')).toContainText("What's in 0.8.1");
    await expect(notes.getByTestId('changelog-heading')).toHaveText(['Upgrading', 'Known issues']);
    await expect(notes.locator('code')).toHaveText('wfc doctor');
    await expect(notes.locator('strong')).toHaveText('draft');
    await expect(notes.getByRole('link', { name: /All of 0\.8\.1's notes/ })).toHaveAttribute('href', ABOUT_LINKS.changelog);
    await page.getByTestId('update-notes-toggle').click();
    await expect(notes).toHaveCount(0);

    opts = { version: '0.8.0', offer: { version: '0.8.1' } };
    await page.reload();
    await page.getByTestId('version-label').click();
    await expect(page.getByTestId('update-notes-toggle')).toHaveCount(0);
    await expect(page.getByTestId('update-notes-link')).toHaveText("What's in 0.8.1");
    await expect(page.getByTestId('update-notes-link')).toHaveAttribute('href', ABOUT_LINKS.changelog);
  });
});

test.describe('Report a bug (case 12, canvas half)', () => {
  test('asks the report route only when clicked, and opens exactly the URL it answers in a new tab', async ({ page, context }) => {
    const reportUrl = 'https://github.com/ddpoe/workflow-canvas/issues/new?body=%2A%2AWhat+happened%2A%2A%0A%0Awfc+0.8.0+%C2%B7+Windows-11+%C2%B7+Python+3.12.7+%C2%B7+Docker+not+running';
    const about = { ...server(() => aboutAnswer()), reportUrl };
    await context.route('https://github.com/**', r => r.fulfill({ status: 200, contentType: 'text/html', body: '<title>new issue</title>' }));
    await routes(page, about);
    await open(page);

    await page.getByTestId('version-label').click();
    await expect(page.getByTestId('version-menu')).toBeVisible();
    expect(about.reportCalls).toBe(0);

    const popupPromise = context.waitForEvent('page');
    await page.getByTestId('report-bug').click();
    const popup = await popupPromise;
    await popup.waitForURL(reportUrl);
    expect(popup.url()).toBe(reportUrl);
    expect(about.reportCalls).toBe(1);
  });
});

test.describe('the footer (case 14)', () => {
  for (const state of ['stable', 'prereleases', 'off'] as const) {
    test(`reads the ${state} sentence and links the [updates] guide section`, async ({ page }) => {
      await routes(page, server(() => aboutAnswer({ footer: { state, text: ABOUT_FOOTER_TEXT[state] } })));
      await open(page);
      await page.getByTestId('version-label').click();
      const foot = page.getByTestId('version-footer');
      await expect(foot).toHaveText(ABOUT_FOOTER_TEXT[state].replaceAll('`', ''));
      await expect(foot.getByTestId('footer-link')).toHaveText('wf-canvas.toml');
      await expect(foot.getByTestId('footer-link')).toHaveAttribute('href', ABOUT_LINKS.updates_guide);
    });
  }

  test('an invalid [updates] shows the refusal, with no link', async ({ page }) => {
    const text = "wf-canvas.toml [updates] check must be true or false, not 'yes'";
    await routes(page, server(() => aboutAnswer({ footer: { state: 'invalid', text } })));
    await open(page);
    await page.getByTestId('version-label').click();
    await expect(page.getByTestId('version-footer')).toHaveText(text);
    await expect(page.getByTestId('footer-link')).toHaveCount(0);
  });
});

// ---------- Case 13: the Toolbar at 1280 px, an offer, Run in draft mode ----------

const V1 = '1'.repeat(40);
const CONTRACT = { inputs: { data: { type: 'csv', required: true } }, outputs: { out: { type: 'csv' } }, params_schema: {}, executor: 'python' };
const registered = { ...CONTRACT, env: 'demo', versions: [{ label: 1, identity: V1, registered_at: '2026-10-01T09:00:00Z', env: 'demo', newest: true, ...CONTRACT }] };
const DRAFT_PIPELINE = {
  name: 'A pipeline name long enough to fill the name box',
  nodes: [
    { id: 'node_1', type: 'input_selector', method: '', module: '', params: {}, samples: ['s1'], source: 'registered', fan_mode: 'out', keep_going: true, position: { x: 40, y: 220 } },
    { id: 'node_2', type: 'method', method: 'label', module: 'demo', version: 'draft', folder: 'methods/label', params: {}, position: { x: 300, y: 220 } },
  ],
  links: [{ source: 'node_1', target: 'node_2', sourceHandle: 'output', targetHandle: 'data' }],
  samples: ['s1'],
};
const DRAFT_MODULES = {
  demo: {
    description: 'Demo module',
    methods: { label: registered },
    drafts: { label: { kind: 'version', folder: 'methods/label', valid: true, reasons: [], env: 'demo', ...CONTRACT } },
  },
};

test('at 1280 px with an offer and Run in draft mode the Toolbar is one line, no button wraps, the name box is 240 px (case 13)', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await routes(page, server(() => aboutAnswer({ version: '0.8.0b4', offer: { version: '0.8.0b5', prerelease: true } })));
  await page.route('**/api/pipelines/demo', r => r.fulfill(json(DRAFT_PIPELINE)));
  await page.route('**/api/modules', r => r.fulfill(json(DRAFT_MODULES)));
  await page.route('**/api/wfc/cache-status', r => r.fulfill(json({ rows: [], blocked_reason: null })));
  await page.route('**/api/samples', r => r.fulfill(json(['s1'])));
  await page.route('**/api/wfc/samples', r => r.fulfill(json([])));
  await page.route('**/api/dev/status', r => r.fulfill(json({ dev: false })));
  await page.route('**/api/registry/drafts/snapshot-check', r => r.fulfill(json({
    drafts: [{ module: 'demo', method: 'label', state: 'current', contractChanged: false, filesUpdated: false, message: null }],
  })));
  await page.goto('/?pipeline=demo');
  await expect(page.locator('.svelte-flow__node')).toHaveCount(2, { timeout: 15_000 });
  await expect(page.locator('.btn-run')).toContainText('draft mode');
  await expect(page.getByTestId('version-dot')).toBeVisible();

  const toolbar = page.locator('.toolbar');
  const layout = await toolbar.evaluate(bar => {
    const kids = [...bar.children].map(el => el.getBoundingClientRect());
    const buttons = [...bar.querySelectorAll('button')].map(b => {
      const r = b.getBoundingClientRect();
      const lh = parseFloat(getComputedStyle(b).lineHeight) || parseFloat(getComputedStyle(b).fontSize) * 1.3;
      const pad = parseFloat(getComputedStyle(b).paddingTop) + parseFloat(getComputedStyle(b).paddingBottom);
      return { text: b.textContent?.trim() ?? '', lines: Math.round((r.height - pad) / lh), overflow: b.scrollWidth > b.clientWidth + 1 };
    });
    const name = bar.querySelector('.pipeline-name')!.getBoundingClientRect();
    return {
      height: bar.getBoundingClientRect().height,
      overflow: bar.scrollWidth > bar.clientWidth + 1,
      centres: kids.map(r => r.top + r.height / 2),
      buttons,
      nameWidth: name.width,
    };
  });
  // One line: every item sits on the same row, nothing spills past the edge.
  expect(layout.overflow).toBe(false);
  expect(Math.max(...layout.centres) - Math.min(...layout.centres)).toBeLessThan(8);
  expect(layout.height).toBeLessThan(62);
  for (const b of layout.buttons) {
    expect(b.lines, `${b.text} wraps`).toBeLessThanOrEqual(1);
    expect(b.overflow, `${b.text} is cut`).toBe(false);
  }
  // The name box is set to 240 px and is the one item that gives up width
  // (to 180 px at least): with the dot and "· draft mode" it gives up a few.
  expect(layout.nameWidth).toBeLessThanOrEqual(240);
  expect(layout.nameWidth).toBeGreaterThanOrEqual(180);
});

test('with no offer and no draft the name box keeps its full 240 px at 1280 px (case 13)', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await routes(page, server(() => aboutAnswer({ version: '0.8.0b4' })));
  await open(page);
  await expect(page.getByTestId('version-label')).toBeVisible();
  expect((await page.locator('.pipeline-name').boundingBox())!.width).toBe(240);
});

// ---------- Below the 1280 px layout: every action stays on-screen ----------

for (const width of [1100, 1024]) {
  test(`at ${width} px with an offer and Run in draft mode every Toolbar action and the version label are on-screen and clickable`, async ({ page }) => {
    await page.setViewportSize({ width, height: 720 });
    const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    await routes(page, server(() => aboutAnswer({ version: '0.8.0b4', offer: { version: '0.8.0b5', prerelease: true } })));
    await page.route('**/api/pipelines/demo', r => r.fulfill(json(DRAFT_PIPELINE)));
    await page.route('**/api/modules', r => r.fulfill(json(DRAFT_MODULES)));
    await page.route('**/api/wfc/cache-status', r => r.fulfill(json({ rows: [], blocked_reason: null })));
    await page.route('**/api/samples', r => r.fulfill(json(['s1'])));
    await page.route('**/api/wfc/samples', r => r.fulfill(json([])));
    await page.route('**/api/dev/status', r => r.fulfill(json({ dev: false })));
    await page.route('**/api/registry/drafts/snapshot-check', r => r.fulfill(json({
      drafts: [{ module: 'demo', method: 'label', state: 'current', contractChanged: false, filesUpdated: false, message: null }],
    })));
    await page.goto('/?pipeline=demo');
    await expect(page.locator('.svelte-flow__node')).toHaveCount(2, { timeout: 15_000 });
    await expect(page.locator('.btn-run')).toContainText('draft mode');

    const toolbar = page.locator('.toolbar');
    const targets = {
      Import: toolbar.getByRole('button', { name: 'Import', exact: true }),
      Validate: toolbar.getByRole('button', { name: 'Validate', exact: true }),
      Export: toolbar.getByRole('button', { name: 'Export', exact: true }),
      Run: toolbar.locator('.btn-run'),
      Clear: toolbar.getByRole('button', { name: 'Clear', exact: true }),
      Undo: toolbar.getByTitle('Undo (Ctrl+Z)'),
      Redo: toolbar.getByTitle('Redo (Ctrl+Y)'),
      'version label': page.getByTestId('version-label'),
    };
    for (const [label, target] of Object.entries(targets)) {
      await expect(target, `${label} is missing`).toHaveCount(1);
      // Whole inside the viewport, and the topmost element at its centre is
      // the control itself: a click lands on it with nothing scrolled.
      const placed = await target.evaluate(el => {
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return {
          inside: r.left >= 0 && r.top >= 0 && r.right <= window.innerWidth && r.bottom <= window.innerHeight,
          hit: hit !== null && (hit === el || el.contains(hit)),
          box: [Math.round(r.left), Math.round(r.right)],
        };
      });
      expect(placed.inside, `${label} at ${placed.box} is outside the ${width} px viewport`).toBe(true);
      expect(placed.hit, `${label} is covered`).toBe(true);
      await target.click({ trial: true, timeout: 2_000 });
    }
  });
}
