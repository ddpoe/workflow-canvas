/**
 * A dropped, a loaded and a grafted node are the same node
 * (canvas-ui case `catalog.builder-node-data`).
 *
 * A *loaded* and a *grafted* run reference agree, and so do the drop rows:
 * a method, an input selector and a run reference dropped from the sidebar
 * against their loaded or grafted twins.
 *
 * Existing coverage this does NOT duplicate:
 *   - `pipeline.reload.test.ts` covers `graftRunReference`'s own shape.
 *     The claim that a loaded and a grafted node agree — as data and as
 *     compiled output — is only here.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { nodes, edges, pipelineName, resetNodeCounter, modules } from '../stores';
import { loadPipeline, graftRunReference } from '../pipeline';
import { compilePipelineToJSON } from '../compile';
import { nodeFromDragPayload } from '../nodeData';
import type { MethodDef, PipelineJSON, PipelineNode } from '../../shared/types';

const RUN_ID = 'r_42';

beforeEach(() => {
  nodes.set([]);
  edges.set([]);
  pipelineName.set('My Pipeline');
  resetNodeCounter(0);
  modules.set([]);
});

/** A one-node document holding a run reference, as a save produces it. */
function savedDocWithRunReference(): PipelineJSON {
  return {
    name: 'p',
    samples: [],
    links: [],
    nodes: [
      {
        id: 'node_1',
        type: 'run_reference',
        method: '',
        module: '',
        params: {},
        position: { x: 10, y: 20 },
        run_id: RUN_ID,
      },
    ],
  };
}

/** The compiled entry with the two facts that are allowed to differ. */
function compiledNodeSansIdentity(nodeId: string): Omit<PipelineNode, 'id' | 'position'> {
  const doc = compilePipelineToJSON({
    name: 'p',
    nodes: get(nodes).filter(n => n.id === nodeId),
    edges: [],
    samples: [],
  });
  expect(doc.nodes).toHaveLength(1);
  const { id: _id, position: _position, ...rest } = doc.nodes[0];
  return rest;
}

describe('a loaded and a grafted run reference are the same node', () => {
  it('carry the same data record', () => {
    loadPipeline(savedDocWithRunReference());
    const loaded = get(nodes).find(n => n.id === 'node_1');
    expect(loaded).toBeDefined();

    const graftedId = graftRunReference(RUN_ID);
    const grafted = get(nodes).find(n => n.id === graftedId);
    expect(grafted).toBeDefined();

    // Position and id are the two facts a creation path is entitled to
    // decide for itself; everything else in the record must agree.
    expect(grafted!.data).toEqual(loaded!.data);
    // And both name the run they reference.
    expect(grafted!.data.selectedRunId).toBe(RUN_ID);
    expect(grafted!.data.nodeType).toBe('run_reference');
  });

  it('compile to the same sparse document entry', () => {
    loadPipeline(savedDocWithRunReference());
    const graftedId = graftRunReference(RUN_ID);

    expect(compiledNodeSansIdentity(graftedId)).toEqual(compiledNodeSansIdentity('node_1'));
    // The entry carries the run id and no singular output slot — each
    // outgoing edge names its source slot instead.
    expect(compiledNodeSansIdentity('node_1')).toMatchObject({
      type: 'run_reference',
      run_id: RUN_ID,
    });
  });
});

// ── the drop rows ─────────────────────────────────────────────────────────
//
// Each row below exercises the drop through the same entry point
// `App.svelte::onDrop` uses:
// `nodeFromDragPayload` with the literal payload `Sidebar.svelte` puts on the
// DataTransfer. The only part of a drop not represented here is the
// DragEvent that supplies the payload and the pointer position.

/** The payload the sidebar drags for a method: its bare `MethodDef`. */
const DRAGGED_METHOD: MethodDef = {
  name: 'norm',
  module: 'mod_a',
  version: '2.1',
  color: '#4A90D9',
  inputs: [{ name: 'data', type: 'csv' }],
  outputs: [{ name: 'output', type: 'csv' }],
  params: [{ name: 'alpha', type: 'number', required: true }],
};

/**
 * The payloads the sidebar drags for the two system nodes. `name` is the
 * palette's own text, which the sidebar sets and no writer reads.
 */
const DRAGGED_INPUT_SELECTOR = { _systemNode: true, nodeType: 'input_selector', name: 'Sample Input' };
const DRAGGED_RUN_REFERENCE = { _systemNode: true, nodeType: 'run_reference', name: 'Run Output' };

describe('a dropped node is the same node as a loaded one', () => {
  it('a dropped method is a method node, so every method-only reader sees it', () => {
    const dropped = nodeFromDragPayload('node_1', { x: 10, y: 20 }, DRAGGED_METHOD);

    // `nodeType` is what the method-only readers key on: the Inspector's
    // method definition, its input and output wiring lists, the column
    // lookup and the output tab's streaming effect, and
    // `Toolbar.svelte::collectRequiredErrors`, which refuses a blank
    // required param only on a node it recognises as a method. The compiler
    // defaults a missing `nodeType` to 'method', so without this the two
    // disagree about the same node.
    expect(dropped.data.nodeType).toBe('method');
  });

  it('a loaded method carries its version, so the chip survives a reload', () => {
    // The method is in the registry, which is the only place a version can
    // come from: nothing saves it — the compiler never writes a version and
    // no document carries one.
    modules.set([{ name: 'mod_a', color: '#4A90D9', methods: [DRAGGED_METHOD] }]);
    loadPipeline({
      name: 'p',
      samples: [],
      links: [],
      nodes: [{
        id: 'node_1', type: 'method', method: 'norm', module: 'mod_a',
        params: {}, position: { x: 0, y: 0 },
      }],
    });

    // Without this the version chip on the node and in the Inspector header
    // is there on a dropped method and gone on the same method reloaded.
    expect(get(nodes).find(n => n.id === 'node_1')!.data.version).toBe('2.1');
  });

  it('a dropped system node wears the name the documentation calls it by', () => {
    const selector = nodeFromDragPayload('node_1', { x: 0, y: 0 }, DRAGGED_INPUT_SELECTOR);
    const reference = nodeFromDragPayload('node_2', { x: 0, y: 0 }, DRAGGED_RUN_REFERENCE);

    // The label is the node's own title on the canvas and in the Inspector
    // header, and it is not saved: the compiler writes no label for a system
    // node, so a reload renames a dropped one. Every other writer — the
    // loader, the fixture and the graft — uses these two names, and
    // so does the user documentation.
    expect(selector.data.label).toBe('Input Selector');
    expect(reference.data.label).toBe('Run Reference');
  });

  it('a dropped run reference is the same record as a grafted one', () => {
    const dropped = nodeFromDragPayload('node_1', { x: 10, y: 20 }, DRAGGED_RUN_REFERENCE);

    const graftedId = graftRunReference(RUN_ID);
    const grafted = get(nodes).find(n => n.id === graftedId)!;

    // The run it names is the only fact that may differ: a graft knows its
    // run and a drop does not yet. Everything else must match, which means
    // a dropped run reference carries none of the input selector's fields —
    // `selectedSamples`, `fanMode`, `inputCollapsed`.
    expect(dropped.data).toEqual({ ...grafted.data, selectedRunId: undefined });
  });

  it('a dropped method and a loaded one are the same record and compile alike', () => {
    // The registry is the only source of a version, and it is also where the
    // sidebar the method was dragged from gets its methods, so a loaded node
    // and a dropped one read the same contract.
    modules.set([{ name: 'mod_a', color: '#4A90D9', methods: [DRAGGED_METHOD] }]);
    loadPipeline({
      name: 'p',
      samples: [],
      links: [],
      nodes: [{
        id: 'node_1',
        type: 'method',
        method: 'norm',
        module: 'mod_a',
        params: {},
        position: { x: 10, y: 20 },
      }],
    });
    const loaded = get(nodes).find(n => n.id === 'node_1')!;
    const loadedEntry = compiledNodeSansIdentity('node_1');

    const dropped = nodeFromDragPayload('node_1', { x: 10, y: 20 }, DRAGGED_METHOD);

    // The whole record, not just `nodeType`, `version` and label: a reader
    // that keys on any of them sees one node, however it was created.
    expect(dropped.data).toEqual(loaded.data);

    nodes.set([dropped]);
    expect(compiledNodeSansIdentity('node_1')).toEqual(loadedEntry);
  });

  it('a dropped input selector and a loaded one are the same record and compile alike', () => {
    // A document stating neither fan mode nor keep-going — the sparse shape a
    // hand-written or older document has. What a document that *does* state
    // the flag round-trips is the row below.
    loadPipeline({
      name: 'p',
      samples: [],
      links: [],
      nodes: [{
        id: 'node_1',
        type: 'input_selector',
        method: '',
        module: '',
        params: {},
        position: { x: 10, y: 20 },
      }],
    });
    const loaded = get(nodes).find(n => n.id === 'node_1')!;
    const loadedEntry = compiledNodeSansIdentity('node_1');

    const dropped = nodeFromDragPayload('node_1', { x: 10, y: 20 }, DRAGGED_INPUT_SELECTOR);

    expect(dropped.data).toEqual(loaded.data);

    nodes.set([dropped]);
    expect(compiledNodeSansIdentity('node_1')).toEqual(loadedEntry);
  });

  it('a dropped input selector states no keep-going flag, so the compiler derives it', () => {
    const dropped = nodeFromDragPayload('node_1', { x: 0, y: 0 }, DRAGGED_INPUT_SELECTOR);

    // The posted document is frozen. A selector created without a flag
    // carries none, and `compile.ts` derives `keep_going` from the *live*
    // fan mode: true while it fans out, false once the user flips it to
    // fan-in, where the flag is a no-op (one bundled job). A default written
    // at creation would post `true` for that flipped node, which is a
    // different document from the one the canvas posts for it.
    nodes.set([dropped]);
    expect(compiledNodeSansIdentity('node_1')).toMatchObject({
      fan_mode: 'out',
      keep_going: true,
    });

    nodes.set([{ ...dropped, data: { ...dropped.data, fanMode: 'in' } }]);
    expect(compiledNodeSansIdentity('node_1')).toMatchObject({
      fan_mode: 'in',
      keep_going: false,
    });

    expect(dropped.data.keepGoing).toBeUndefined();
  });

  it('a loaded input selector carries exactly what its document declared', () => {
    loadPipeline({
      name: 'p',
      samples: [],
      links: [],
      nodes: [
        {
          id: 'node_1',
          type: 'input_selector',
          method: '',
          module: '',
          params: {},
          position: { x: 0, y: 0 },
          fan_mode: 'out',
          keep_going: false,
        },
      ],
    } as unknown as PipelineJSON);

    // A document that states the flag round-trips it: the node carries the
    // stated value and the next save writes it back, fan-out and all.
    expect(get(nodes).find(n => n.id === 'node_1')!.data.keepGoing).toBe(false);
    expect(compiledNodeSansIdentity('node_1')).toMatchObject({
      fan_mode: 'out',
      keep_going: false,
    });
  });
});
