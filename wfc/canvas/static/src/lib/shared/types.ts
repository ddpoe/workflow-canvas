/**
 * Pipeline and canvas type definitions.
 * Matches the backend PipelineDef / StepDef schema from wfc/graph/.
 */

// ---------- Sample axis ----------

/**
 * The sample identity a sample-collapsed (fan-in) run carries. Mirrors
 * `wfc/contracts/vocabulary.py::COLLAPSED_SAMPLE`: the backend writes this value to
 * the run row and the canvas compares against it. It is an identity, not
 * a display string -- what the canvas shows for a collapsed run is decided
 * where it renders.
 */
export const COLLAPSED_SAMPLE = '__all__';

// ---------- Module / Method registry ----------

export interface SlotDef {
  name: string;
  type: string;       // e.g. "csv", "parquet", "label"
  multi?: boolean;     // fan-in allowed
  description?: string;
}

interface ParamConstraints {
  enum?: string[];
  min?: number;
  max?: number;
}

/**
 * The parameter type vocabulary a `method.yaml` declares, the same list the
 * server checks against (`wfc/contracts/vocabulary.py::PARAM_TYPES`).
 */
export const PARAM_TYPES = ['str', 'int', 'float', 'bool', 'list', 'dict', 'any'] as const;

/**
 * Raw contract type as declared in `method.yaml` (source of truth is
 * `wfc/contracts/declarations.py::parse_method_yaml`).  `any` is unchecked.
 * `unknown` means the param was not found in the method contract at all,
 * or its type is outside the vocabulary — values are treated as
 * pass-through strings.
 */
export type ContractType = (typeof PARAM_TYPES)[number] | 'unknown';

export interface ParamDef {
  name: string;
  /** The type as the contract spells it (e.g. `int`, `any`); the Inspector shows it. */
  type: string;
  /** Raw contract type from method.yaml, narrowed to the vocabulary. Drives the editor and its check. */
  contractType?: ContractType;
  /** Native default — string for primitives, native dict/array for list/dict params. */
  default?: unknown;
  description?: string;
  required?: boolean;
  constraints?: ParamConstraints;
  /**
   * The input (or inputs, in order) whose upstream outputs offer this
   * param's options. When set, the Inspector renders the string row with the
   * options picker, listing what each upstream wired to those inputs declares
   * in its output's `options`. Suggestions only: free text is always accepted.
   */
  options_from?: string | string[];
}

// ---------- Pipeline Variables ----------

/**
 * A pipeline variable: named value declared once in the Pipeline Variables
 * panel and bindable to N param rows via `{$var: name}` refs. The `type`
 * field is used by the bind picker to grey out type-incompatible
 * variables; substitution is whole-value, no coercion.
 */
export interface PipelineVariable {
  type: ContractType | string;
  value: unknown;
  description?: string;
}

export type PipelineVariables = Record<string, PipelineVariable>;

/**
 * Variable reference embedded in a node's params or a param_sets variant.
 * Server-side `wfc/graph/variables.py::resolve_variables` substitutes
 * these whole-value with the named variable's value before _enrich_pipeline.
 */
export interface VarRef {
  $var: string;
}

/**
 * One registered version of a method, as `/api/modules` lists it: its
 * display label, the content identity a pin holds, and that version's own
 * contract.
 */
export interface MethodVersionDef {
  /** The display label (`v{label}`); never what a pin names. */
  label: number;
  /** The version's content identity: the value a node's `version` holds. */
  identity: string;
  /** True on the method's newest version only. */
  newest: boolean;
  registeredAt?: string;
  env?: string;
  inputs: SlotDef[];
  outputs: SlotDef[];
  params: ParamDef[];
}

/**
 * A method's draft, as the palette's Drafts group lists it: a method folder
 * the canvas runs as it is on disk, with the contract its `method.yaml`
 * declared when the draft was last read (added or updated in the Registry).
 *
 * A `version` draft is the next version of the registered method of the same
 * name in its module; a `method` draft is a new method of the module. A draft
 * is not a version: a node runs it with `version: "draft"` and its folder.
 */
export interface DraftDef {
  module: string;
  method: string;
  kind: 'version' | 'method';
  /** The method folder the draft's pointer names. */
  folder: string;
  /** False when the draft's Registration check fails; `reasons` says why. */
  valid: boolean;
  reasons: string[];
  env?: string;
  description?: string;
  inputs: SlotDef[];
  outputs: SlotDef[];
  params: ParamDef[];
}

export interface MethodDef {
  name: string;
  module: string;
  /** Every registered version in label order, newest last. */
  versions?: MethodVersionDef[];
  /** The method's draft, when it has one (one draft per method). */
  draft?: DraftDef;
  description?: string;
  script_path?: string;
  inputs: SlotDef[];
  outputs: SlotDef[];
  params: ParamDef[];
}

/** A palette module. Its colour is read through `moduleColor(name)`. */
export interface ModuleDef {
  name: string;
  description?: string;
  methods: MethodDef[];
  /**
   * The module's drafts, each as the method a node runs (a `version` draft
   * is its registered method's own entry; a `method` draft lists no versions).
   */
  drafts?: MethodDef[];
}

// ---------- Node type discriminator ----------

export type NodeType = 'method' | 'input_selector' | 'run_reference';

// ---------- Pipeline JSON (backend-compatible) ----------

export interface PipelineNode {
  id: string;
  type?: NodeType;
  method: string;
  module?: string;
  /** A method node's pin: an older version's content identity, or `"draft"`. */
  version?: string;
  /** A draft node's method folder (only with `version: "draft"`). */
  folder?: string;
  params: Record<string, unknown>;
  position?: { x: number; y: number };
  // input_selector fields
  samples?: string[];
  source?: string;
  /** Per-input-selector dispatch mode: 'out' = parallel runs, 'in' = bundled. */
  fan_mode?: 'out' | 'in';
  // run_reference fields
  run_id?: string;
}

export interface PipelineLink {
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
}

/**
 * Per-node param variants expressed as the engine expects them.
 *
 * Shape: `{ node_id: { variant_name: { param_name: value } } }`.
 *
 * Used verbatim by the backend (loaded by `wfc/graph/`, emitted by
 * `wfc/orchestration/snakemake.py`) — the canvas compiles its
 * richer authoring state (variants + sampleOverrides) into this
 * structure before POSTing to `/api/workflow/run`.
 */
export type ParamSets = Record<string, Record<string, Record<string, unknown>>>;

/**
 * An explicit (sample, variant) combo row used to bind a variant to a
 * specific sample.  Single-sample overrides compile into one explicit
 * combo plus a matching `param_sets` variant named `{sample}__o{n}`.
 */
export interface ExplicitCombo {
  sample: string;
  variant: string;
  [k: string]: unknown;
}

export type PipelineJSON = {
  name?: string;
  nodes: PipelineNode[];
  links: PipelineLink[];
  samples: string[];
  /** Per-node sweep variants.  Passes straight through to the engine. */
  param_sets?: ParamSets;
  /** Explicit (sample, variant) binding rows.  Passes straight through. */
  explicit_combos?: ExplicitCombo[];
  /**
   * Pipeline variables. Pre-substitution form only —
   * server-side `resolve_variables` strips this block and inlines each
   * `{$var: name}` ref before _enrich_pipeline. Persists to
   * `pipeline.editable.json` so History "Open in canvas" can rehydrate
   * the Pipeline Variables panel and per-row binding chips.
   */
  variables?: PipelineVariables;
};

// ---------- Run state ----------

export type RunStatus = 'idle' | 'pending' | 'running' | 'completed' | 'cached' | 'failed' | 'cancelled' | 'mixed';

export interface RunTally {
  running: number;
  completed: number;
  failed: number;
  [key: string]: number;  // allow unknown/other buckets the backend may add
}

/**
 * Pipeline-level error surfaced to the canvas: raised by pre_run before
 * any step runs (dirty repo, missing method, etc.) so there's no per-node
 * Run row to attach it to.
 * ``kind`` lets the UI pick an icon / inline affordance; ``hint`` is an
 * optional second sentence separated from ``message``.
 */
export interface PipelineError {
  message: string;
  kind?:
    | 'dirty_repo'
    | 'not_found'
    | 'not_runnable_docker'
    | 'not_runnable_git'
    | 'unknown'
    | string;
  /**
   * The lines under `message` when it is a header ("Validation errors:"):
   * the banner lists them as bullets. Absent for a single message.
   */
  items?: string[];
  hint?: string;
}

export interface WorkflowRunState {
  jobId: string | null;
  running: boolean;
  pipelineError?: PipelineError | null;
}

// ---------- Canvas node data ----------

export type CanvasNodeData = {
  label: string;
  method: string;
  module: string;
  /**
   * The content identity of the older version this node is pinned to, or
   * `"draft"` for a node that runs its method's draft. Absent when the node
   * follows the newest version.
   */
  version?: string;
  /** The method's versions, for the node's version number and dropdown. */
  versions?: MethodVersionDef[];
  /** The method's draft, when it has one: the dropdown's ● DRAFT entry. */
  draft?: DraftDef;
  /** A draft node's method folder, as its document names it. */
  folder?: string;
  /**
   * What the last snapshot check said of a draft node's folder, shown on the
   * node with `draftMessage` saying why; the server decides whether it runs.
   */
  draftState?: 'current' | 'invalid' | 'missing';
  draftMessage?: string;
  /**
   * The server's refusals naming this node from the last Validate or Run
   * (`nodeRefusals.ts`), shown on the node and in the Inspector until the
   * next one.
   */
  refusals?: string[];
  inputs: SlotDef[];
  outputs: SlotDef[];
  params: ParamDef[];
  paramValues: Record<string, unknown>;
  runStatus: RunStatus;
  /** Per-sample fan-out tally when the status aggregates a mixed outcome. */
  runTally?: RunTally;
  expanded: boolean;
  datasource?: string;
  /**
   * Per-param sweep variants authored on this node.
   * Shape: `{ paramName: { variantName: value } }`.
   * Example: `{ min_quality: { v1: 0.3, v2: 0.7 } }`.
   */
  variants?: Record<string, Record<string, unknown>>;
  /**
   * Per-sample overrides authored on this node.
   * Shape: `{ sampleName: { paramName: value } }`.
   * Never serialized to JSON — compiled into `param_sets` +
   * `explicit_combos` by `compilePipelineToJSON`.
   */
  sampleOverrides?: Record<string, Record<string, unknown>>;
  /**
   * Per-sample sweep variants. Shape:
   * `{ sampleName: { paramName: { variantName: value } } }`.
   * Authored alongside `sampleOverrides`: sample X's effective baseline
   * is `{...paramValues, ...sampleOverrides[X]}` and the per-sample
   * sweep produces additional `X__o{n}` variants by cartesian-expanding
   * `sampleVariants[X]`. Never serialized directly — compiled into
   * `param_sets` + `explicit_combos` like sweeps and overrides.
   */
  sampleVariants?: Record<string, Record<string, Record<string, unknown>>>;
  // System node fields
  nodeType?: NodeType;
  // input_selector
  selectedSamples?: string[];
  /**
   * Fan-out: each selected sample spawns a parallel pipeline run.
   * Fan-in:  all selected samples are bundled and feed into the
   * downstream node as a single multi-input (e.g. a merge_csv that
   * accepts N files). Defaults to 'out'.
   */
  fanMode?: 'out' | 'in';
  /**
   * Snakemake --keep-going behaviour for fan-out pipelines: when true, a
   * failed sample doesn't cancel the others. Only meaningful when
   * fanMode === 'out'; a fan-in run is a single bundled job so there's
   * nothing to keep going around. Defaults to true because fan-out is
   * the canvas's only fan-out mechanism and continuing past isolated
   * failures is almost always what you want for per-sample work.
   */
  keepGoing?: boolean;
  /** UI-only: collapse the per-sample list inside the node body. */
  inputCollapsed?: boolean;
  // run_reference
  selectedRunId?: string;
};

// ---------- System node API types ----------

export interface SampleInfo {
  name: string;
  file_type: string;
  registered_path: string;
  file_size: number | null;
  registered_at: string | null;
  /** The sample's description, from registration or its manifest. */
  description: string | null;
  /** How many files a directory sample holds; null for a file sample. */
  file_count: number | null;
}

export interface CompletedRun {
  id: string;
  method: string;
  module: string;
  sample: string;
  params: Record<string, unknown>;
  output_slots: string[];
  pipeline_id: string;
  finished_at: string;
}
