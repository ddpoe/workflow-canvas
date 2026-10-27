/**
 * The one writer of a canvas node's data, one constructor per node type.
 *
 * Every creation path calls a constructor here — `App.svelte`'s `onDrop`
 * (through `nodeFromDragPayload`), the fixture seeder in `__fixtures__/seed.ts`,
 * `pipeline.ts::loadPipeline` and `pipeline.ts::graftRunReference` — so they
 * all agree about `nodeType`, `version` (the pin), the system nodes' labels and which
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
import type {
  CanvasNodeData, MethodDef, MethodVersionDef, NodeType, SlotDef,
} from '../shared/types.js';

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
  /** The document's pin: a version's content identity. */
  version?: string;
}

/**
 * The version a pin names: the newest entry carrying that identity (two
 * labels with one identity run the same code, so either is right; the
 * newest is the one shown).
 */
function versionByIdentity(
  versions: MethodVersionDef[] | undefined,
  identity: string | undefined,
): MethodVersionDef | undefined {
  if (!identity || !versions) return undefined;
  return [...versions].reverse().find(v => v.identity === identity);
}

/**
 * Which pin a node keeps and which contract it draws.
 *
 * A pin whose identity is the newest version's is no pin: the node follows
 * the newest, so a save writes nothing and a later version moves it. A pin
 * no listed version carries is kept as written, so the server refuses it by
 * name; the node draws the method's newest contract meanwhile.
 */
function resolvePin(
  versions: MethodVersionDef[] | undefined,
  identity: string | undefined,
): { version: string | undefined; contract: MethodVersionDef | undefined } {
  if (!identity) return { version: undefined, contract: undefined };
  const newest = versions?.find(v => v.newest);
  if (newest && newest.identity === identity) return { version: undefined, contract: newest };
  const pinned = versionByIdentity(versions, identity);
  return { version: identity, contract: pinned };
}

/**
 * The version a method node runs, for its number and colour: the pinned
 * version, or the newest when it follows the newest. ``undefined`` when the
 * method lists no versions (or the pin names none of them).
 */
export function nodeVersion(data: CanvasNodeData): MethodVersionDef | undefined {
  if (data.version) return versionByIdentity(data.versions, data.version);
  return data.versions?.find(v => v.newest);
}

/**
 * The hover text for the version a method node runs: the newest reads as the
 * latest, an older one names its pin and the newest. A pin naming no listed
 * version says so; a node of a method with no versions gets none.
 */
export function versionTitle(data: CanvasNodeData): string {
  const running = nodeVersion(data);
  if (!running) return data.version ? 'Pinned to a version this method does not list' : '';
  if (running.newest) return `Latest method version (v${running.label})`;
  // The modules route marks one version of every method the newest.
  const newest = data.versions?.find(v => v.newest);
  return `Pinned to method version v${running.label} · latest is v${newest?.label}`;
}

/**
 * The fields a version switch writes onto a method node: the pin (none for
 * the newest) and that version's slots and params. Parameter values stay as
 * they are; one the new version does not declare is flagged by
 * `undeclaredParams` until the user removes it.
 *
 * Args:
 *     data: The node's current data.
 *     identity: The picked version's content identity.
 */
export function versionSwitch(data: CanvasNodeData, identity: string): Partial<CanvasNodeData> {
  const { version, contract } = resolvePin(data.versions, identity);
  const update: Partial<CanvasNodeData> = { version };
  if (contract) {
    update.inputs = contract.inputs;
    update.outputs = contract.outputs;
    update.params = contract.params;
  }
  return update;
}

/**
 * The parameters a method node holds a value, variant or per-sample value
 * for that its version does not declare, in the order the node holds them.
 * A node that knows no contract to check against (its method lists no
 * versions, or its pin names none of them) flags nothing: the server
 * refuses such a node by name.
 */
export function undeclaredParams(data: CanvasNodeData): string[] {
  if (data.nodeType && data.nodeType !== 'method') return [];
  if (!nodeVersion(data)) return [];
  const declared = new Set((data.params ?? []).map(p => p.name));
  const held = [
    ...Object.keys(data.paramValues ?? {}),
    ...Object.keys(data.variants ?? {}),
    ...Object.values(data.sampleOverrides ?? {}).flatMap(Object.keys),
    ...Object.values(data.sampleVariants ?? {}).flatMap(Object.keys),
  ];
  return [...new Set(held)].filter(name => !declared.has(name));
}

/**
 * The fields that remove one parameter from a method node: its value, its
 * variants and every sample's own value and variants for it.
 *
 * Args:
 *     data: The node's current data.
 *     name: The parameter to remove.
 */
export function withoutParam(data: CanvasNodeData, name: string): Partial<CanvasNodeData> {
  const drop = <T>(record: Record<string, T> | undefined): Record<string, T> | undefined => {
    if (!record) return record;
    const { [name]: _dropped, ...rest } = record;
    return rest;
  };
  const perSample = <T>(
    bySample: Record<string, Record<string, T>> | undefined,
  ): Record<string, Record<string, T>> | undefined => bySample && Object.fromEntries(
    Object.entries(bySample).map(([sample, values]) => [sample, drop(values)!]),
  );
  return {
    paramValues: drop(data.paramValues) ?? {},
    variants: drop(data.variants),
    sampleOverrides: perSample(data.sampleOverrides),
    sampleVariants: perSample(data.sampleVariants),
  };
}

/** A method node's data, from the contract of the version it runs. */
export function methodNodeData(method: MethodDef, opts: MethodNodeOpts = {}): CanvasNodeData {
  const { version, contract } = resolvePin(method.versions, opts.version);
  return {
    label: opts.label ?? method.name,
    method: method.name,
    module: method.module,
    // The pin is the document's own field: a node that follows the newest
    // carries none, and the compiler writes one only for an older version.
    version,
    versions: method.versions,
    color: method.color ?? DEFAULT_METHOD_COLOR,
    inputs: contract?.inputs ?? method.inputs,
    outputs: contract?.outputs ?? method.outputs,
    params: contract?.params ?? method.params,
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
