/**
 * The options lookup's requests (canvas side): for an `options_from` param,
 * one request per upstream wired to the inputs it lists, in `options_from`
 * order and then edge order, each carrying every value set the upstream
 * would run with (base values, sweep variants, per-sample overrides, a
 * pipeline variable's value in place of its ref) and its pin; a Run
 * Reference names its run. The key the Inspector watches changes with the
 * upstreams' values and wiring, not with the downstream's own.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { nodes, edges, pipelineVariables, updateNodeData } from '../../builder/stores';
import { pendingBoundVariables } from '../../builder/pipeline';
import { inputSelectorNode, methodNode, runReferenceNode } from '../../builder/nodeData';
import {
  lookupPipeline, optionsLookupKey, optionsLookupPlan, resolveOptionsLookup,
  type CanvasNode,
} from '../nodeEdits';
import type { MethodDef, ParamDef } from '../../shared/types';

const SLOTS = {
  inputs: [{ name: 'cells', type: 'csv' }, { name: 'annotations', type: 'csv' }],
  outputs: [{ name: 'table', type: 'csv' }],
};
const CHANNELS: ParamDef = { name: 'channels', type: 'str', contractType: 'str' };
const UPSTREAM: MethodDef = {
  name: 'measure', module: 'feat', ...SLOTS, params: [CHANNELS],
  versions: [
    { label: 1, identity: 'id-v1', newest: false, ...SLOTS, params: [CHANNELS] },
    { label: 2, identity: 'id-v2', newest: true, ...SLOTS, params: [CHANNELS] },
  ],
};
const JOIN: ParamDef = {
  name: 'join_key', type: 'str', contractType: 'str', options_from: ['cells', 'annotations'],
};
const DOWNSTREAM: MethodDef = { name: 'join', module: 'feat', ...SLOTS, params: [JOIN] };

type Call = { url: string; body: Record<string, unknown> };
let calls: Call[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: { body?: string }) => {
    calls.push({ url: String(url), body: JSON.parse(init?.body ?? '{}') });
    return { ok: true, json: async () => ({ fixed: ['a'], built: [], patterns: [], all: ['a'] }) };
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  nodes.set([]);
  edges.set([]);
  pipelineVariables.set({});
  pendingBoundVariables.set({});
});

function down(): CanvasNode {
  return get(nodes).find(n => n.id === 'down') as CanvasNode;
}

function plan() {
  const node = down();
  return optionsLookupPlan(node, node.data, get(edges), get(nodes),
    lookupPipeline(get(nodes), get(edges), get(pipelineVariables)));
}

async function lookup() {
  return resolveOptionsLookup(plan());
}

function wire(...links: Array<[string, string]>) {
  edges.set(links.map(([source, input], i) => ({
    id: `e${i}`, source, target: 'down', sourceHandle: 'table', targetHandle: input,
  })));
}

describe('a method upstream\'s request', () => {
  it('carries the base values, each variant\'s and override\'s values, and the pin', async () => {
    nodes.set([
      methodNode('up', { x: 0, y: 0 }, UPSTREAM, { version: 'id-v1' }),
      methodNode('down', { x: 0, y: 0 }, DOWNSTREAM),
    ]);
    updateNodeData('up', {
      paramValues: { channels: 'dapi', size: 3 },
      variants: { channels: { v1: 'gfp' } },
      sampleOverrides: { s1: { channels: 'cy5' } },
    });
    wire(['up', 'cells']);

    await lookup();

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('/api/contracts/feat.measure/output_options');
    expect(calls[0].body).toEqual({
      slot: 'table',
      version: 'id-v1',
      param_sets: [
        { channels: 'dapi', size: 3 }, { channels: 'gfp', size: 3 }, { channels: 'cy5', size: 3 },
      ],
    });
  });

  it('names no version for an upstream that follows the newest', async () => {
    nodes.set([methodNode('up', { x: 0, y: 0 }, UPSTREAM), methodNode('down', { x: 0, y: 0 }, DOWNSTREAM)]);
    wire(['up', 'cells']);
    await lookup();
    expect(calls[0].body).not.toHaveProperty('version');
  });

  it('sends a pipeline variable\'s value where the upstream holds its ref', async () => {
    nodes.set([methodNode('up', { x: 0, y: 0 }, UPSTREAM), methodNode('down', { x: 0, y: 0 }, DOWNSTREAM)]);
    pipelineVariables.set({ chan: { type: 'str', value: 'gfp' } });
    pendingBoundVariables.set({ 'up::channels': 'chan' });
    wire(['up', 'cells']);
    await lookup();
    expect(calls[0].body.param_sets).toEqual([{ channels: 'gfp' }]);
  });
});

describe('a Run Reference upstream\'s request', () => {
  it('names its run, and the path names no method', async () => {
    nodes.set([runReferenceNode('ref', { x: 0, y: 0 }, '412'), methodNode('down', { x: 0, y: 0 }, DOWNSTREAM)]);
    wire(['ref', 'cells']);
    const answer = await lookup();
    expect(calls).toEqual([{
      url: '/api/contracts/__run_reference__/output_options',
      body: { slot: 'table', run_id: '412' },
    }]);
    expect(answer.join_key.upstreams[0]).toMatchObject({ kind: 'run_reference', runId: '412' });
  });
});

describe('several upstreams', () => {
  it('are asked in options_from order, then edge order; a system node is listed but not asked', async () => {
    nodes.set([
      methodNode('m1', { x: 0, y: 0 }, UPSTREAM),
      methodNode('m2', { x: 0, y: 0 }, UPSTREAM),
      methodNode('m3', { x: 0, y: 0 }, UPSTREAM),
      inputSelectorNode('sel', { x: 0, y: 0 }),
      methodNode('down', { x: 0, y: 0 }, DOWNSTREAM),
    ]);
    updateNodeData('m3', { paramValues: { channels: 'cy5' } });
    wire(['m2', 'annotations'], ['m1', 'cells'], ['m3', 'annotations'], ['sel', 'cells']);

    const answer = await lookup();

    expect(answer.join_key.inputs).toEqual(['cells', 'annotations']);
    expect(answer.join_key.upstreams.map(u => [u.nodeId, u.input, u.kind, u.options !== null])).toEqual([
      ['m1', 'cells', 'method', true],
      ['sel', 'cells', 'system', false],
      ['m2', 'annotations', 'method', true],
      ['m3', 'annotations', 'method', true],
    ]);
    // m1 and m2 send the same request: it is asked once.
    expect(calls).toHaveLength(2);
  });

  it('a failed lookup answers null for that upstream only', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })));
    nodes.set([methodNode('up', { x: 0, y: 0 }, UPSTREAM), methodNode('down', { x: 0, y: 0 }, DOWNSTREAM)]);
    wire(['up', 'cells']);
    const answer = await lookup();
    expect(answer.join_key.upstreams[0].options).toBeNull();
  });
});

describe('the lookup key', () => {
  it('changes with an upstream\'s values, variants, overrides, pin and wiring only', () => {
    nodes.set([methodNode('up', { x: 0, y: 0 }, UPSTREAM), methodNode('down', { x: 0, y: 0 }, DOWNSTREAM)]);
    wire(['up', 'cells']);
    const keys = [optionsLookupKey(plan())];
    updateNodeData('down', { paramValues: { join_key: 'typed' } });
    nodes.update(ns => ns.map(n => (n.id === 'up' ? { ...n, position: { x: 50, y: 50 } } : n)));
    keys.push(optionsLookupKey(plan()));
    expect(keys[1]).toBe(keys[0]);

    for (const change of [
      { paramValues: { channels: 'dapi' } },
      { variants: { channels: { v1: 'gfp' } } },
      { sampleOverrides: { s1: { channels: 'cy5' } } },
      { version: 'id-v1' },
    ]) {
      updateNodeData('up', change);
      keys.push(optionsLookupKey(plan()));
    }
    wire(['up', 'annotations']);
    keys.push(optionsLookupKey(plan()));
    expect(new Set(keys.slice(1)).size).toBe(keys.length - 1);
  });
});
