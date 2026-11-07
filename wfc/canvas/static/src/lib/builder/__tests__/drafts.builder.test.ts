/**
 * Drafts in the Builder: a draft is a method node that runs its folder.
 *
 * The palette reads each module's drafts from `/api/modules`; a dropped draft
 * becomes a node through the one writer (`nodeData.ts`) and compiles to
 * `version: "draft"` with its folder. Every node downstream of a draft runs
 * in draft mode, owned by the nearest draft. Before a draft node is added,
 * and before Run or Validate, its folder is checked: a changed `method.yaml`
 * gives the node the new contract from the palette and refuses nothing (the
 * server reads the draft again), a gone draft adds no node. A server refusal
 * naming a node shows on that node. The Runs Preview counts draft-mode rows
 * as new, never cached.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import type { Edge, Node } from '@xyflow/svelte';
import { moduleDefsFromRegistry } from '../api';
import { methodNode, nodeFromDragPayload, versionSwitch, draftInvalidReason, nodeVersion } from '../nodeData';
import { compilePipelineToJSON } from '../compile';
import { draftModeOf, refreshDrafts, dropPaletteItem, stepsAfter } from '../drafts';
import { clearRefusals, refusalsByNode, showRefusals } from '../nodeRefusals';
import { blockedReasons } from '../lockFlow';
import { nodes, edges, modules, runState } from '../stores';
import { verdictsFor, DRAFT_MODE_REASON } from '../runsPreviewStatus';
import type { CanvasNodeData } from '../../shared/types';

const CONTRACT = {
  inputs: { data: { type: 'csv' } },
  outputs: { labeled: { type: 'csv' } },
  params_schema: { threshold: { type: 'float', default: 150 } },
};
const DRAFT_CONTRACT = {
  inputs: { data: { type: 'csv' } },
  outputs: { labeled: { type: 'csv' }, extra: { type: 'png' } },
  params_schema: { threshold: { type: 'float' }, label_column: { type: 'str' } },
};
const PALETTE = {
  demo: {
    description: '', color: '#6E5CD6',
    methods: {
      label: { ...CONTRACT, versions: [{ label: 3, identity: 'id-v3', newest: true, env: 'demo-env', ...CONTRACT }] },
      plot: { ...CONTRACT, versions: [{ label: 1, identity: 'id-p1', newest: true, ...CONTRACT }] },
    },
    drafts: {
      label: { kind: 'version', folder: 'C:/src/demo/label', valid: true, reasons: [], env: 'demo-env', ...DRAFT_CONTRACT },
      qc_report: { kind: 'method', folder: 'C:/src/demo/qc_report', valid: false, reasons: ['env wfc-qc-env is not registered'], ...CONTRACT },
    },
  },
};

function palette() {
  return moduleDefsFromRegistry(PALETTE as unknown as Record<string, unknown>)[0];
}
const at = { x: 0, y: 0 };
const edge = (source: string, target: string): Edge => ({ id: `${source}-${target}`, source, target });

afterEach(() => { vi.unstubAllGlobals(); nodes.set([]); edges.set([]); modules.set([]); });

describe('the palette lists each module\'s drafts', () => {
  it('a version draft rides on its method; a method draft is a method with no versions', () => {
    const mod = palette();
    expect(mod.drafts!.map(d => [d.name, d.draft!.kind, d.versions!.length])).toEqual([
      ['label', 'version', 1], ['qc_report', 'method', 0],
    ]);
    expect(mod.methods.find(m => m.name === 'label')!.draft!.folder).toBe('C:/src/demo/label');
    expect(mod.methods.map(m => m.name)).toEqual(['label', 'plot']);
  });
});

describe('a draft node runs its folder', () => {
  it('a dropped draft draws the draft\'s contract and compiles to version "draft" with its folder', () => {
    const label = palette().drafts![0];
    const node = nodeFromDragPayload('n1', at, { _draft: true, method: JSON.parse(JSON.stringify(label)) });
    expect(node.data.version).toBe('draft');
    expect(node.data.outputs.map(o => o.name)).toEqual(['labeled', 'extra']);
    expect(nodeVersion(node.data)).toBeUndefined();
    const doc = compilePipelineToJSON({ name: 'p', nodes: [node], edges: [], samples: [] });
    expect(doc.nodes[0]).toMatchObject({ version: 'draft', folder: 'C:/src/demo/label' });
  });

  it('switching to the newest version drops the folder; back to DRAFT restores it', () => {
    const label = palette().drafts![0];
    const data = methodNode('n1', at, label, { version: 'draft' }).data;
    const toV3 = { ...data, ...versionSwitch(data, 'id-v3') };
    expect(toV3.version).toBeUndefined();
    expect(toV3.folder).toBeUndefined();
    expect(toV3.outputs.map(o => o.name)).toEqual(['labeled']);
    const back = { ...toV3, ...versionSwitch(toV3, 'draft') };
    expect(back).toMatchObject({ version: 'draft', folder: 'C:/src/demo/label' });
  });

  it('an invalid draft names its reason; a draft the palette no longer lists says so', () => {
    const qc = palette().drafts![1];
    expect(draftInvalidReason(methodNode('n', at, qc, { version: 'draft' }).data))
      .toBe('qc_report: env wfc-qc-env is not registered');
    const gone = methodNode('n', at, { ...qc, draft: undefined }, { version: 'draft', folder: 'C:/x' }).data;
    expect(gone.folder).toBe('C:/x');
    expect(draftInvalidReason(gone)).toBe('qc_report: this draft is no longer in the Registry.');
  });
});

describe('draft mode reaches every node downstream of a draft', () => {
  it('downstream nodes belong to the nearest draft; upstream nodes are not in draft mode', () => {
    const mod = palette();
    const plot = mod.methods[1];
    const ns = [
      methodNode('up', at, plot), methodNode('d1', at, mod.drafts![0], { version: 'draft' }),
      methodNode('a', at, plot), methodNode('d2', at, mod.drafts![0], { version: 'draft' }),
      methodNode('b', at, plot),
    ];
    const mode = draftModeOf(ns, [edge('up', 'd1'), edge('d1', 'a'), edge('a', 'd2'), edge('d2', 'b')]);
    expect(mode.has('up')).toBe(false);
    expect(mode.get('d1')).toMatchObject({ isDraft: true });
    expect(mode.get('a')).toMatchObject({ isDraft: false, ownerId: 'd1', owner: 'label' });
    expect(mode.get('b')).toMatchObject({ isDraft: false, ownerId: 'd2' });
    expect(stepsAfter(mode, 'd1')).toBe(1);
  });
});

describe('the folder check before Run, Validate and a drop', () => {
  let state: string;
  let contractChanged: boolean;
  let served: unknown;
  beforeEach(() => {
    state = 'current';
    contractChanged = false;
    served = PALETTE;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('snapshot-check')) {
        return new Response(JSON.stringify({ drafts: [{ module: 'demo', method: 'label', state, contractChanged, filesUpdated: false }] }));
      }
      return new Response(JSON.stringify(served));
    }));
  });

  function canvasWithDraft(): Node<CanvasNodeData> {
    const node = methodNode('n1', at, palette().drafts![0], { version: 'draft', paramValues: { label_column: 'cluster' } });
    nodes.set([node]);
    return node;
  }

  it('a changed method.yaml takes the new contract from the palette and blocks nothing; the values stay', async () => {
    canvasWithDraft();
    contractChanged = true;
    // method.yaml now declares a `mask` output and no longer `label_column`.
    const draft = PALETTE.demo.drafts.label;
    served = { demo: { ...PALETTE.demo, drafts: { ...PALETTE.demo.drafts, label: {
      ...draft, outputs: { ...draft.outputs, mask: { type: 'png' } }, params_schema: { threshold: { type: 'float' } } } } } };
    await refreshDrafts();
    const data = get(nodes)[0].data;
    expect(data.outputs.map(o => o.name)).toEqual(['labeled', 'extra', 'mask']);
    expect(data.params.map(p => p.name)).toEqual(['threshold']);
    expect(data.draft!.outputs.map(o => o.name)).toContain('mask');
    // Nothing is dropped and Run is not blocked: the server refuses the param.
    expect(data.paramValues).toEqual({ label_column: 'cluster' });
    expect(get(blockedReasons)).toEqual([]);
    const doc = compilePipelineToJSON({ name: 'p', nodes: get(nodes), edges: [], samples: [] });
    expect(doc.nodes[0].params).toEqual({ label_column: 'cluster' });
  });

  it('Run reads the draft again before the required-params check: a param the new contract no longer requires does not refuse Run', async () => {
    const root = await import('../../machines/root');
    const dispatched = vi.spyOn(root, 'dispatchRun').mockImplementation(() => {});
    // The node was drawn from a contract that required `label_column` and has no value for it.
    const draft = PALETTE.demo.drafts.label;
    const node = methodNode('n1', at, palette().drafts![0], { version: 'draft' });
    node.data.params = [...node.data.params,
      { name: 'must_set', type: 'str', required: true } as (typeof node.data.params)[number]];
    nodes.set([node]);
    const { startRun, missingRequired } = await import('../lockFlow');
    expect(missingRequired()).toEqual(['label.must_set is required']);
    // method.yaml now declares only `threshold`, with a default.
    contractChanged = true;
    served = { demo: { ...PALETTE.demo, drafts: { ...PALETTE.demo.drafts, label: {
      ...draft, params_schema: { threshold: { type: 'float', default: 1 } } } } } };
    await startRun();
    expect(get(runState).pipelineError).toBeNull();
    expect(dispatched).toHaveBeenCalledTimes(1);
    dispatched.mockRestore();
  });

  it('an invalid draft is shown on its node; the server decides whether it runs', async () => {
    canvasWithDraft();
    state = 'invalid';
    await refreshDrafts();
    expect(get(nodes)[0].data).toMatchObject({ draftState: 'invalid', draftMessage: "label: this draft's Registration check fails." });
  });

  it('a draft that is gone adds no node; a current one is added', async () => {
    const label = palette().drafts![0];
    state = 'missing';
    expect(await dropPaletteItem('n1', at, { _draft: true, method: label })).toContain('gone');
    expect(get(nodes)).toHaveLength(0);
    state = 'current';
    expect(await dropPaletteItem('n1', at, { _draft: true, method: label })).toBe('');
    expect(get(nodes).map(n => n.data.version)).toEqual(['draft']);
  });
});

describe('a server refusal shows on the node it names', () => {
  it('each refusal lands on its node, by its node id or its leading Node \'<id>\'; the next Validate or Run clears them', () => {
    const mod = palette();
    nodes.set([methodNode('n1', at, mod.drafts![0], { version: 'draft' }), methodNode('n2', at, mod.methods[1])]);
    const param = "Node 'n1' (demo.label draft): parameter 'label_column' is not declared by draft; remove it";
    const unwired = "Node 'n1': label declares input 'mask', which nothing is connected to (its inputs: data, mask).";
    const byNode = refusalsByNode(
      [param, unwired, 'Pipeline has no samples', param],
      [{ nodeId: 'n2', message: "Node 'n2' (demo.plot): its draft run is still running" }],
    );
    expect(byNode).toEqual({ n1: [param, unwired], n2: ["Node 'n2' (demo.plot): its draft run is still running"] });
    showRefusals(byNode);
    expect(get(nodes).map(n => n.data.refusals)).toEqual([[param, unwired], byNode.n2]);
    showRefusals(refusalsByNode([param]));
    expect(get(nodes)[1].data.refusals).toBeUndefined();
    clearRefusals();
    expect(get(nodes).map(n => n.data.refusals)).toEqual([undefined, undefined]);
  });

  it('a Run the server refuses with 409 draft_refused marks each refusing node and fails with that kind and status', async () => {
    const mod = palette();
    nodes.set([methodNode('n1', at, mod.drafts![0], { version: 'draft' }), methodNode('n2', at, mod.methods[1])]);
    const refusal = "Node 'n1' (demo.label draft): its draft run is still running";
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/api/workflow/validate')) return new Response(JSON.stringify({ valid: true }));
      return new Response(JSON.stringify({ detail: {
        kind: 'draft_refused', message: refusal, hint: 'Fix each draft in the Registry, then click Run again.',
        refusals: [{ nodeId: 'n1', message: refusal }],
      } }), { status: 409 });
    }));
    const { createActor, toPromise } = await import('xstate');
    const { submitPipeline } = await import('../../machines/services');
    const doc = compilePipelineToJSON({ name: 'p', nodes: get(nodes), edges: [], samples: [] });
    const actor = createActor(submitPipeline, { input: { pipeline: doc } });
    actor.start();
    await expect(toPromise(actor)).rejects.toMatchObject({
      kind: 'draft_refused', status: 409, message: 'Cannot run — drafts refused:', items: [refusal],
    });
    expect(get(nodes).map(n => n.data.refusals)).toEqual([[refusal], undefined]);
  });
});

describe('the Runs Preview counts draft-mode rows as new', () => {
  it('a draft-mode row the cache would hit is new, with no source run', () => {
    const row = {
      key: 'n1::s::default', node_id: 'n1', sample: 's', variant: 'default',
      status: 'cached_local' as const, reason: null, cache_key: 'k', source_run_id: 7, source_nid: 'x', outputs: [],
    };
    const v = verdictsFor([row.key], { rows: [{ ...row, draft_mode: true }] });
    expect(v[row.key]).toMatchObject({ status: 'new_step_changed', reason: DRAFT_MODE_REASON, sourceRunId: null });
    const recorded = verdictsFor([row.key], { rows: [{ ...row, draft_mode: false }] });
    expect(recorded[row.key].status).toBe('cached_local');
  });
});
