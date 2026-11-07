/**
 * The artifact list shows previews on demand and keeps every name readable
 * (catalog cases 11 and 14).
 *
 * A run with top-level images and a folder holding images: every preview
 * starts closed; a row's caret opens that preview alone; a folder's show all
 * opens every image in it, then reads hide all. A name the row has cut shows
 * its full name on hover and on keyboard focus; a download-only file's ringed icon says so on
 * hover. Component test over the real list; jsdom lays nothing out, so a cut
 * is simulated by giving the name's head a scroll width over its width.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { tick } from 'svelte';
import { cleanup, fireEvent, render, within } from '@testing-library/svelte';
import ArtifactList from '../ArtifactList.svelte';
import type { Artifact } from '../historyApi';

const ARTIFACTS: Artifact[] = [
  { name: 'umap.png', size: 240_000, type: 'file', viewer: 'image', extension: 'png' },
  { name: 'cluster_sizes.png', size: 170_000, type: 'file', viewer: 'image', extension: 'png' },
  { name: 'cluster_assignments.csv', size: 1_800_000, type: 'file', viewer: 'table', extension: 'csv' },
  { name: 'markers.parquet', size: 3_900_000, type: 'file', viewer: null, extension: 'parquet' },
  {
    name: 'qc_plots/', size: 2_400_000, type: 'dir', viewer: null, extension: '', count: 4,
    children: [
      { name: '01_n_genes.png', size: 210_000, viewer: 'image', extension: 'png' },
      { name: '02_total_counts.png', size: 205_000, viewer: 'image', extension: 'png' },
      { name: 'notes.txt', size: 300, viewer: 'text', extension: 'txt' },
      { name: 'raw/', size: 0, viewer: null, extension: '' },
    ],
  },
];

function mount() {
  return render(ArtifactList, { props: { runId: 'r1', artifacts: ARTIFACTS, artifactState: 'done' } });
}

function previewSrcs(container: HTMLElement): string[] {
  return [...container.querySelectorAll('[data-testid="artifact-preview"] img')].map(i => i.getAttribute('src') ?? '');
}

afterEach(() => {
  cleanup();
});

describe('artifact list previews', () => {
  it('starts with every preview closed; a caret opens that preview alone, as a thumb', async () => {
    const view = mount();
    expect(view.queryAllByTestId('artifact-preview')).toHaveLength(0);

    await fireEvent.click(view.getByRole('button', { name: 'Show preview of umap.png' }));

    expect(previewSrcs(view.container)).toEqual(['/api/wfc/run/r1/image/umap.png?size=thumb']);
    expect(view.getByRole('button', { name: 'Hide preview of umap.png' })).toHaveAttribute('aria-expanded', 'true');
    // Only image rows have a caret: the table and the download-only file do not.
    expect(view.queryByRole('button', { name: /preview of cluster_assignments\.csv/ })).toBeNull();
    expect(view.queryByRole('button', { name: /preview of markers\.parquet/ })).toBeNull();
  });

  it("a folder's show all opens every image in it, then reads hide all and closes them", async () => {
    const view = mount();
    const folder = view.getByRole('button', { name: 'Open folder qc_plots/' }).closest('.dir-head') as HTMLElement;
    const pill = within(folder).getByTestId('show-all');
    expect(pill).toHaveTextContent('show all');

    await fireEvent.click(pill);

    expect(previewSrcs(view.container)).toEqual([
      '/api/wfc/run/r1/image/qc_plots/01_n_genes.png?size=thumb',
      '/api/wfc/run/r1/image/qc_plots/02_total_counts.png?size=thumb',
    ]);
    expect(pill).toHaveTextContent('hide all');
    // The subdirectory stays a plain row: nothing on it to click.
    expect(within(view.getByTestId('artifact-subdir')).queryByRole('button')).toBeNull();
    expect(within(view.getByTestId('artifact-subdir')).queryByRole('link')).toBeNull();

    await fireEvent.click(pill);
    expect(view.queryAllByTestId('artifact-preview')).toHaveLength(0);
    expect(pill).toHaveTextContent('show all');
  });

  it('the Images header pill opens every top-level image, and only those', async () => {
    const view = mount();
    await fireEvent.click(within(view.getByTestId('category-images')).getByTestId('show-all'));
    expect(previewSrcs(view.container)).toEqual([
      '/api/wfc/run/r1/image/umap.png?size=thumb',
      '/api/wfc/run/r1/image/cluster_sizes.png?size=thumb',
    ]);
    expect(within(view.getByTestId('category-images')).getByTestId('show-all')).toHaveTextContent('hide all');
  });
});

describe('artifact list hover text', () => {
  it('a cut name shows its full name on hover; an uncut one shows nothing', async () => {
    const view = mount();
    const longName = view.getByText('cluster_assig').closest('.fname') as HTMLElement;
    const head = longName.querySelector('.n1') as HTMLElement;
    Object.defineProperty(head, 'scrollWidth', { configurable: true, value: 120 });
    Object.defineProperty(head, 'clientWidth', { configurable: true, value: 60 });

    await fireEvent.mouseEnter(longName);
    expect(view.getByTestId('artifact-tip')).toHaveTextContent('cluster_assignments.csv');
    await fireEvent.mouseLeave(longName);
    expect(view.queryByTestId('artifact-tip')).toBeNull();

    // umap.png fits: jsdom reports no overflow, so no tooltip.
    await fireEvent.mouseEnter(view.getByText('umap.png').closest('.fname') as HTMLElement);
    expect(view.queryByTestId('artifact-tip')).toBeNull();
  });

  it('a cut name shows its full name on keyboard focus too; an uncut one shows nothing', async () => {
    const view = mount();
    const head = view.getByText('cluster_assig');
    Object.defineProperty(head, 'scrollWidth', { configurable: true, value: 120 });
    Object.defineProperty(head, 'clientWidth', { configurable: true, value: 60 });
    const button = head.closest('button') as HTMLButtonElement;

    button.focus();
    await tick();
    expect(view.getByTestId('artifact-tip')).toHaveTextContent('cluster_assignments.csv');
    button.blur();
    await tick();
    expect(view.queryByTestId('artifact-tip')).toBeNull();

    (view.getByText('umap.png').closest('button') as HTMLButtonElement).focus();
    await tick();
    expect(view.queryByTestId('artifact-tip')).toBeNull();
  });

  it("a download-only file's ringed icon says so on hover; a viewable file's just downloads", async () => {
    const view = mount();
    const only = view.getByRole('link', { name: 'Download markers.parquet' });
    expect(only).toHaveClass('ring');
    await fireEvent.mouseEnter(only);
    expect(view.getByTestId('artifact-tip')).toHaveTextContent('Download only: no preview for this file type');
    await fireEvent.mouseLeave(only);

    const viewable = view.getByRole('link', { name: 'Download umap.png' });
    expect(viewable).not.toHaveClass('ring');
    expect(viewable).toHaveAttribute('download', 'umap.png');
    await fireEvent.mouseEnter(viewable);
    expect(view.getByTestId('artifact-tip')).toHaveTextContent(/^Download$/);
  });
});
