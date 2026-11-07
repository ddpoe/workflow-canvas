/**
 * The run panel's artifact viewer, end to end in the browser.
 *
 * A finished run with an artifact of every kind (`fixtures/artifact-run.ts`),
 * its listing, file, preview and image routes served through route replay.
 * From the Artifacts tab: a row opens the viewer, which steps through a
 * folder's viewable files by arrow button and key, skipping download-only
 * ones, and closes on Esc with focus back on the row; the image zoom
 * controls and full screen act; the table, JSON, text and PDF bodies render;
 * a folder's DIR pill with a 3- or 4-digit count overlaps nothing at 360 px;
 * a row's arrow sits halfway between the arrow above it and the row's
 * icon, with icons, names and the tail column aligned, at 360 px;
 * the panel's left edge drags, steps by arrow key and resets on double-click
 * within 360-720 px, and the width survives a reload (catalog case 15).
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import { ARTIFACTS, LONG_CSV_NAME, openRunArtifacts, setupArtifactRun } from './fixtures/artifact-run';

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  // Every test starts at the default width.
  await page.addInitScript(() => {
    try {
      if (!sessionStorage.getItem('spec-width-kept')) localStorage.removeItem('wfc.history.runDetailWidth');
    } catch {
      // A document with no storage (about:blank).
    }
  });
});

const viewer = (page: Page) => page.getByTestId('artifact-viewer');
const row = (page: Page, name: string) => page.getByTestId('artifact-row').filter({ hasText: name });

test('a folder member opens the viewer, which steps over the folder and closes back to the row', async ({ page }) => {
  await setupArtifactRun(page);
  await openRunArtifacts(page);

  // Previews start closed; the caret opens one, and its card opens the viewer.
  await expect(page.getByTestId('artifact-preview')).toHaveCount(0);
  await row(page, 'umap.png').getByTestId('preview-toggle').click();
  await expect(page.getByTestId('artifact-preview')).toHaveCount(1);
  await page.getByTestId('artifact-preview').click();
  await expect(viewer(page)).toBeVisible();
  await expect(page.getByTestId('viewer-name')).toHaveText('umap.png');
  await page.keyboard.press('Escape');
  await expect(viewer(page)).toHaveCount(0);

  // The folder: three images, a download-only file, a text file, a subfolder.
  await page.getByRole('button', { name: 'Open folder qc_plots/' }).click();
  const first = row(page, '01_n_genes.png').locator('.name-btn');
  await first.click();
  await expect(viewer(page)).toBeVisible();
  await expect(page.getByTestId('viewer-path')).toHaveText('qc_plots/');
  await expect(page.getByTestId('viewer-count')).toHaveText('1 / 4');
  await expect(page.getByTestId('viewer-strip').getByRole('button')).toHaveCount(4);

  await page.getByRole('button', { name: 'Next file' }).click();
  await expect(page.getByTestId('viewer-name')).toHaveText('02_total_counts.png');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('viewer-name')).toHaveText('03_pct_mito_16bit.tif');
  // A 16-bit, three-page TIFF larger than the view size: the amber note says all three.
  await expect(page.getByTestId('viewer-image-note')).toContainText('Preview 2048 x 1638 of 5000 x 4000');
  await expect(page.getByTestId('viewer-image-note')).toContainText('16-bit shown as 8-bit');
  await expect(page.getByTestId('viewer-image-note')).toContainText('page 1 of 3');
  // matrix.h5 is download only: the next file is notes.txt.
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('viewer-name')).toHaveText('notes.txt');
  await expect(page.getByTestId('viewer-count')).toHaveText('4 / 4');
  await expect(page.getByTestId('viewer-text')).toContainText('QC plots for TP132_ctrl_01.');
  await expect(page.getByRole('button', { name: 'Next file' })).toBeDisabled();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByTestId('viewer-name')).toHaveText('03_pct_mito_16bit.tif');

  await page.keyboard.press('Escape');
  await expect(viewer(page)).toHaveCount(0);
  await expect(first).toBeFocused();
});

test('the zoom controls and full screen act on an image', async ({ page }) => {
  const { imageRequests } = await setupArtifactRun(page);
  await openRunArtifacts(page);
  await row(page, 'spatial_section_04.tif').locator('.name-btn').click();
  const img = page.getByTestId('viewer-image');
  const zoom = page.getByTestId('viewer-zoom');
  await expect(img).toHaveAttribute('src', /size=view$/);
  await expect(page.getByTestId('viewer-making-preview')).toHaveCount(0);
  await expect(zoom).toHaveText('Fit');
  const fitWidth = (await img.boundingBox())!.width;

  const imgWidth = async () => (await img.boundingBox())!.width;
  await page.getByRole('button', { name: 'Zoom in' }).click();
  await expect(zoom).toHaveText(/^\d+%$/);
  await expect.poll(imgWidth).toBeGreaterThan(fitWidth);
  const zoomedIn = await imgWidth();
  await page.getByRole('button', { name: 'Zoom out' }).click();
  await expect.poll(imgWidth).toBeLessThan(zoomedIn);

  // The wheel zooms too.
  const before = await zoom.textContent();
  const stage = (await img.boundingBox())!;
  await page.mouse.move(stage.x + stage.width / 2, stage.y + stage.height / 2);
  await page.mouse.wheel(0, -200);
  await expect(zoom).not.toHaveText(before ?? '');

  // 1:1 of an 8000 x 6000 file: the image route's large size (4096 px wide), loaded on demand.
  await page.getByRole('button', { name: '1:1' }).click();
  await expect(zoom).toHaveText('100%');
  await expect(img).toHaveAttribute('style', /width: 4096px/);
  await expect(img).toHaveAttribute('src', /size=large$/);
  expect(imageRequests.some(r => r.path === 'spatial_section_04.tif' && r.size === 'large')).toBe(true);
  await page.getByRole('button', { name: 'Fit', exact: true }).click();
  await expect(zoom).toHaveText('Fit');

  // Full screen: the dialog fills the screen, and the same button leaves it.
  const fs = page.getByTestId('viewer-fullscreen');
  await fs.click();
  await expect(fs).toContainText('Exit full screen');
  expect(await page.evaluate(() => document.fullscreenElement?.getAttribute('data-testid'))).toBe('artifact-viewer');
  await fs.click();
  await expect(fs).toContainText('Full screen');
  expect(await page.evaluate(() => document.fullscreenElement)).toBeNull();
});

test('the table, JSON, text, PDF and too-large bodies render', async ({ page }) => {
  await setupArtifactRun(page);
  await openRunArtifacts(page);
  await row(page, 'counts.tsv').locator('.name-btn').click();

  const table = page.getByTestId('viewer-table');
  await expect(table.locator('tbody tr')).toHaveCount(7);
  await expect(table.locator('thead')).toContainText('ilc_01');
  await expect(page.getByTestId('viewer-delimiter').locator('option').first()).toHaveText('Auto (tab)');

  await page.getByRole('button', { name: 'Next file' }).click();
  await expect(page.getByTestId('viewer-name')).toHaveText(LONG_CSV_NAME);
  await expect(page.getByTestId('viewer-delimiter').locator('option').first()).toHaveText('Auto (comma)');
  await expect(table.locator('tbody tr')).toHaveCount(12);

  await page.getByRole('button', { name: 'Next file' }).click();
  await expect(page.getByTestId('viewer-json-tree')).toContainText('leiden_resolution');
  await page.getByRole('button', { name: 'Raw' }).click();
  await expect(page.getByTestId('viewer-json-tree')).toHaveCount(0);
  await expect(page.getByTestId('viewer-code').or(page.getByTestId('viewer-text'))).toContainText('"converged": true');

  await page.getByRole('button', { name: 'Show qc_report.pdf' }).click();
  await expect(page.getByTestId('viewer-pdf')).toHaveAttribute('src', /\/artifact\/qc_report\.pdf$/);
  await expect(page.getByTestId('viewer-fullscreen')).toBeVisible();

  await page.getByRole('button', { name: 'Show whole_slide_scan.tif' }).click();
  await expect(page.getByTestId('viewer-too-large'))
    .toHaveText('Too large to preview (40000 x 38400). To view it, set [previews] memory_mb to at least 11750 '
      + 'in wf-canvas.toml, or download it.');
  // Nothing to zoom: the zoom, 1:1, Fit and Full screen controls are gone (the PDF had Full screen).
  const viewer = page.getByTestId('artifact-viewer');
  await expect(viewer.getByTestId('viewer-zoom')).toHaveCount(0);
  for (const name of ['Zoom in', 'Zoom out', 'Fit', '1:1']) {
    await expect(viewer.getByRole('button', { name, exact: true })).toHaveCount(0);
  }
  await expect(viewer.getByTestId('viewer-fullscreen')).toHaveCount(0);
  await expect(viewer.getByRole('link', { name: '⤓ Download' })).toBeVisible();

  await page.getByRole('button', { name: 'Show run.log' }).click();
  const text = page.getByTestId('viewer-text');
  await expect(text).toContainText('leiden resolution=0.8');
  await expect(text.locator('.tx.warn')).toHaveCount(2);
  await expect(text.locator('.tx.err')).toHaveCount(1);

  // A YAML file is coloured by shiki.
  await page.getByRole('button', { name: 'Show config.yaml' }).click();
  await expect(page.getByTestId('viewer-code')).toContainText('resolution');
});

for (const count of [123, 1234]) {
  test(`a folder of ${count} files: its DIR pill overlaps neither show all, the name nor the size at 360 px`, async ({ page }) => {
    const listing = (ARTIFACTS.listing as { name: string }[])
      .map(a => (a.name === 'qc_plots/' ? { ...a, count } : a));
    await setupArtifactRun(page, { ...ARTIFACTS, listing });
    await openRunArtifacts(page);
    await expect(page.getByTestId('run-detail-edge')).toHaveAttribute('aria-valuenow', '360');

    const head = page.locator('.dir-head').filter({ hasText: 'qc_plots/' });
    const pill = head.locator('.dir-pill');
    await expect(pill).toHaveText(`DIR · ${count}`);
    const box = async (target: Locator) => (await target.boundingBox())!;
    const [name, showAll, dir, size, row] = await Promise.all([
      box(head.locator('.dname')), box(head.getByTestId('show-all')), box(pill),
      box(head.locator('.size')), box(head),
    ]);
    // Left to right, each ends before the next starts, and the size ends inside the row.
    expect(name.x + name.width).toBeLessThanOrEqual(showAll.x);
    expect(showAll.x + showAll.width).toBeLessThanOrEqual(dir.x);
    expect(dir.x + dir.width).toBeLessThanOrEqual(size.x);
    expect(size.x + size.width).toBeLessThanOrEqual(row.x + row.width);
  });
}

test('a row arrow sits halfway between the arrow above it and the row icon; icons, names and the tail column stay aligned', async ({ page }) => {
  await setupArtifactRun(page);
  await openRunArtifacts(page);
  await expect(page.getByTestId('run-detail-edge')).toHaveAttribute('aria-valuenow', '360');
  await page.getByRole('button', { name: 'Open folder qc_plots/' }).click();

  const box = async (target: Locator) => (await target.boundingBox())!;
  const left = async (target: Locator) => (await box(target)).x;
  const right = async (target: Locator) => {
    const b = await box(target);
    return b.x + b.width;
  };
  const centre = async (target: Locator) => {
    const b = await box(target);
    return b.x + b.width / 2;
  };
  const midway = async (arrow: Locator, above: Locator, icon: Locator) =>
    Math.abs(await centre(arrow) - (await centre(above) + await centre(icon)) / 2);
  const header = (cat: string) => page.getByTestId(`category-${cat}`).getByTestId('category-toggle');
  const folderHead = page.locator('.dir-head').filter({ hasText: 'qc_plots/' });
  const imageArrow = row(page, 'umap.png').getByTestId('preview-toggle');
  const folderArrow = folderHead.getByTestId('dir-toggle');
  const memberArrow = row(page, '01_n_genes.png').getByTestId('preview-toggle');

  // A row's arrow is centred halfway between its category header's arrow and
  // the header's icon (the row's icon shares that column), so it is never in
  // line with the header's arrow; a folder member's arrow halfway between its
  // folder's arrow and its own icon.
  const headerIcon = (cat: string) => page.getByTestId(`category-${cat}`).locator('.cat-header .cat-icon');
  expect(await midway(imageArrow, header('images'), headerIcon('images'))).toBeLessThan(1);
  expect(await midway(folderArrow, header('directories'), headerIcon('directories'))).toBeLessThan(1);
  expect(Math.abs(await left(headerIcon('images')) - await left(row(page, 'umap.png').locator('.slot')))).toBeLessThan(0.5);
  expect(await midway(memberArrow, folderArrow, row(page, '01_n_genes.png').locator('.slot'))).toBeLessThan(1);
  expect(await centre(imageArrow) - await centre(header('images'))).toBeGreaterThan(8);

  // Icons and names keep their columns: a Data row, an image row and a folder row line up.
  const dataRow = row(page, 'counts.tsv');
  const iconLeft = await left(dataRow.locator('.slot'));
  const nameLeft = await left(dataRow.locator('.fname'));
  for (const target of [row(page, 'umap.png'), row(page, 'qc_report.pdf'), folderHead]) {
    expect(Math.abs(await left(target.locator('.slot')) - iconLeft)).toBeLessThan(0.5);
  }
  for (const target of [row(page, 'umap.png').locator('.fname'), row(page, 'qc_report.pdf').locator('.fname'), folderHead.locator('.dname')]) {
    expect(Math.abs(await left(target) - nameLeft)).toBeLessThan(0.5);
  }
  // Folder members: an arrowed and an arrowless one share the icon column.
  expect(Math.abs(await left(row(page, '01_n_genes.png').locator('.slot'))
    - await left(row(page, 'matrix.h5').locator('.slot')))).toBeLessThan(0.5);

  // The tail column still ends at one x on every row.
  const dlRight = await right(dataRow.getByTestId('artifact-download'));
  for (const name of ['umap.png', '01_n_genes.png', 'matrix.h5']) {
    expect(Math.abs(await right(row(page, name).getByTestId('artifact-download')) - dlRight)).toBeLessThan(0.5);
  }
});

test('the panel edge drags, steps and resets within 360-720 px, and the width survives a reload', async ({ page }) => {
  await setupArtifactRun(page);
  await openRunArtifacts(page);
  const panel = page.locator('.detail-panel');
  const edge = page.getByTestId('run-detail-edge');
  await expect(edge).toHaveAttribute('role', 'separator');
  await expect(edge).toHaveAttribute('aria-valuenow', '360');
  await expect(edge).toHaveAttribute('aria-valuemin', '360');
  await expect(edge).toHaveAttribute('aria-valuemax', '720');
  const panelWidth = async () => Math.round((await panel.boundingBox())!.width);
  expect(await panelWidth()).toBe(360);

  // Drag the edge 200 px left: the panel is 560 px wide.
  const box = (await edge.boundingBox())!;
  const y = box.y + 200;
  await page.mouse.move(box.x + 2, y);
  await page.mouse.down();
  await page.mouse.move(box.x + 2 - 200, y, { steps: 8 });
  await page.mouse.up();
  await expect(edge).toHaveAttribute('aria-valuenow', '560');
  expect(await panelWidth()).toBe(560);

  // Past the bounds the panel stops at them.
  const at = (await edge.boundingBox())!;
  await page.mouse.move(at.x + 2, y);
  await page.mouse.down();
  await page.mouse.move(at.x - 600, y, { steps: 8 });
  await page.mouse.up();
  await expect(edge).toHaveAttribute('aria-valuenow', '720');
  expect(await panelWidth()).toBe(720);

  // Arrow keys move the edge 16 px: right narrows, left widens, never past 720.
  await edge.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(edge).toHaveAttribute('aria-valuenow', '720');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await expect(edge).toHaveAttribute('aria-valuenow', '688');

  // Double-click resets to 360, and the edge cannot go narrower.
  await edge.dblclick();
  await expect(edge).toHaveAttribute('aria-valuenow', '360');
  await page.keyboard.press('ArrowRight');
  await expect(edge).toHaveAttribute('aria-valuenow', '360');
  expect(await panelWidth()).toBe(360);

  // A width set before a reload is the width after it.
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  await expect(edge).toHaveAttribute('aria-valuenow', '392');
  await page.evaluate(() => sessionStorage.setItem('spec-width-kept', '1'));
  await openRunArtifacts(page);
  await expect(page.getByTestId('run-detail-edge')).toHaveAttribute('aria-valuenow', '392');
  expect(await panelWidth()).toBe(392);
});
