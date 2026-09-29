/**
 * The one writer of a canvas node's data, one constructor per node type.
 *
 * Every creation path calls a constructor here — `App.svelte`'s `onDrop`
 * (through `nodeFromDragPayload`), the fixture seeder in `__fixtures__/seed.ts`,
 * `pipeline.ts::loadPipeline` and `pipeline.ts::graftRunReference` — so they
 * all agree about `nodeType`, `version`, the system nodes' labels and which
 * fields each type carries.
 *
 * Each constructor takes only source facts — a `MethodDef`, a run id, the
 * drag payload's colour — plus the id and position, which stay the caller's:
 * a creation path is entitled to decide where its node lands.
 * `loadPipeline` reads the position from the document, `graftRunReference`
 * places itself right of the selection, and a drop uses the pointer.
 *
 * This module never replaces a node in the store. `stores.ts::updateNodeData`
 * mutates a node's data in place so Svelte Flow keeps a dragged node's
 * position, and it remains the only writer of an existing node's data.
 */
import type { Node } from '@xyflow/svelte';
import type { CanvasNodeData, MethodDef, NodeType, SlotDef } from '../shared/types.js';

/** Every system node wears the same teal. */
const SYSTEM_COLOR = '#1ABC9C';

/** What a method node wears when its module declares no colour. */
const DEFAULT_METHOD_COLOR = '#2ecc71';

/** Where a node lands, in flow coordinates. */
export interface NodePosition {
  x: number;
  y: number;
}

/**
 * The placeholder slot a system node shows before it knows its real ones —
 * an input selector until samples are picked, a run reference until the
 * Inspector fetches the run it names. A fresh array per node: two nodes must
 * never share one slot list.
 */
function placeholderOutputs(): SlotDef[] {
  return [{ name: 'output', type: 'csv' }];
}

// ---------- Method node ----------

export interface MethodNodeOpts {
  /** The document's own label. Defaults to the method's name. */
  label?: string;
  paramValues?: Record<string, unknown>;
  datasource?: string;
  variants?: Record<string, Record<string, unknown>>;
  sampleOverrides?: Record<string, Record<string, unknown>>;
  sampleVariants?: Record<string, Record<string, Record<string, unknown>>>;
}

/** A method node's data, from the method's contract. */
export function methodNodeData(method: MethodDef, opts: MethodNodeOpts = {}): CanvasNodeData {
  return {
    label: opts.label ?? method.name,
    method: method.name,
    module: method.module,
    // Wherever the method is known, so the chip survives a reload. A
    // document never carries a version and the compiler never writes one:
    // the registry is the only source.
    version: method.version,
    color: method.color ?? DEFAULT_METHOD_COLOR,
    inputs: method.inputs,
    outputs: method.outputs,
    params: method.params,
    paramValues: opts.paramValues ?? {},
    runStatus: 'idle',
    expanded: false,
    nodeType: 'method',
    datasource: opts.datasource,
    variants: opts.variants,
    sampleOverrides: opts.sampleOverrides,
    sampleVariants: opts.sampleVariants,
  };
}

// ---------- Input selector ----------

export interface InputSelectorNodeOpts {
  selectedSamples?: string[];
  fanMode?: 'out' | 'in';
  /**
   * The keep-going flag the caller's own source declares — in practice a
   * loaded document that states `keep_going`. Omitted by every other writer;
   * see the tail of `inputSelectorNodeData` for why.
   */
  keepGoing?: boolean;
}

/** An input selector's data. */
export function inputSelectorNodeData(opts: InputSelectorNodeOpts = {}): CanvasNodeData {
  const fanMode = opts.fanMode ?? 'out';
  const data: CanvasNodeData = {
    // The name the user documentation uses, from every writer. A system
    // node's label is not saved — the compiler writes none — so a node
    // labelled anything else is renamed by its own reload.
    label: 'Input Selector',
    method: '',
    module: '',
    color: SYSTEM_COLOR,
    inputs: [],
    outputs: placeholderOutputs(),
    params: [],
    paramValues: {},
    runStatus: 'idle',
    expanded: false,
    nodeType: 'input_selector',
    selectedSamples: opts.selectedSamples ?? [],
    fanMode,
    inputCollapsed: false,
  };
  // The flag is written only when the caller declares it, so a document that
  // states `keep_going` round-trips it and every other selector carries none.
  // A missing flag is derived by its readers — `compile.ts` from the *live*
  // `fanMode`, `machines/services.ts` from its own `?? true`, which it reads
  // only on a fan-out selector, where the two agree. Defaulting it here
  // instead would freeze the value at creation: a selector dropped fan-out
  // and later flipped to fan-in would post `keep_going: true` where the
  // canvas posts `false`, and the posted document is frozen.
  if (opts.keepGoing !== undefined) data.keepGoing = opts.keepGoing;
  return data;
}

// ---------- Run reference ----------

export interface RunReferenceNodeOpts {
  /**
   * The slots the referenced run produced. A loaded document shows a
   * placeholder until the Inspector fetches the run.
   */
  outputs?: SlotDef[];
}

/** A run reference's data, naming the finished run it reads outputs from. */
export function runReferenceNodeData(
  runId: string | undefined,
  opts: RunReferenceNodeOpts = {},
): CanvasNodeData {
  return {
    // The name the user documentation uses, from every writer. Accepting a
    // run in the Inspector renames the node after the run it references,
    // which is a different, user-visible act and stays as it is.
    label: 'Run Reference',
    // A run reference runs no method. `CanvasNodeData.method` is not
    // optional, so the field is blank rather than absent.
    method: '',
    module: '',
    color: SYSTEM_COLOR,
    inputs: [],
    outputs: opts.outputs ?? placeholderOutputs(),
    params: [],
    paramValues: {},
    runStatus: 'idle',
    expanded: false,
    nodeType: 'run_reference',
    selectedRunId: runId,
    // No `selectedSamples`, `fanMode` or `inputCollapsed`: those are the
    // input selector's fields.
  };
}

// ---------- The nodes themselves ----------

/**
 * `origin: [0, 0]` is Svelte Flow's own default, so stating it on every node
 * does not change where anything lands.
 */
function nodeOf(id: string, position: NodePosition, data: CanvasNodeData): Node<CanvasNodeData> {
  return { id, type: 'custom', position, origin: [0, 0], data };
}

export function methodNode(
  id: string,
  position: NodePosition,
  method: MethodDef,
  opts: MethodNodeOpts = {},
): Node<CanvasNodeData> {
  return nodeOf(id, position, methodNodeData(method, opts));
}

export function inputSelectorNode(
  id: string,
  position: NodePosition,
  opts: InputSelectorNodeOpts = {},
): Node<CanvasNodeData> {
  return nodeOf(id, position, inputSelectorNodeData(opts));
}

export function runReferenceNode(
  id: string,
  position: NodePosition,
  runId: string | undefined,
  opts: RunReferenceNodeOpts = {},
): Node<CanvasNodeData> {
  return nodeOf(id, position, runReferenceNodeData(runId, opts));
}

// ---------- The drop ----------

/**
 * What `Sidebar.svelte` puts on the DataTransfer for one of the two system
 * nodes. A method is dragged as its bare `MethodDef`.
 */
interface SystemNodePayload {
  _systemNode: true;
  nodeType: NodeType;
  /**
   * The sidebar palette's own name for the node. No writer reads it: a
   * dropped node carries the same label as every other writer's.
   */
  name: string;
}

/**
 * The node a dropped sidebar item becomes.
 *
 * This is the drop path's data construction, and the only part of it that is
 * not a DragEvent: `App.svelte::onDrop` reads the payload and the pointer
 * position and calls this. A test can therefore create the node a drop
 * creates by passing the literal payload the sidebar sets.
 */
export function nodeFromDragPayload(
  id: string,
  position: NodePosition,
  payload: unknown,
): Node<CanvasNodeData> {
  if ((payload as SystemNodePayload)?._systemNode) {
    const system = payload as SystemNodePayload;
    if (system.nodeType === 'run_reference') {
      return runReferenceNode(id, position, undefined);
    }
    return inputSelectorNode(id, position);
  }
  return methodNode(id, position, payload as MethodDef);
}
