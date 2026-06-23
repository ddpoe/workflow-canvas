/**
 * Generate Mermaid `stateDiagram-v2` blocks for each actor machine.
 *
 * Walks the static machine config directly (rather than going through
 * `@xstate/graph::toDirectedGraph`, which in v3 collapses transition
 * targets and emits `[object Object]` for parallel/branching edges).
 * For each state, we read its `on` map and emit one
 * `source --> target: event` line per transition; nested states are
 * qualified with their parent (e.g. `cancelled.becauseUpstream`).
 *
 * Writes one `.mmd` file per machine into
 * `wfc/canvas/static/scripts/mermaid/`. The Auditor (not the Builder)
 * embeds the rendered output into `docs/features/canvas/lifecycle/`.
 *
 * NOTE: only static structure is captured here. The runtime spawn tree
 * (pipelineRunActor → nodeRunActor → streamingActor) does NOT appear in
 * the diagrams; that's the auditor doc's job to clarify in prose.
 *
 * Run with: `npm run gen-mermaid`
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeNodeRunMachine } from '../src/lib/machines/nodeRun.machine';
import { makeStreamingMachine } from '../src/lib/machines/streaming.machine';
import { makePipelineRunMachine } from '../src/lib/machines/pipelineRun.machine';
import { makeParamEditorMachine } from '../src/lib/machines/paramEditor.machine';
import { makeVariantMachine } from '../src/lib/machines/variant.machine';
import { makeParamEditorAggregatorMachine } from '../src/lib/machines/paramEditorAggregator.machine';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(__dirname, 'mermaid');

// xstate v5 internal config shape we walk. We only read the surface we
// need; this avoids depending on @xstate/graph internals.
interface RawTransition {
  target?: string | string[];
  // Named guard reference (parameterized via `setup({guards: {...}})`).
  // Anonymous lambdas appear as functions and are intentionally skipped
  // by `guardName` below — they have no name to show in the diagram.
  guard?: unknown;
  // The DSL-emitted transition can have `description`, `actions`, etc;
  // those we don't read.
}

/**
 * Pull a string name out of a transition's `guard` field so the
 * generator can annotate the edge label. Returns null for inline
 * lambdas (no current users in the codebase, but defensive — without
 * this, xstate's normalization changes could land function values
 * into the config and break the diagram silently).
 */
function guardName(g: unknown): string | null {
  if (typeof g === 'string') return g;
  if (g && typeof g === 'object' && 'type' in g) {
    const t = (g as { type: unknown }).type;
    if (typeof t === 'string') return t;
  }
  return null;
}
interface InvokeConfig {
  src?: string;
  id?: string;
  onDone?: RawTransition | RawTransition[];
  onError?: RawTransition | RawTransition[];
}

interface StateConfig {
  initial?: string;
  type?: 'final' | 'parallel' | 'compound' | 'atomic' | string;
  states?: Record<string, StateConfig>;
  on?: Record<string, RawTransition | RawTransition[]>;
  always?: RawTransition | RawTransition[];
  invoke?: InvokeConfig | InvokeConfig[];
  // Top-level machine root has its own `on` for events handled at any state.
}

interface MachineLike {
  config: StateConfig & { id?: string };
}

/**
 * Resolve a target reference (e.g. `'.preflight'`, `'idle'`,
 * `'cancelled.becauseUpstream'`) to the qualified state path used in
 * the Mermaid output. xstate's relative `.foo` syntax targets a sibling
 * of the machine root; treat it as a top-level state.
 */
function qualifyTarget(target: string, parentPath: string): string {
  if (target.startsWith('.')) {
    // `.foo` → root sibling. Drop the leading dot.
    return target.slice(1);
  }
  if (target.includes('.')) return target;
  // Bare `foo` from inside a nested state means a sibling. Otherwise it's
  // a top-level state.
  return target;
}

function collectTransitions(
  state: StateConfig,
  path: string,
  out: string[],
): void {
  // Walk `on` map.
  if (state.on) {
    for (const [eventName, raw] of Object.entries(state.on)) {
      const transitions = Array.isArray(raw) ? raw : [raw];
      for (const t of transitions) {
        const targets = t.target == null ? [] : Array.isArray(t.target) ? t.target : [t.target];
        const gn = guardName(t.guard);
        const label = gn ? `${eventName} [${gn}]` : eventName;
        for (const tgt of targets) {
          const targetPath = qualifyTarget(tgt, path);
          out.push(`  ${path} --> ${targetPath}: ${label}`);
        }
      }
    }
  }
  // Walk `always` (eventless transitions, drawn as a special edge).
  if (state.always) {
    const transitions = Array.isArray(state.always) ? state.always : [state.always];
    for (const t of transitions) {
      const targets = t.target == null ? [] : Array.isArray(t.target) ? t.target : [t.target];
      for (const tgt of targets) {
        const targetPath = qualifyTarget(tgt, path);
        out.push(`  ${path} --> ${targetPath}: (always)`);
      }
    }
  }
  // Walk `invoke.onDone` and `invoke.onError`. xstate accepts a single
  // invoke object or an array; handle both.
  if (state.invoke) {
    const invokes = Array.isArray(state.invoke) ? state.invoke : [state.invoke];
    for (const inv of invokes) {
      for (const [hook, label] of [
        [inv.onDone, 'onDone'],
        [inv.onError, 'onError'],
      ] as const) {
        if (!hook) continue;
        const transitions = Array.isArray(hook) ? hook : [hook];
        for (const t of transitions) {
          const targets =
            t.target == null ? [] : Array.isArray(t.target) ? t.target : [t.target];
          for (const tgt of targets) {
            const targetPath = qualifyTarget(tgt, path);
            out.push(`  ${path} --> ${targetPath}: ${label}`);
          }
        }
      }
    }
  }
  // Recurse into children.
  if (state.states) {
    for (const [childKey, childState] of Object.entries(state.states)) {
      const childPath = `${path}.${childKey}`;
      collectTransitions(childState, childPath, out);
    }
  }
}

function collectFinalStates(
  state: StateConfig,
  path: string,
  out: string[],
): void {
  // Mermaid stateDiagram-v2 marks terminal states with `state --> [*]`.
  // The earlier generator used `<<choice>>` which is a *decision*
  // pseudostate (diamond), semantically wrong for terminals.
  if (state.type === 'final') {
    out.push(`  ${path} --> [*]`);
  }
  if (state.states) {
    for (const [childKey, childState] of Object.entries(state.states)) {
      const childPath = path ? `${path}.${childKey}` : childKey;
      collectFinalStates(childState, childPath, out);
    }
  }
}

export function buildMermaid(
  machineName: string,
  machine: MachineLike,
): string {
  const lines: string[] = [
    `---`,
    `title: ${machineName}`,
    `---`,
    `stateDiagram-v2`,
  ];
  const root = machine.config;
  if (root.initial) {
    lines.push(`  [*] --> ${root.initial}`);
  }
  // Top-level transitions on the machine root itself (e.g. RESET on
  // pipelineRun.machine.ts handled at any state).
  const rootTransitions: string[] = [];
  if (root.on) {
    for (const [eventName, raw] of Object.entries(root.on)) {
      const transitions = Array.isArray(raw) ? raw : [raw];
      for (const t of transitions) {
        const targets = t.target == null ? [] : Array.isArray(t.target) ? t.target : [t.target];
        for (const tgt of targets) {
          const targetPath = qualifyTarget(tgt, '');
          rootTransitions.push(`  [*] --> ${targetPath}: ${eventName}`);
        }
      }
    }
  }
  lines.push(...rootTransitions);
  // Per-state transitions.
  if (root.states) {
    for (const [stateKey, stateConfig] of Object.entries(root.states)) {
      collectTransitions(stateConfig, stateKey, lines);
    }
  }
  // Final-state markers.
  const finals: string[] = [];
  if (root.states) {
    for (const [stateKey, stateConfig] of Object.entries(root.states)) {
      collectFinalStates(stateConfig, stateKey, finals);
    }
  }
  lines.push(...finals);
  return lines.join('\n') + '\n';
}

export function generateAll(outDir: string = OUT_DIR): {
  name: string;
  path: string;
  body: string;
}[] {
  mkdirSync(outDir, { recursive: true });
  const targets: Array<[string, MachineLike]> = [
    ['nodeRun', makeNodeRunMachine() as unknown as MachineLike],
    ['streaming', makeStreamingMachine() as unknown as MachineLike],
    ['pipelineRun', makePipelineRunMachine() as unknown as MachineLike],
    ['paramEditor', makeParamEditorMachine() as unknown as MachineLike],
    ['variant', makeVariantMachine() as unknown as MachineLike],
    ['paramEditorAggregator', makeParamEditorAggregatorMachine() as unknown as MachineLike],
  ];
  const results: { name: string; path: string; body: string }[] = [];
  for (const [name, m] of targets) {
    const out = resolve(outDir, `${name}.mmd`);
    const body = buildMermaid(name, m);
    if (!body || body.length === 0) {
      throw new Error(`empty mermaid output for ${name}`);
    }
    writeFileSync(out, body, 'utf-8');
    results.push({ name, path: out, body });
  }
  return results;
}

function main() {
  const results = generateAll();
  for (const r of results) {
    // eslint-disable-next-line no-console
    console.log(`wrote ${r.path} (${r.body.length} bytes)`);
  }
}

// Only run when invoked as a script. The test imports `buildMermaid` /
// `generateAll` directly without triggering file writes.
const invokedAsScript =
  typeof process !== 'undefined' &&
  process.argv[1] &&
  fileURLToPath(import.meta.url) === resolve(process.argv[1]);
if (invokedAsScript) main();
