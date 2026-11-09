/**
 * The version label, menu and What's new dialog, photographed for review
 * beside the approved mockup (`docs/pev-requests/assets/2026-11-08-version-menu-mockup.html`).
 *
 * The Builder at the mockup's 1280x720 viewport, with the mockup's states:
 * no update (menu open), an update available (the dot), the menu open on
 * the update block, What's new after an upgrade (the centered dialog), and
 * Known issues (the dialog opened at its Known issues). The URL Report a bug
 * opens is asserted as text. Every assertion runs on every run; the PNGs are
 * written to `tests/e2e/gallery/version-menu/` only when the gallery is asked
 * for (`CAPTURE_GALLERY`, see playwright.config.ts): `npm run test:e2e:gallery`.
 */
import { expect, test, type Page } from '@playwright/test';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CAPTURE_GALLERY } from '../../playwright.config';
import {
  ABOUT_FOOTER_TEXT,
  aboutAnswer,
  setupAboutRoutes,
  setupRouteReplay,
  type AboutAnswerOptions,
  type AboutServer,
} from '../../src/lib/__fixtures__/route-replay';

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'gallery', 'version-menu');

const B4 = {
  version: '0.8.0b4', date: '2026-11-08',
  sections: [
    { heading: 'Upgrading', intro: '', items: ['The canvas checks PyPI once a day for a newer wfc; `[updates] check = false` in `wf-canvas.toml` turns it off.'] },
    { heading: 'Added', intro: '', items: ['A `v<version>` menu beside the title: updates, what\'s new, known issues and reporting a bug.'] },
    { heading: 'Fixed', intro: '', items: ['A pipeline can read earlier runs whose outputs are archived, and runs that were cache hits.'] },
    { heading: 'Known issues', intro: 'Each is on the guide\'s Known Issues page with its way around.', items: [
      '`wfc check-pipeline` refuses a node with two inputs when its links use `target_slot`.',
      'A draft can\'t find a helper listed outside its method folder.',
      'History\'s Export runs leaves out a run whose outputs were pruned.',
    ] },
  ],
};
const B3 = {
  version: '0.8.0b3', date: '2026-11-07',
  sections: [
    { heading: 'Upgrading', intro: '', items: [
      'On Windows, keep your project on a local disk: a project on a network drive now gets a warning before a run.',
      'After upgrading, `wfc list-envs` may report leftovers; `wfc prune-envs` clears them.',
    ] },
    { heading: 'Added', intro: '', items: ['`wfc prune-envs` stops and removes what an interrupted run or registration left behind.'] },
  ],
};
const OFFER = {
  version: '0.8.0', prerelease: false,
  sections: [
    { heading: 'Upgrading', intro: '', items: ['Run `wfc doctor` once after upgrading.'] },
    { heading: 'Known issues', intro: '', items: ['A node left on `"version": "draft"` runs the draft without a warning.'] },
  ],
};
const REPORT_URL = 'https://github.com/ddpoe/workflow-canvas/issues/new?body='
  + encodeURIComponent('**What happened**\n\n\n**What I expected**\n\n\n**Steps to reproduce**\n\n\n---\n'
    + 'wfc 0.8.0b4 · Windows-11-10.0.26100-SP0 · Python 3.12.7 · Docker 27.3.1');

async function setup(page: Page, about: AboutServer): Promise<void> {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.route('**/api/**', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  await setupRouteReplay(page, []);
  await setupAboutRoutes(page, about);
}

async function shoot(page: Page, name: string): Promise<void> {
  if (CAPTURE_GALLERY) await page.screenshot({ path: path.join(OUT, `${name}.png`) });
}

test('the version menu gallery: each mockup state', async ({ page, context }) => {
  let opts: AboutAnswerOptions = {
    version: '0.8.0b4', versions: [B4],
    footer: { state: 'prereleases', text: ABOUT_FOOTER_TEXT.prereleases },
  };
  let since: AboutAnswerOptions | null = null;
  const about: AboutServer = {
    answer: s => aboutAnswer(s && since ? since : opts), reportUrl: REPORT_URL, aboutCalls: [], reportCalls: 0,
  };
  await setup(page, about);
  await page.goto('/?fixture=two-methods');
  await page.waitForSelector('.svelte-flow', { timeout: 10_000 });
  const label = page.getByTestId('version-label');

  // 1. No update: the menu open.
  await label.click();
  await expect(page.getByTestId('version-menu')).toBeVisible();
  await expect(page.getByTestId('version-update')).toHaveCount(0);
  await expect(page.getByTestId('known-issues-count')).toHaveText('3');
  await shoot(page, '01-no-update-menu-open');
  await page.keyboard.press('Escape');

  // 2. An update is out: the dot on the label, the tooltip says so.
  opts = { ...opts, offer: OFFER };
  await page.reload();
  await expect(page.getByTestId('version-dot')).toBeVisible();
  await expect(label).toHaveAttribute('title', 'Update available: 0.8.0');
  await shoot(page, '02-update-available');

  // 3. The menu open: the update block on top, What's in 0.8.0 expanded.
  await label.click();
  await expect(page.getByTestId('update-notes')).toBeVisible();
  await shoot(page, '03-menu-open-update');
  await page.keyboard.press('Escape');

  // 4. What's new after an upgrade from 0.8.0b2: the centered dialog.
  opts = { version: '0.8.0b4', versions: [B4], footer: { state: 'prereleases', text: ABOUT_FOOTER_TEXT.prereleases } };
  since = { ...opts, versions: [B4, B3] };
  await page.evaluate(() => localStorage.setItem('wfc.version.lastSeen', '0.8.0b2'));
  await page.reload();
  const dialog = page.getByTestId('whats-new-dialog');
  await expect(dialog).toContainText("What's new since 0.8.0b2");
  await expect(dialog.getByTestId('whats-new-version')).toHaveCount(2);
  await shoot(page, '04-whats-new-after-upgrade');
  await page.getByTestId('whats-new-close').click();

  // 5. Known issues in 0.8.0b4: the dialog opened at its Known issues.
  await label.click();
  await page.getByTestId('menu-known-issues').click();
  await expect(dialog.locator('[data-known-issues]')).toBeInViewport();
  await shoot(page, '05-known-issues');
  await page.getByTestId('whats-new-close').click();

  // Report a bug: the new-issue URL it opens, as text.
  await context.route('https://github.com/**', r => r.fulfill({ status: 200, contentType: 'text/html', body: '<title>new issue</title>' }));
  await label.click();
  const popupPromise = context.waitForEvent('page');
  await page.getByTestId('report-bug').click();
  const popup = await popupPromise;
  await popup.waitForURL(REPORT_URL);
  expect(decodeURIComponent(new URL(popup.url()).searchParams.get('body')!)).toContain(
    'wfc 0.8.0b4 · Windows-11-10.0.26100-SP0 · Python 3.12.7 · Docker 27.3.1');
});
