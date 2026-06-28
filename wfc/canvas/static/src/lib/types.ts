/**
 * Pipeline and canvas type definitions.
 * Matches the backend PipelineDef / StepDef schema from wfc/snakemake_gen.py.
 */

// ---------- Module / Method registry ----------

export interface SlotDef {
  name: string;
  type: string;       // e.g. "csv", "parquet", "label"
  multi?: boolean;     // fan-in allowed
  description?: string;
}

export interface ParamConstraints {
  enum?: string[];
  min?: number;
  max?: number;
}

/**
 * Raw contract type as declared in `method.yaml` (source of truth is
 * `wfc/contracts.py::parse_method_yaml`).  The canvas uses these for
 * chip coercion/validation in ChipEditor.  `unknown` means the param
 * was not found in the method contract at all — values are treated
 * as pass-through strings.
 */
export type ContractType = 'str' | 'int' | 'float' | 'bool' | 'list' | 'dict' | 'unknown';

export interface ParamDef {
  name: string;
  type: string;        // Inspector-facing type: "string" | "number" | "boolean"
  /** Raw contract type from method.yaml. Used by ChipEditor for typed coercion. */
  contractType?: ContractType;
  /** Native default — string for primitives, native dict/array for list/dict params. */
  default?: unknown;
  description?: string;
  required?: boolean;
  constraints?: ParamConstraints;
  /**
   * Track 1 (ADR-017): when set, the inspector renders this string param as
   * a column-name dropdown populated from the upstream method's declared
   * `outputs.<slot>.columns`. The slot name is the value of this field.
   * The producer side (the upstream method's contract) reuses ADR-005's
   * `outputs.<slot>.columns` vocabulary unchanged.
   */
  column_of_input?: string;
  /**
   * Track 1 (ADR-017): when true, the param is a column the method
   * *creates* (not consumes) — render as plain free-text always, no
   * dropdown, even if column_of_input is also set.
   */
  new_column?: boolean;
}

// ---------- Pipeline Variables (Track 2, ADR-017) ----------

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
 * Server-side `wfc/canvas/wfc_provider.py::resolve_variables` substitutes
 * these whole-value with the named variable's value before _enrich_pipeline.
 */
export interface VarRef {
  $var: string;
}

export function isVarRef(v: unknown): v is VarRef {
  return (
    typeof v === 'object'
    && v !== null
    && '$var' in v
    && typeof (v as { $var: unknown }).$var === 'string'
    && Object.keys(v).length === 1
  );
}

export interface MethodDef {
  name: string;
  module: string;
  version?: string;
  description?: string;
  script_path?: string;
  inputs: SlotDef[];
  outputs: SlotDef[];
  params: ParamDef[];
  color?: string;       // module accent color
}

export interface ModuleDef {
  name: string;
  description?: string;
  color: string;
  methods: MethodDef[];
}

// ---------- Node type discriminator ----------

export type NodeType = 'method' | 'input_selector' | 'run_reference';

// ---------- Pipeline JSON (backend-compatible) ----------

export interface PipelineNode {
  id: string;
  type?: NodeType;
  method: string;
  module?: string;
  script?: string;
  params: Record<string, unknown>;
  position?: { x: number; y: number };
  // input_selector fields
  samples?: string[];
  source?: string;
  /** Per-input-selector dispatch mode: 'out' = parallel runs, 'in' = bundled. */
  fan_mode?: 'out' | 'in';
  // run_reference fields
  run_id?: string;
  output_slot?: string;
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
 * Used verbatim by `wfc/snakemake_gen.py` — the canvas compiles its
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

export interface PipelineJSON {
  name?: string;
  nodes: PipelineNode[];
  links: PipelineLink[];
  samples: string[];
  /** Per-node sweep variants.  Passes straight through to the engine. */
  param_sets?: ParamSets;
  /** Explicit (sample, variant) binding rows.  Passes straight through. */
  explicit_combos?: ExplicitCombo[];
  /**
   * Pipeline variables (Track 2, ADR-017). Pre-substitution form only —
   * server-side `resolve_variables` strips this block and inlines each
   * `{$var: name}` ref before _enrich_pipeline. Persists to
   * `pipeline.editable.json` so History "Open in canvas" can rehydrate
   * the Pipeline Variables panel and per-row binding chips.
   */
  variables?: PipelineVariables;
}

/**
 * Authoring-state sample overrides.
 *
 * Shape: `{ node_id: { sample_name: { param_name: value } } }`.
 *
 * This structure is **never serialized** to pipeline JSON.  It lives
 * only in the canvas and is compiled on export to
 * `param_sets` + `explicit_combos`.  Naming convention for compiled
 * override variants: `{sample}__o{n}` (1-indexed per node-sample).
 */
export type SampleOverrides = Record<string, Record<string, Record<string, unknown>>>;

/**
 * Per-node, per-param sweep variants authored in the Inspector.
 *
 * Shape: `{ node_id: { param_name: { variant_name: value } } }`.
 *
 * Compiled into `param_sets` by cartesian-expanding param-level variants
 * into node-level variants at export time.  For simple cases (one param
 * with chips), this is a direct copy.
 */
export type NodeVariants = Record<string, Record<string, Record<string, unknown>>>;

/**
 * Method-contract param schema as received from `/api/modules`.
 * See `wfc/contracts.py::parse_method_yaml`.
 */
export interface MethodParamSchema {
  type?: ContractType | string;
  default?: unknown;
  required?: boolean;
  description?: string;
}

// ---------- Run state ----------

export type RunStatus = 'idle' | 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'mixed';

export interface RunTally {
  running: number;
  completed: number;
  failed: number;
  [key: string]: number;  // allow unknown/other buckets the backend may add
}

export interface NodeRunState {
  status: RunStatus;
  error?: string;
  /**
   * Per-sample fan-out tally. Populated for nodes whose samples ran as
   * independent jobs — lets the canvas show "3/4" on a mixed node.
   * Absent for system nodes and for pre-run pending states.
   */
  tally?: RunTally;
  /**
   * Run row IDs produced by this node in the current pipeline execution,
   * ordered newest-first by started_at. runIds[0] is "the most recent
   * attempt" — what the Builder Output tab streams. Empty/undefined for
   * pending nodes, cache-hit-only nodes, and system nodes.
   */
  runIds?: string[];
}

/**
 * Pipeline-level error surfaced to the canvas: raised by pre_run before
 * any step runs (dirty repo, malformed env spec, missing method, env-lock
 * lookup failures, etc.) so there's no per-node Run row to attach it to.
 * ``kind`` lets the UI pick an icon / inline affordance; ``hint`` is an
 * optional second sentence separated from ``message``.
 */
export interface PipelineError {
  message: string;
  kind?:
    | 'dirty_repo'
    | 'env_spec'
    | 'env_lock_missing'
    | 'env_name_missing'
    | 'not_found'
    | 'not_runnable_docker'
    | 'not_runnable_git'
    | 'unknown'
    | string;
  hint?: string;
}

export interface WorkflowRunState {
  jobId: string | null;
  running: boolean;
  nodeStates: Record<string, NodeRunState>;
  currentStep?: string;
  totalSteps?: number;
  completedSteps?: number;
  pipelineError?: PipelineError | null;
}

// ---------- Canvas node data ----------

export interface CanvasNodeData {
  label: string;
  method: string;
  module: string;
  version?: string;
  color: string;
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
   * Optional prefix/suffix applied to auto-generated NIDs (v1, v2, ...).
   * Stored on the node, applied at display/compile time — not persisted per
   * run. Custom per-run names (set via inline rename in the runs preview)
   * bypass prefix/suffix entirely.
   */
  nidPrefix?: string;
  nidSuffix?: string;
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
  /**
   * Legacy: pre-multi-output single-slot selection. Kept so old pipeline
   * JSON (authored before each run output became its own handle) still
   * round-trips. New pipelines leave this undefined — outputs are rendered
   * per slot in `outputs` and each outgoing edge names its own source slot.
   */
  selectedOutputSlot?: string;
}

// ---------- System node API types ----------

export interface SampleInfo {
  name: string;
  file_type: string;
  registered_path: string;
  file_size: number | null;
  registered_at: string | null;
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
