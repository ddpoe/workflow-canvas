/**
 * The artifact viewer steps through a folder and tells the truth about an
 * image preview (catalog cases 12 and 13).
 *
 * Case 12: opened from a folder member's row, the viewer steps through the
 * folder's viewable files in the folder's order with the arrows, the keys
 * and the strip, never landing on a download-only file; Esc closes it and
 * focus is back on the row. Case 13: the amber note appears exactly when
 * the preview is smaller, of lower bit depth or of fewer pages than the
 * file, listing only what applies; an image too large to decode says so.
 *
 * Component tests over the real list and viewer, the server answered by a
 * stubbed `fetch` (image-info and the preview route). jsdom lays nothing out
 * and loads no image, so zoom geometry and full screen are left to the
 * Playwright gate.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/svelte';
import ArtifactList from '../ArtifactList.svelte';
import ArtifactViewer from '../ArtifactViewer.svelte';
import type { Artifact, ImageInfo } from '../historyApi';
import type { ViewerItem } from '../viewerFormat';

const ARTIFACTS: Artifact[] = [
  { name: 'umap.png', size: 240_000, type: 'file', viewer: 'image', extension: 'png' },
  {
    name: 'qc_plots/', size: 2_400_000, type: 'dir', viewer: null, extension: '', count: 6,
    children: [
      { name: '01_n_genes.png', size: 210_000, viewer: 'image', extension: 'png' },
      { name: '02_total_counts.png', size: 205_000, viewer: 'image', extension: 'png' },
      { name: 'matrix.h5', size: 9_000_000, viewer: null, extension: 'h5' },
      { name: 'notes.txt', size: 300, viewer: 'text', extension: 'txt' },
      { name: 'qc_report.pdf', size: 1_100_000, viewer: 'pdf', extension: 'pdf' },
      { name: 'raw/', size: 0, viewer: null, extension: '' },
    ],
  },
];

const SAME: ImageInfo = {
  width: 800, height: 600, preview_width: 800, preview_height: 600,
  bit_depth: 8, bit_depth_reduced: false, pages: 1, decoded_bytes: 1_440_000,
  memory_bytes: 3_360_000, memory_mb_needed: 7, refused: null,
};

/** image-info answers by displayed path; anything else not found. */
let imageInfo: Record<string, ImageInfo> = {};
let fetched: string[] = [];
let prefetched: string[] = [];

function answer(body: unknown, status = 200) {
  return { ok: status < 400, status, statusText: '', json: async () => body, text: async () => JSON.stringify(body) };
}

beforeEach(() => {
  imageInfo = {};
  fetched = [];
  prefetched = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    fetched.push(url);
    const info = url.match(/\/image-info\/(.+)\?size=/);
    if (info) {
      const facts = imageInfo[decodeURIComponent(info[1])];
      return facts ? answer(facts) : answer({ detail: 'not an image' }, 415);
    }
    if (url.includes('/preview/')) {
      return answer({ kind: 'text', text: 'line one\nWARN two', truncated: false, shown_bytes: 17, total_bytes: 17 });
    }
    return answer({ detail: 'not found' }, 404);
  }));
  vi.stubGlobal('Image', class {
    set src(v: string) { prefetched.push(v); }
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function chipNames(dialog: HTMLElement): string[] {
  return within(within(dialog).getByTestId('viewer-strip'))
    .getAllByRole('button')
    .map(b => (b.getAttribute('aria-label') ?? '').replace(/^Show /, ''));
}

describe('the viewer steps through a folder (case 12)', () => {
  it('steps in folder order, skips download-only files, closes on Esc and returns focus to the row', async () => {
    const view = render(ArtifactList, { props: { runId: 'r1', artifacts: ARTIFACTS, artifactState: 'done' } });
    await fireEvent.click(view.getByRole('button', { name: 'Open folder qc_plots/' }));
    const row = view.getByText('01_n_genes.png').closest('button') as HTMLButtonElement;
    row.focus();
    await fireEvent.click(row);

    const dialog = view.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(within(dialog).getByTestId('viewer-path')).toHaveTextContent('qc_plots/');
    expect(within(dialog).getByTestId('viewer-name')).toHaveTextContent('01_n_genes.png');
    expect(within(dialog).getByTestId('viewer-count')).toHaveTextContent('1 / 4');
    // Focus moved inside the dialog.
    expect(dialog.contains(document.activeElement)).toBe(true);
    // The strip holds the viewable files only, in the folder's order.
    expect(chipNames(dialog)).toEqual(['01_n_genes.png', '02_total_counts.png', 'notes.txt', 'qc_report.pdf']);
    // The image opens on the view size; only the next image is fetched ahead.
    expect(within(dialog).getByTestId('viewer-image')).toHaveAttribute('src', '/api/wfc/run/r1/image/qc_plots/01_n_genes.png?size=view');
    expect(prefetched).toEqual(['/api/wfc/run/r1/image/qc_plots/02_total_counts.png?size=view']);

    await fireEvent.keyDown(window, { key: 'ArrowRight' });
    expect(within(dialog).getByTestId('viewer-name')).toHaveTextContent('02_total_counts.png');
    // The next file is text: nothing more is fetched ahead.
    expect(prefetched).toHaveLength(1);

    await fireEvent.keyDown(window, { key: 'ArrowRight' });
    // matrix.h5 (download only) is skipped.
    expect(within(dialog).getByTestId('viewer-name')).toHaveTextContent('notes.txt');
    expect(within(dialog).getByTestId('viewer-count')).toHaveTextContent('3 / 4');
    await waitFor(() => expect(within(dialog).getByTestId('viewer-text')).toHaveTextContent('WARN two'));

    await fireEvent.click(within(dialog).getByRole('button', { name: 'Next file' }));
    expect(within(dialog).getByTestId('viewer-name')).toHaveTextContent('qc_report.pdf');
    expect(within(dialog).getByTestId('viewer-pdf')).toHaveAttribute('src', '/api/wfc/run/r1/artifact/qc_plots/qc_report.pdf');
    expect(within(dialog).getByRole('button', { name: 'Next file' })).toBeDisabled();
    await fireEvent.keyDown(window, { key: 'ArrowRight' });
    expect(within(dialog).getByTestId('viewer-count')).toHaveTextContent('4 / 4');

    await fireEvent.keyDown(window, { key: 'ArrowLeft' });
    expect(within(dialog).getByTestId('viewer-name')).toHaveTextContent('notes.txt');
    await fireEvent.click(within(dialog).getByRole('button', { name: 'Show 01_n_genes.png' }));
    expect(within(dialog).getByTestId('viewer-count')).toHaveTextContent('1 / 4');
    expect(within(dialog).getByRole('button', { name: 'Previous file' })).toBeDisabled();

    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(view.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(row);
  });

  it('keeps Tab inside the dialog', async () => {
    const items: ViewerItem[] = [
      { path: 'a.txt', name: 'a.txt', size: 1, viewer: 'text', extension: 'txt', color: 'grey' },
    ];
    const view = render(ArtifactViewer, { props: { runId: 'r1', folder: '', items, start: 0, onClose: () => {} } });
    const dialog = view.getByRole('dialog');
    const close = within(dialog).getByRole('button', { name: 'Close' });
    expect(document.activeElement).toBe(close);
    // Close is the last control: Tab wraps to the first.
    await fireEvent.keyDown(window, { key: 'Tab' });
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Wrap' }));
    await fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(close);
  });
});

describe('the amber note says how a preview differs (case 13)', () => {
  const CASES: Array<[string, Partial<ImageInfo>, string | null]> = [
    ['the whole file', {}, null],
    ['a smaller preview', { width: 8192, height: 6144, preview_width: 2048, preview_height: 1536 },
      'Preview 2048 x 1536 of 8192 x 6144 · Download for the full image'],
    ['a reduced bit depth', { bit_depth: 16, bit_depth_reduced: true },
      '16-bit shown as 8-bit · Download for the full image'],
    ['one page of several', { pages: 30 }, 'page 1 of 30 · Download for the full image'],
    ['all three', {
      width: 8192, height: 6144, preview_width: 2048, preview_height: 1536,
      bit_depth: 16, bit_depth_reduced: true, pages: 30,
    }, 'Preview 2048 x 1536 of 8192 x 6144 · 16-bit shown as 8-bit · page 1 of 30 · Download for the full image'],
  ];

  function openImage() {
    const items: ViewerItem[] = [
      { path: 'scan.tif', name: 'scan.tif', size: 1, viewer: 'image', extension: 'tif', color: 'blue' },
    ];
    return render(ArtifactViewer, { props: { runId: 'r1', folder: '', items, start: 0, onClose: () => {} } });
  }

  it.each(CASES)('%s', async (_label, facts, expected) => {
    imageInfo['scan.tif'] = { ...SAME, ...facts };
    const view = openImage();
    await waitFor(() => expect(fetched.some(u => u.includes('/image-info/scan.tif?size=view'))).toBe(true));
    // The image is still converting: its place says so.
    expect(view.getByTestId('viewer-making-preview')).toHaveTextContent('making preview…');
    if (expected === null) {
      await new Promise(r => setTimeout(r, 0));
      expect(view.queryByTestId('viewer-image-note')).toBeNull();
    } else {
      await waitFor(() => expect(view.getByTestId('viewer-image-note')).toHaveTextContent(expected));
    }
  });

  it('an image too large to preview says so, with its size and the memory_mb that would show it', async () => {
    imageInfo['scan.tif'] = {
      ...SAME, width: 16384, height: 16384, memory_mb_needed: 1168, refused: 'over memory_mb',
    };
    const view = openImage();
    await waitFor(() => expect(view.getByTestId('viewer-too-large')).toHaveTextContent(
      'Too large to preview (16384 x 16384). To view it, set [previews] memory_mb to at least 1168 '
      + 'in wf-canvas.toml, or download it.'));
    expect(view.queryByTestId('viewer-image')).toBeNull();
    // Nothing to zoom or show full screen: those controls are gone, Download stays.
    for (const name of ['Zoom in', 'Zoom out', 'Fit', '1:1']) {
      expect(view.queryByRole('button', { name })).toBeNull();
    }
    expect(view.queryByTestId('viewer-zoom')).toBeNull();
    expect(view.queryByTestId('viewer-fullscreen')).toBeNull();
    expect(view.getByText('⤓ Download')).toBeInTheDocument();
  });

  it('an image that previews keeps its zoom and Full screen controls', async () => {
    imageInfo['scan.tif'] = { ...SAME };
    const view = openImage();
    await waitFor(() => expect(fetched.some(u => u.includes('/image-info/scan.tif?size=view'))).toBe(true));
    await new Promise(r => setTimeout(r, 0));
    expect(view.getByRole('button', { name: 'Zoom in' })).toBeInTheDocument();
    expect(view.getByRole('button', { name: '1:1' })).toBeInTheDocument();
    expect(view.getByTestId('viewer-fullscreen')).toBeInTheDocument();
  });
});
