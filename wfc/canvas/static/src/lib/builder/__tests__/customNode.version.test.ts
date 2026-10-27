/**
 * A method node shows the version it runs.
 *
 * `CustomNode.svelte` mounted over node data built by the one writer,
 * `nodeData.ts::methodNode`. The header labels the module, the env and the
 * version the node runs. A green dot sits beside the number when the node
 * runs the newest version and a yellow one when it is pinned to an older
 * one, with a tooltip saying which.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import CustomNode from '../CustomNode.svelte';
import { methodNode } from '../nodeData';
import type { MethodDef } from '../../shared/types';

// `Handle` needs a mounted SvelteFlow; see customNode.runStatus.test.ts.
vi.mock('@xyflow/svelte', async () => ({
  Handle: (await import('./HandleStub.svelte')).default,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
}));

afterEach(() => { cleanup(); });

const SLOTS = {
  inputs: [{ name: 'data', type: 'csv' }],
  outputs: [{ name: 'output', type: 'csv' }],
  params: [],
};
const METHOD: MethodDef = {
  name: 'norm', module: 'mod_a', ...SLOTS,
  versions: [
    { label: 1, identity: 'id-v1', newest: false, env: 'container:old-env', ...SLOTS },
    { label: 2, identity: 'id-v2', newest: true, env: 'container:new-env', ...SLOTS },
  ],
};

function header(pin?: string): HTMLElement {
  const { container } = render(CustomNode, {
    props: { data: methodNode('n', { x: 0, y: 0 }, METHOD, { version: pin }).data, id: 'n' },
  });
  return container.querySelector('.node-meta') as HTMLElement;
}

/** Each chip's label and value, in the order the header draws them. */
function chips(h: HTMLElement): string[][] {
  return [...h.querySelectorAll('.meta-chip')].map(c => [
    c.querySelector('.meta-key')!.textContent!.trim(),
    c.querySelector('.meta-val')!.textContent!.trim(),
  ]);
}

describe('a method node shows the version it runs', () => {
  it('a node that runs the newest marks its number as the latest', () => {
    const h = header();
    expect(chips(h)).toEqual([['Module', 'mod_a'], ['Env', 'new-env'], ['Version', 'v2']]);
    const version = h.querySelector('.node-version') as HTMLElement;
    expect(version.classList.contains('node-version-newest')).toBe(true);
    expect(version.title).toBe('Latest method version (v2)');
  });

  it('a node pinned to an older version names its pin and the latest, and its env', () => {
    const h = header('id-v1');
    expect(chips(h)).toEqual([['Module', 'mod_a'], ['Env', 'old-env'], ['Version', 'v1']]);
    const version = h.querySelector('.node-version') as HTMLElement;
    expect(version.classList.contains('node-version-newest')).toBe(false);
    expect(version.title).toBe('Pinned to method version v1 · latest is v2');
    // The env chip drops the env's kind; its hover gives the full value.
    expect((h.querySelector('.node-env') as HTMLElement).title).toBe('container:old-env');
  });
});
