/**
 * The run panel's two remembered states (catalog cases 15 and 16): its
 * width, clamped to 360-720 px and kept in this browser across a reload
 * (store level); and which artifact categories are collapsed, kept for the
 * session (every run the list shows after) but not across a reload, through
 * the real artifact list.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { cleanup, fireEvent, render, within } from '@testing-library/svelte';
import ArtifactList from '../ArtifactList.svelte';
import type { Artifact } from '../historyApi';

const WIDTH_KEY = 'wfc.history.runDetailWidth';

beforeEach(() => {
  localStorage.removeItem(WIDTH_KEY);
  vi.resetModules();
});

describe('run panel width', () => {
  it('starts at 360 and keeps every width within 360-720', async () => {
    const { runDetailWidth } = await import('../runDetailWidth');
    expect(get(runDetailWidth)).toBe(360);

    runDetailWidth.set(1000);
    expect(get(runDetailWidth)).toBe(720);
    runDetailWidth.update(w => w - 900);
    expect(get(runDetailWidth)).toBe(360);
    runDetailWidth.set(Number.NaN);
    expect(get(runDetailWidth)).toBe(360);
    runDetailWidth.set(512.4);
    expect(get(runDetailWidth)).toBe(512);
  });

  it('is remembered across a reload, and a stored width out of bounds comes back clamped', async () => {
    const first = await import('../runDetailWidth');
    first.runDetailWidth.set(480);

    vi.resetModules();
    const reloaded = await import('../runDetailWidth');
    expect(get(reloaded.runDetailWidth)).toBe(480);

    localStorage.setItem(WIDTH_KEY, '5000');
    vi.resetModules();
    const tampered = await import('../runDetailWidth');
    expect(get(tampered.runDetailWidth)).toBe(720);

    localStorage.setItem(WIDTH_KEY, 'wide');
    vi.resetModules();
    const garbage = await import('../runDetailWidth');
    expect(get(garbage.runDetailWidth)).toBe(360);
  });
});

describe('artifact category collapse', () => {
  const runArtifacts = (run: string): Artifact[] => [
    { name: `${run}_umap.png`, size: 1_000, type: 'file', viewer: 'image', extension: 'png' },
    { name: `${run}_counts.csv`, size: 2_000, type: 'file', viewer: 'table', extension: 'csv' },
  ];
  const rowsIn = (view: { getByTestId(id: string): HTMLElement }, cat: string) =>
    within(view.getByTestId(`category-${cat}`)).queryAllByTestId('artifact-row');

  afterEach(() => {
    cleanup();
  });

  it('a category collapsed in the list stays collapsed for every later run of the session, and a reload opens it', async () => {
    const first = render(ArtifactList, { props: { runId: 'r1', artifacts: runArtifacts('r1'), artifactState: 'done' } });
    await fireEvent.click(first.getByRole('button', { name: 'Collapse Images' }));
    expect(rowsIn(first, 'images')).toHaveLength(0);

    // The panel shows the next run: the same list gets that run's artifacts.
    await first.rerender({ runId: 'r2', artifacts: runArtifacts('r2'), artifactState: 'done' });
    expect(rowsIn(first, 'images')).toHaveLength(0);
    expect(rowsIn(first, 'data')).toHaveLength(1);

    // A list made afresh for a third run (the tab reopened) keeps it too:
    // the collapse is the session's, not the component's.
    cleanup();
    const third = render(ArtifactList, { props: { runId: 'r3', artifacts: runArtifacts('r3'), artifactState: 'done' } });
    expect(rowsIn(third, 'images')).toHaveLength(0);
    expect(third.getByRole('button', { name: 'Expand Images' })).toHaveAttribute('aria-expanded', 'false');
    expect(rowsIn(third, 'data')).toHaveLength(1);
    await fireEvent.click(third.getByRole('button', { name: 'Expand Images' }));
    expect(rowsIn(third, 'images')).toHaveLength(1);

    // Nothing is persisted: a reload opens every category.
    await fireEvent.click(third.getByRole('button', { name: 'Collapse Data' }));
    vi.resetModules();
    const reloaded = await import('../artifactCollapse');
    expect(get(reloaded.collapsedCategories)).toEqual({});
  });
});
