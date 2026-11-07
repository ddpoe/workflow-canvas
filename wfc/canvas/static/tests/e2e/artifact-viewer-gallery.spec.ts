/**
 * The run panel's artifact list and viewer, photographed.
 *
 * The run of `fixtures/artifact-run.ts` (an artifact of every kind, a folder,
 * a long name, download-only files, a 16-bit TIFF, an image too large to
 * decode), served through route replay. Each state of the mockup is shown in
 * turn: the list with previews closed, one preview open, a folder after show
 * all, a collapsed category, a cut name's tooltip, the download-only ring's
 * tooltip and the panel widened; the viewer on an image with the amber note
 * and the strip, the TSV and CSV tables, JSON as a tree and Raw, a log with
 * tinted lines, a PDF, "making preview…" and "Too large to preview". The test
 * asserts each state is there on every run, and writes one PNG per state to
 * `gallery/artifact-viewer/`, the committed review gallery. The write is
 * OPT-IN (`CAPTURE_GALLERY`, see playwright.config.ts):
 * `npm run test:e2e:gallery -- artifact-viewer-gallery.spec.ts`.
 */
import { expect, test, type Locator } from '@playwright/test';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CAPTURE_GALLERY } from '../../playwright.config';
import { ARTIFACTS, LONG_CSV_NAME, openRunArtifacts, setupArtifactRun } from './fixtures/artifact-run';

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'gallery', 'artifact-viewer');

test.use({ viewport: { width: 1440, height: 900 } });

test('every artifact list and viewer state, photographed', async ({ page }) => {
  test.setTimeout(90_000);
  // umap.png's viewer sizes answer slowly, so opening it shows "making preview…".
  const fixture = {
    ...ARTIFACTS,
    images: { ...ARTIFACTS.images, 'umap.png': { ...ARTIFACTS.images!['umap.png'], delayMs: 4_000 } },
  };
  await page.addInitScript(() => {
    try { localStorage.removeItem('wfc.history.runDetailWidth'); } catch { /* no storage */ }
  });
  await setupArtifactRun(page, fixture);
  const shot = async (name: string, target?: Locator) => {
    if (!CAPTURE_GALLERY) return;
    await page.waitForTimeout(250);
    if (target) await target.screenshot({ path: path.join(OUT, name) });
    else await page.screenshot({ path: path.join(OUT, name) });
  };
  const panel = page.locator('.detail-panel');
  const row = (name: string) => page.getByTestId('artifact-row').filter({ hasText: name });
  const tip = page.getByTestId('artifact-tip');
  const viewer = page.getByTestId('artifact-viewer');

  await openRunArtifacts(page);
  await expect(page.getByTestId('artifact-preview')).toHaveCount(0);
  await shot('01-list-previews-closed.png', panel);

  await row('umap.png').getByTestId('preview-toggle').click();
  await expect(page.getByTestId('artifact-preview').locator('img:not(.loading)')).toHaveCount(1);
  await shot('02-list-one-preview-open.png', panel);
  await row('umap.png').getByTestId('preview-toggle').click();

  const dirHead = page.locator('.dir-head').filter({ hasText: 'qc_plots/' });
  await dirHead.getByTestId('show-all').click();
  await expect(dirHead.getByTestId('show-all')).toHaveText('hide all');
  await expect(page.locator('.prev-card.in-dir img:not(.loading)')).toHaveCount(3);
  await dirHead.scrollIntoViewIfNeeded();
  await shot('03-list-folder-show-all.png', panel);
  await dirHead.getByTestId('show-all').click();

  await page.getByTestId('category-data').getByTestId('category-toggle').click();
  await expect(page.getByTestId('category-data').getByTestId('artifact-row')).toHaveCount(0);
  await page.getByTestId('category-data').scrollIntoViewIfNeeded();
  await shot('04-list-category-collapsed.png', panel);
  await page.getByTestId('category-data').getByTestId('category-toggle').click();

  await row(LONG_CSV_NAME).locator('.fname').hover();
  await expect(tip).toHaveText(LONG_CSV_NAME);
  await shot('05-list-cut-name-tooltip.png', panel);

  await row('adata_filtered.h5ad').getByTestId('artifact-download').hover();
  await expect(tip).toHaveText('Download only: no preview for this file type');
  await shot('06-list-download-only-tooltip.png', panel);
  await page.mouse.move(10, 10);

  const edge = page.getByTestId('run-detail-edge');
  await edge.focus();
  for (let i = 0; i < 15; i += 1) await page.keyboard.press('ArrowLeft');
  await expect(edge).toHaveAttribute('aria-valuenow', '600');
  await shot('07-panel-widened.png');
  await edge.dblclick();
  await expect(edge).toHaveAttribute('aria-valuenow', '360');

  // The viewer. umap.png first: its view size is slow, so it is still being made.
  await row('umap.png').locator('.name-btn').click();
  await expect(page.getByTestId('viewer-making-preview')).toBeVisible();
  await shot('15-viewer-making-preview.png');
  await expect(page.getByTestId('viewer-making-preview')).toHaveCount(0, { timeout: 10_000 });
  await page.keyboard.press('Escape');

  // The folder is still open from its show all.
  await expect(page.getByRole('button', { name: 'Close folder qc_plots/' })).toBeVisible();
  await row('03_pct_mito_16bit.tif').locator('.name-btn').click();
  await expect(page.getByTestId('viewer-image-note')).toContainText('16-bit shown as 8-bit');
  await expect(page.getByTestId('viewer-strip').getByRole('button')).toHaveCount(4);
  await expect(page.getByTestId('viewer-making-preview')).toHaveCount(0);
  await shot('08-viewer-image-note-and-strip.png');
  await page.keyboard.press('Escape');

  await row('counts.tsv').locator('.name-btn').click();
  await expect(page.getByTestId('viewer-table').locator('tbody tr')).toHaveCount(7);
  await shot('09-viewer-table-tsv.png');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('viewer-name')).toHaveText(LONG_CSV_NAME);
  await expect(page.getByTestId('viewer-table').locator('tbody tr')).toHaveCount(12);
  await shot('10-viewer-table-csv.png');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('viewer-json-tree')).toContainText('leiden_resolution');
  await shot('11-viewer-json-tree.png');
  await viewer.getByRole('button', { name: 'Raw' }).click();
  await expect(page.getByTestId('viewer-code')).toContainText('"converged"');
  await shot('12-viewer-json-raw.png');

  await viewer.getByRole('button', { name: 'Show run.log' }).click();
  await expect(page.getByTestId('viewer-text').locator('.tx.err')).toHaveCount(1);
  await shot('13-viewer-text-log-tinted.png');

  await viewer.getByRole('button', { name: 'Show qc_report.pdf' }).click();
  await expect(page.getByTestId('viewer-pdf')).toBeVisible();
  if (CAPTURE_GALLERY) await page.waitForTimeout(1_500);
  await shot('14-viewer-pdf.png');

  await viewer.getByRole('button', { name: 'Show whole_slide_scan.tif' }).click();
  await expect(page.getByTestId('viewer-too-large'))
    .toContainText('Too large to preview (40000 x 38400). To view it, set [previews] memory_mb to at least 11750');
  await expect(page.getByTestId('viewer-fullscreen')).toHaveCount(0);
  await shot('16-viewer-too-large.png');
});
