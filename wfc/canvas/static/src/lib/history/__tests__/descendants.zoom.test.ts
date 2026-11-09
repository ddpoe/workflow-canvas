/**
 * The Descendants view zooms its run cards.
 *
 * The cards and their tree connectors (each tree row) scale together; the
 * sample label stays at its own size. A control box holds − / percentage /
 * + / Fit, the keys + − 0 and Ctrl+wheel reach it, and the choice is kept
 * in this browser. Fit widens the cards to the view, within 100–150%.
 * Component test over the real view and stores; jsdom has no layout, so
 * Fit's measurement is covered here through `fitZoom` and in the browser by
 * Playwright Test H in tests/e2e/history-descendants.spec.ts.
 */
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import DescendantsView from '../DescendantsView.svelte';
import { runs, resetFilters, descendantsCollapsed, selectedRunId } from '../historyStore';
import type { WfcRun } from '../historyApi';
import { fitZoom, ZOOM_KEY } from '../descendantsZoom';

function mkRun(partial: Partial<WfcRun>): WfcRun {
  return {
    id: 'r1', module: 'seg', method: 'normalize', version: 3, timestamp: 1, duration: 14.2,
    status: 'success', inputs: {}, outputs: {}, outputAvailability: {}, metrics: {},
    dataSource: 'sample_a', parentRunIds: [], upstreamRunIds: [], parents: [],
    experimentId: 'e', runName: 'pip/run', nid: 'v1', user: 'u', favorite: false,
    pipelineId: null, scriptPath: null,
    ...partial,
  } as WfcRun;
}

/** A root and its child on one sample: two tree rows, one connector. */
function twoRuns(): WfcRun[] {
  return [
    mkRun({ id: 'load', method: 'load' }),
    mkRun({ id: 'seg', method: 'segment', parentRunIds: ['load'], upstreamRunIds: ['load'], timestamp: 2 }),
  ];
}

function level(container: HTMLElement): string {
  return container.querySelector('[data-testid="descendants-zoom-level"]')?.textContent?.trim() ?? '';
}

/**
 * The zoom an element's inline style gives it: the view sets `--dz`, which
 * its `.tree-row` rule turns into CSS `zoom` (jsdom has no layout, so the
 * drawn scale is Playwright Test H's).
 */
function inlineZoom(el: HTMLElement): string | null {
  return el.style.getPropertyValue('--dz').trim() || null;
}

function rowZooms(container: HTMLElement): (string | null)[] {
  return [...container.querySelectorAll<HTMLElement>('.tree-row')].map(inlineZoom);
}

/** The zoom set on the label or on anything between it and the view. */
function zoomAbove(label: HTMLElement, root: HTMLElement): string[] {
  const found: string[] = [];
  for (let el: HTMLElement | null = label; el && el !== root.parentElement; el = el.parentElement) {
    const z = inlineZoom(el);
    if (z) found.push(z);
  }
  return found;
}

function button(container: HTMLElement, name: string): HTMLElement {
  const b = container.querySelector<HTMLElement>(`[data-testid="descendants-zoom"] button[aria-label="${name}"]`);
  if (!b) throw new Error(`no zoom button "${name}"`);
  return b;
}

beforeEach(() => {
  localStorage.clear();
  runs.set(twoRuns());
  resetFilters();
  descendantsCollapsed.set(new Set());
  selectedRunId.set(null);
});

afterEach(() => cleanup());

describe('zooming the Descendants cards', () => {
  it('+ and − zoom every tree row and show the percentage; the sample label keeps its size', async () => {
    const { container } = render(DescendantsView);
    const label = container.querySelector<HTMLElement>('.section-label')!;

    await fireEvent.click(button(container, 'Zoom in'));
    expect(level(container)).toBe('110%');
    expect(rowZooms(container)).toEqual(['1.1', '1.1']);
    expect(zoomAbove(label, container)).toEqual([]);

    await fireEvent.click(button(container, 'Zoom out'));
    await fireEvent.click(button(container, 'Zoom out'));
    expect(level(container)).toBe('90%');
    expect(rowZooms(container)).toEqual(['0.9', '0.9']);
  });

  it('+ = − and 0 on the focused view zoom and fit; Ctrl+wheel zooms and a plain wheel does not', async () => {
    const { container } = render(DescendantsView);
    const view = container.querySelector<HTMLElement>('.descendants-view')!;
    const fit = button(container, 'Fit to view');

    await fireEvent.keyDown(view, { key: '+' });
    await fireEvent.keyDown(view, { key: '=' });
    expect(level(container)).toBe('120%');
    expect(fit.classList.contains('sel')).toBe(false);
    await fireEvent.keyDown(view, { key: '-' });
    expect(level(container)).toBe('110%');
    // Ctrl with a key stays the browser's own zoom.
    await fireEvent.keyDown(view, { key: '=', ctrlKey: true });
    expect(level(container)).toBe('110%');
    await fireEvent.keyDown(view, { key: '0' });
    expect(fit.classList.contains('sel')).toBe(true);

    const scroller = container.querySelector<HTMLElement>('.dv-scroll')!;
    await fireEvent.click(button(container, 'Zoom in'));
    const before = level(container);
    await fireEvent.wheel(scroller, { deltaY: -100 });
    expect(level(container)).toBe(before);
    await fireEvent.wheel(scroller, { deltaY: -100, ctrlKey: true });
    expect(Number.parseInt(level(container))).toBe(Number.parseInt(before) + 10);
    await fireEvent.wheel(scroller, { deltaY: 100, ctrlKey: true });
    expect(level(container)).toBe(before);
  });

  it('Fit widens the cards to the view, never below 100% or above 150%', () => {
    expect(fitZoom(1200, 1000)).toBe(120);
    expect(fitZoom(1500, 500)).toBe(150);
    expect(fitZoom(800, 1000)).toBe(100);
    // Rounded down, so the widest card never overruns the edge.
    expect(fitZoom(1000, 750)).toBe(133);
    // Nothing measured yet (no layout): plain size.
    expect(fitZoom(1000, 0)).toBe(100);
  });

  it('the chosen zoom is kept in this browser and restored', async () => {
    localStorage.setItem(ZOOM_KEY, '130');
    const first = render(DescendantsView);
    expect(level(first.container)).toBe('130%');
    expect(rowZooms(first.container)).toEqual(['1.3', '1.3']);

    await fireEvent.click(button(first.container, 'Fit to view'));
    expect(localStorage.getItem(ZOOM_KEY)).toBe('fit');
    await fireEvent.click(button(first.container, 'Zoom in'));
    first.unmount();

    const second = render(DescendantsView);
    expect(level(second.container)).toBe(localStorage.getItem(ZOOM_KEY) + '%');
    expect(button(second.container, 'Fit to view').classList.contains('sel')).toBe(false);
  });

  it('the first time, with nothing kept, the view opens at Fit', () => {
    const { container } = render(DescendantsView);
    expect(button(container, 'Fit to view').classList.contains('sel')).toBe(true);
  });
});
