/**
 * A column dropdown reads its pinned upstream's own version (canvas
 * side): the lookup names the upstream's pin, and an upstream that
 * follows the newest names none.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { nodes, edges } from '../../builder/stores';
import { methodNode } from '../../builder/nodeData';
import { resolveColumnsFor } from '../nodeEdits';
import type { MethodDef, ParamDef } from '../../shared/types';

const SLOTS = { inputs: [{ name: 'data', type: 'csv' }], outputs: [{ name: 'table', type: 'csv' }], params: [] };
const UPSTREAM: MethodDef = {
  name: 'make', module: 'mod_a', ...SLOTS,
  versions: [
    { label: 1, identity: 'id-v1', newest: false, ...SLOTS },
    { label: 2, identity: 'id-v2', newest: true, ...SLOTS },
  ],
};
const COLUMN: ParamDef = { name: 'col', type: 'string', contractType: 'str', column_of_input: 'data' };
const DOWNSTREAM: MethodDef = { name: 'use', module: 'mod_a', ...SLOTS, params: [COLUMN] };

let urls: string[];

beforeEach(() => {
  urls = [];
  vi.stubGlobal('fetch', vi.fn(async (input: unknown) => {
    urls.push(String(input));
    return { ok: true, json: async () => ({ columns: ['a'] }) };
  }));
  edges.set([{ id: 'e', source: 'up', target: 'down', sourceHandle: 'table', targetHandle: 'data' }]);
});

afterEach(() => {
  vi.unstubAllGlobals();
  nodes.set([]);
  edges.set([]);
});

async function lookupWith(pin?: string): Promise<string> {
  const down = methodNode('down', { x: 0, y: 0 }, DOWNSTREAM);
  nodes.set([methodNode('up', { x: 0, y: 0 }, UPSTREAM, { version: pin }), down]);
  await resolveColumnsFor(down, COLUMN);
  return new URL(urls.at(-1)!, 'http://canvas').searchParams.get('version') ?? '';
}

describe('a column lookup names its upstream\'s pin', () => {
  it('a pinned upstream is asked for its own version\'s columns', async () => {
    expect(await lookupWith('id-v1')).toBe('id-v1');
  });

  it('an upstream that follows the newest names no version', async () => {
    expect(await lookupWith(undefined)).toBe('');
  });
});
