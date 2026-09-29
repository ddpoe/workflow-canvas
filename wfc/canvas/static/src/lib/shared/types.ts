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
 * Raw contract type as declared in `method.yaml` (source of truth is
 * `wfc/contracts/declarations.py::parse_method_yaml`).  The canvas uses these for
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
   * When set, the inspector renders this string param as
   * a column-name dropdown populated from the upstream method's declared
   * `outputs.<slot>.columns`. The slot name is the value of this field.
   */
  column_of_input?: string;
  /**
   * When true, the param is a column the method
   * *creates* (not consumes) — render as plain free-text always, no
   * dropdown, even if column_of_input is also set.
   */
  new_column?: boolean;
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
   * Pipeline variables. Pre-substitution form only —
   * server-side `resolve_variables` strips this block and inlines each
   * `{$var: name}` ref before _enrich_pipeline. Persists to
   * `pipeline.editable.json` so History "Open in canvas" can rehydrate
   * the Pipeline Variables panel and per-row binding chips.
   */
  variables?: PipelineVariables;
}

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
  hint?: string;
}

export interface WorkflowRunState {
  jobId: string | null;
  running: boolean;
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
}

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
