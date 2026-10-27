/**
 * Every link in the run detail's Cache-key inputs card hands off to the view
 * it names, and its copy icons copy the full value.
 *
 * `CacheKeyInputsCard.svelte` mounted over the real History and Registry
 * stores. Each link is clicked and the store the target view reads is
 * asserted: the selected run (and the tab it opens on), the Registry focus
 * (sub-tab, method and version), and the Pipelines view with the run
 * highlighted. That the Registry tab then shows the row is Playwright Test F
 * in `tests/e2e/history-descendants.spec.ts`.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import { get } from 'svelte/store';
import CacheKeyInputsCard from '../CacheKeyInputsCard.svelte';
import {
  historyView,
  highlightedRunId,
  selectedRunId,
  takePendingSection,
} from '../historyStore';
import { registryFocus } from '../../registry/registryFocus';
import type { WfcRun } from '../historyApi';

const COMMIT = '1fb99bc5d3e2a1b0c9f8e7d6c5b4a3f2e1d0c9b8';

function run(): WfcRun {
  return {
    id: 'r9', module: 'viz', method: 'plot', version: 3, versionIdentity: 'id-v3',
    timestamp: 0, duration: 0, status: 'success',
    inputs: { title: 'umap' }, outputs: {}, outputAvailability: {}, metrics: {},
    dataSource: 's1', parentRunIds: ['r4'], upstreamRunIds: ['r4'],
    parents: [{ slot: 'adata', sourceRunId: 'r4', sourceSlot: 'adata_out', cacheKey: 'k'.repeat(64) }],
    sampleInputs: [{ slot: 'palette', sample: 'colors_v2', contentHash: 'c'.repeat(32) }],
    codeFingerprint: 'a'.repeat(64), envName: 'env', envFingerprint: 'e'.repeat(32),
    gitCommit: COMMIT, wfcVersion: '0.7.0', pipelineDocHash: 'd'.repeat(32),
    keyedUnderEarlierRule: false, paramsHash: 'p'.repeat(32), inputsHash: 'i'.repeat(64),
    paramTypes: { title: 'str' },
    experimentId: 'pl-1', runName: 'plot', nid: 'v1', user: '', favorite: false,
    pipelineId: 'pl-1', scriptPath: null,
  };
}

async function opened(): Promise<HTMLElement> {
  const { container } = render(CacheKeyInputsCard, { props: { run: run() } });
  const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as HTMLElement;
  await fireEvent.click(byId('cache-key-toggle'));
  await fireEvent.click(byId('cache-key-slots-toggle'));
  return container;
}

function el(container: HTMLElement, id: string): HTMLElement {
  const found = container.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;
  expect(found, id).not.toBeNull();
  return found!;
}

let clipboard: string | null = null;

beforeEach(() => {
  selectedRunId.set(null);
  registryFocus.set(null);
  highlightedRunId.set(null);
  historyView.set('descendants');
  takePendingSection('');
  clipboard = null;
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn(async (t: string) => { clipboard = t; }) },
  });
});

afterEach(() => { cleanup(); });

describe('the Cache-key inputs card links', () => {
  it('opens closed, and the run chip opens that run on its Overview', async () => {
    const { container } = render(CacheKeyInputsCard, { props: { run: run() } });
    expect(container.querySelector('[data-testid="cache-key-group-in-key"]')).toBeNull();
    cleanup();

    const card = await opened();
    await fireEvent.click(el(card, 'slot-run-link'));
    expect(get(selectedRunId)).toBe('r4');
    expect(takePendingSection('r4')).toBe('overview');
  });

  it('the output-slot chip opens that run on its Artifacts', async () => {
    const card = await opened();
    await fireEvent.click(el(card, 'slot-output-link'));
    expect(get(selectedRunId)).toBe('r4');
    expect(takePendingSection('r4')).toBe('artifacts');
    // Taken once: the next opening of the run starts on Overview.
    expect(takePendingSection('r4')).toBe('overview');
  });

  it('the method chip asks the Registry for the method, on the run\'s version', async () => {
    const card = await opened();
    await fireEvent.click(el(card, 'cache-key-method-link'));
    expect(get(registryFocus)).toEqual({
      sub: 'methods', module: 'viz', method: 'plot', versionIdentity: 'id-v3', versionLabel: 'v3',
    });
  });

  it('the sample chip asks the Registry for its samples, and its copy icon copies the name', async () => {
    const card = await opened();
    await fireEvent.click(el(card, 'slot-sample-link'));
    expect(get(registryFocus)).toEqual({ sub: 'samples' });
    await fireEvent.click(el(card, 'slot-sample-copy'));
    expect(clipboard).toBe('colors_v2');
  });

  it('the pipeline tag switches to the Pipelines view with the run highlighted', async () => {
    const card = await opened();
    await fireEvent.click(el(card, 'cache-key-pipeline-link'));
    expect(get(historyView)).toBe('pipelines');
    expect(get(highlightedRunId)).toBe('r9');
  });

  it('a parent with no recorded output slot reads "not recorded", and links nowhere', async () => {
    const r = run();
    r.parents = [{ ...r.parents[0], sourceSlot: null }];
    const { container } = render(CacheKeyInputsCard, { props: { run: r } });
    await fireEvent.click(el(container, 'cache-key-toggle'));
    await fireEvent.click(el(container, 'cache-key-slots-toggle'));
    const cell = el(container, 'slot-output-unrecorded');
    expect(cell.textContent).toBe('not recorded');
    // It says what it cannot tell: no name was given, or none was recorded.
    expect(cell.getAttribute('aria-label')).toMatch(/named none, or the run predates recording it/);
    expect(container.querySelector('[data-testid="slot-output-link"]')).toBeNull();
  });

  it('each hash tag\'s copy icon copies its full value, the commit\'s its full SHA', async () => {
    const card = await opened();
    const copyOf = (kind: string) =>
      card.querySelector(`[data-testid="hash-copy"][data-kind="${kind}"]`) as HTMLElement;
    await fireEvent.click(copyOf('commit'));
    expect(clipboard).toBe(COMMIT);
    await fireEvent.click(copyOf('code'));
    expect(clipboard).toBe('a'.repeat(64));
    await fireEvent.click(copyOf('run-key'));
    expect(clipboard).toBe('k'.repeat(64));
    // Clicking a tag itself copies nothing: the pipeline tag is a link.
    clipboard = null;
    await fireEvent.click(el(card, 'cache-key-pipeline-link'));
    expect(clipboard).toBeNull();
  });
});
