/**
 * One module colour, read at render time by every view (catalog cases 20
 * and 21; SOP Canvas Colours, Module Colours and Selection).
 *
 * The colour arrives with the module from the API (`rememberModuleColors`,
 * as the module fetchers call it). A Builder node header and a History
 * lineage card's accent bar both take it from `moduleColor`, and a changed
 * colour reaches both without remounting either. Selecting a lineage card
 * adds the glow and leaves its border as it was.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import { flushSync } from 'svelte';
import CustomNode from '../../builder/CustomNode.svelte';
import PathNodeCard from '../../history/PathNodeCard.svelte';
import DescendantsView from '../../history/DescendantsView.svelte';
import { selectedRunId, runs, resetFilters, descendantsCollapsed } from '../../history/historyStore';
import { forgetModuleColors, moduleColor, rememberModuleColors, MODULE_COLOR_UNKNOWN } from '../moduleColor.svelte';
import type { CanvasNodeData } from '../types';
import type { WfcRun } from '../../history/historyApi';

vi.mock('@xyflow/svelte', async () => ({
  Handle: (await import('../../builder/__tests__/HandleStub.svelte')).default,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
}));

afterEach(() => {
  cleanup();
  forgetModuleColors();
  selectedRunId.set(null);
});

const COBALT = '#3C65DE';
const ROSE = '#AA4B70';
const rgb = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
};

function nodeData(): CanvasNodeData {
  return {
    label: 'normalize', method: 'normalize', module: 'mod_a',
    inputs: [], outputs: [], params: [], paramValues: {},
    runStatus: 'idle', expanded: false, nodeType: 'method',
  } as CanvasNodeData;
}

function run(status: string): WfcRun {
  return {
    id: 'r1', method: 'normalize', module: 'mod_a', status,
    sample: 's1', created_at: '2026-11-04T10:00:00Z',
  } as unknown as WfcRun;
}

describe('one module colour in every view', () => {
  it('a module the canvas has no colour for reads the unknown colour', () => {
    expect(moduleColor('mod_a')).toBe(MODULE_COLOR_UNKNOWN);
    rememberModuleColors([{ name: 'mod_a', color: COBALT }]);
    expect(moduleColor('mod_a')).toBe(COBALT);
    // A module sent with no colour keeps the one it had.
    rememberModuleColors([{ name: 'mod_a', color: null }]);
    expect(moduleColor('mod_a')).toBe(COBALT);
  });

  it('the Builder header and the History bar show the module colour, and follow a change (case 21)', () => {
    rememberModuleColors([{ name: 'mod_a', color: COBALT }]);
    const node = render(CustomNode, { props: { data: nodeData(), id: 'n1', selected: false } });
    const card = render(PathNodeCard, { props: { run: run('success') } });
    const header = node.container.querySelector('.node-header') as HTMLElement;
    const bar = card.container.querySelector('.path-node') as HTMLElement;
    expect(header.style.background).toContain(rgb(COBALT));
    expect(bar.style.borderLeftColor).toBe(rgb(COBALT));

    rememberModuleColors([{ name: 'mod_a', color: ROSE }]);
    flushSync();
    expect(header.style.background).toContain(rgb(ROSE));
    expect(bar.style.borderLeftColor).toBe(rgb(ROSE));
  });

  it('History keeps a succeeded card neutral and colours a failed one (exceptions only)', () => {
    rememberModuleColors([{ name: 'mod_a', color: COBALT }]);
    const ok = render(PathNodeCard, { props: { run: run('success') } }).container.querySelector('.path-node') as HTMLElement;
    const bad = render(PathNodeCard, { props: { run: run('failed') } }).container.querySelector('.path-node') as HTMLElement;
    expect(ok.style.getPropertyValue('--glow-c')).toBe('var(--border)');
    expect(bad.style.getPropertyValue('--glow-c')).toBe('var(--status-failed)');
    // The accent bar is the module colour whatever the status.
    expect(bad.style.borderLeftColor).toBe(rgb(COBALT));
  });

  it.each([
    ['success', true],
    ['failed', false],
  ])('selecting a %s lineage card glows and keeps its border (case 20)', (status, neutral) => {
    rememberModuleColors([{ name: 'mod_a', color: COBALT }]);
    const el = render(PathNodeCard, { props: { run: run(status) } }).container.querySelector('.path-node') as HTMLElement;
    const before = el.getAttribute('style');
    expect(el.classList.contains('sel-glow')).toBe(false);

    selectedRunId.set('r1');
    flushSync();
    expect(el.classList.contains('sel-glow')).toBe(true);
    expect(el.classList.contains('glow-neutral')).toBe(neutral);
    expect(el.getAttribute('style')).toBe(before);
  });

  it.each([
    ['success', true],
    ['failed', false],
  ])('selecting a %s Descendants node glows and keeps its border (case 20)', (status, neutral) => {
    rememberModuleColors([{ name: 'mod_a', color: COBALT }]);
    resetFilters();
    descendantsCollapsed.set(new Set());
    runs.set([{
      ...run(status), timestamp: 1, duration: 0, inputs: {}, outputs: {},
      outputAvailability: {}, metrics: {}, dataSource: 's1', parentRunIds: [],
      upstreamRunIds: [], parents: [], experimentId: 'e', runName: 'r1', nid: null,
      user: 'u', favorite: false, pipelineId: null, scriptPath: null,
    } as unknown as WfcRun]);
    const el = render(DescendantsView).container.querySelector('.tree-card') as HTMLElement;
    const before = el.getAttribute('style');
    expect(el.classList.contains('sel-glow')).toBe(false);
    // The node body carries the module tint, its left bar the module colour.
    expect(el.style.borderLeftColor).toBe(rgb(COBALT));

    selectedRunId.set('r1');
    flushSync();
    expect(el.classList.contains('sel-glow')).toBe(true);
    expect(el.classList.contains('glow-neutral')).toBe(neutral);
    expect(el.getAttribute('style')).toBe(before);
    runs.set([]);
  });
});
