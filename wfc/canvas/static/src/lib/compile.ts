/**
 * Pure compile/parse functions for canvas authoring state <-> PipelineJSON.
 *
 * This module deliberately has NO svelte-store imports so it can be imported
 * by Node harnesses (via `--experimental-strip-types`) for regression tests.
 * Runtime logic lives here; `pipeline.ts` re-exports and adds the
 * svelte-store glue (`exportPipeline`, `loadPipeline`).
 */
import type {
  PipelineJSON,
  PipelineNode,
  PipelineLink,
  CanvasNodeData,
  NodeType,
  ParamSets,
  ExplicitCombo,
  PipelineVariables,
  VarRef,
} from './types.js';
import type { Node, Edge } from '@xyflow/svelte';

/**
 * Local copy of `isVarRef` from `./types.ts` — inlined here because this
 * module is imported by Node harnesses via `--experimental-strip-types`,
 * which erases type-only imports but cannot resolve runtime imports of
 * `./types.js` (the source file is `types.ts` with no compiled artifact).
 * Keep in sync with the canonical predicate in `./types.ts`.
 */
function isVarRef(v: unknown): v is VarRef {
  return (
    typeof v === 'object'
    && v !== null
    && '$var' in v
    && typeof (v as { $var: unknown }).$var === 'string'
    && Object.keys(v).length === 1
  );
}

/**
 * Per-row binding marker keyed by `${nodeId}::${paramName}` (and for
 * variant rows: `${nodeId}::${paramName}::${variantName}`). Value is
 * the variable name the row is bound to. Threaded through compile so
 * `{$var: name}` refs land in the right slots in `node.params` and
 * `param_sets[node][variant]`.
 */
export type BoundVariablesMap = Record<string, string>;

/**
 * Authoring-state snapshot consumed by `compilePipelineToJSON`.
 * Kept as a flat typed record so the compile function remains pure
 * and unit-testable without coupling to the svelte stores.
 */
export interface AuthoringState {
  name: string;
  nodes: Node<CanvasNodeData>[];
  edges: Edge[];
  /** Flat list of samples selected across all input_selector nodes. */
  samples: string[];
  /**
   * Pipeline variables (Track 2, ADR-017). When non-empty,
   * `compilePipelineToJSON` emits a top-level `variables` block.
   */
  pipelineVariables?: PipelineVariables;
  /**
   * Per-row binding markers — collected by `exportPipeline` from each
   * row's `paramEditorActor.context.boundVariable`. Compile walks
   * `node.params` / `param_sets[node][variant]` and replaces the literal
   * with `{$var: name}` for any (nodeId, paramName[, variantName]) key
   * present here.
   *
   * Key shapes:
   *   - base row: `${nodeId}::${paramName}`
   *   - variant row: `${nodeId}::${paramName}::${variantName}`
   */
  boundVariables?: BoundVariablesMap;
}

/**
 * Pure, deterministic compile of canvas authoring state to the
 * wire-format `PipelineJSON` understood by the engine.
 *
 * The engine (`wfc/snakemake_gen.py`) already supports per-sample
 * variation via `param_sets` + `explicit_combos`.  A per-sample
 * override is just a named variant restricted to that sample via
 * `explicit_combos`.  Naming convention for compiled override
 * variants: `{sample}__o{n}` (1-indexed per node-sample pair).
 *
 * Rules:
 * - If a node has variants, each (param, variant) entry compiles
 *   into a top-level variant under `param_sets[node_id]`.  When
 *   multiple params have variants on the same node, their cartesian
 *   product is expanded into node-level variants `v1, v2, …`.
 * - If a node has sample overrides, each (sample, overriddenParams)
 *   row compiles into one variant named `{sample}__o{n}`.
 * - When ANY node in the pipeline has at least one override, the
 *   engine is forced into selective mode (`explicit_combos` becomes
 *   the exclusive run list), so we MUST emit the full
 *   (sample × sweep_variant) cartesian PLUS the override rows.
 * - When no override exists anywhere, `explicit_combos` is omitted
 *   and the engine's cartesian-mode padding handles the matrix.
 * - When a node has no variants and no overrides, it is omitted
 *   from `param_sets` entirely.
 */
export function compilePipelineToJSON(state: AuthoringState): PipelineJSON {
  // Drop null/undefined entries from a node's base params. Empty-optional
  // values (committed blank in the inspector) arrive here as null and
  // should be omitted so the method's params.get("x") returns None and
  // the method takes its unset path — same contract as "key was never
  // authored." param_sets variant dicts are NOT filtered: a null there
  // means "this variant explicitly unsets this param," which is distinct
  // from "inherit from base."
  function stripUnset(params: Record<string, unknown>): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) {
      if (v === null || v === undefined) continue;
      out[k] = v;
    }
    return out;
  }

  // Track 2 (ADR-017): replace literal values in `node.params` with
  // `{$var: name}` refs for any (nodeId, paramName) key present in
  // `state.boundVariables`. The parsePipelineJSON inverse re-emits
  // these as per-row `boundVariable` markers so spawned actors land
  // in the `bound` state.
  const boundMap: BoundVariablesMap = state.boundVariables ?? {};
  function applyBaseBindings(
    nodeId: string,
    params: Record<string, unknown>,
  ): Record<string, unknown> {
    const out = { ...params };
    for (const paramName of Object.keys(out)) {
      const key = `${nodeId}::${paramName}`;
      if (boundMap[key] !== undefined) {
        out[paramName] = { $var: boundMap[key] } as VarRef;
      }
    }
    // Allow binding a param that has no literal entry in paramValues
    // (e.g. a row whose value is the default — we still want the bind
    // to round-trip).
    for (const key of Object.keys(boundMap)) {
      const parts = key.split('::');
      if (parts.length !== 2) continue;
      const [nid, pname] = parts;
      if (nid !== nodeId) continue;
      if (out[pname] === undefined) {
        out[pname] = { $var: boundMap[key] } as VarRef;
      }
    }
    return out;
  }

  const pipelineNodes: PipelineNode[] = state.nodes.map(n => {
    const base: PipelineNode = {
      id: n.id,
      type: (n.data.nodeType as NodeType) || 'method',
      method: n.data.method,
      module: n.data.module,
      params: applyBaseBindings(n.id, stripUnset(n.data.paramValues ?? {})),
      position: { x: n.position.x, y: n.position.y },
    };
    if (n.data.nodeType === 'input_selector') {
      base.samples = n.data.selectedSamples ?? [];
      base.source = 'registered';
      const fanMode = n.data.fanMode ?? 'out';
      (base as PipelineNode & { fan_mode?: string }).fan_mode = fanMode;
      // Persist keep_going on the selector so round-trip import/export is
      // lossless. Default: true for fan-out, false for fan-in (the flag is
      // a no-op there since there's only one bundled job).
      const keepGoing = n.data.keepGoing ?? (fanMode === 'out');
      (base as PipelineNode & { keep_going?: boolean }).keep_going = keepGoing;
    } else if (n.data.nodeType === 'run_reference') {
      base.run_id = n.data.selectedRunId;
      // No singular output_slot: each outgoing edge names its source slot
      // via sourceHandle, and the engine resolves per-link from Run's
      // output_paths dict. `output_slot` is retained as an optional
      // back-compat field on old pipelines only (see parsePipelineJSON).
    }
    return base;
  });

  const pipelineLinks: PipelineLink[] = state.edges.map(e => ({
    source: e.source,
    target: e.target,
    sourceHandle: e.sourceHandle ?? undefined,
    targetHandle: e.targetHandle ?? undefined,
  }));

  // ── Compile param_sets + explicit_combos ──────────────────────────────────
  // Two-pass:
  //   Pass 1 — walk every method node; for each, produce (a) its sweep-variant
  //            names and (b) its override-variant rows; record them on
  //            per-node maps and accumulate the global sweep-variant set and
  //            the global override row list.
  //   Pass 2 — decide whether to emit explicit_combos at all.  If ANY node in
  //            the pipeline has at least one override, the engine is forced
  //            into selective mode (RUNS is the exclusive run list), so we
  //            MUST emit the full (sample × sweep_variant) cartesian PLUS the
  //            override rows.  If NO override exists anywhere, explicit_combos
  //            is omitted and the engine's cartesian-mode padding handles the
  //            matrix.
  //
  // Historical bug (fixed in iteration 2 of pev-2026-04-17-parameter-sweeps-
  // chip-ux): previously only override rows were pushed; sweep cells were
  // silently dropped whenever any override existed.  The regression test
  // lives at tests/test_canvas_compile_ts.py.
  const param_sets: ParamSets = {};
  type NodeVariantInfo = {
    sweepVariants: string[];
    overrideRows: Array<{ sample: string; variant: string }>;
  };
  const nodeInfo: Record<string, NodeVariantInfo> = {};

  /**
   * Cartesian-expand a base param dict over per-param variant picks.
   * Used for per-sample sweeps. Given `base = {P: 1, Q: 10}` and
   * `variants = {Q: {v1: 20, v2: 30}}`, returns
   *   [{P:1, Q:20}, {P:1, Q:30}]
   *
   * When `variants` is empty and `base` was built from a non-empty
   * override dict, callers still need one combo (the override itself);
   * that case is handled externally — this helper returns the single
   * `[base]` combo only when variants are empty, matching that contract.
   */
  function buildPerSampleCombos(
    base: Record<string, unknown>,
    variants: Record<string, Record<string, unknown>>,
  ): Array<Record<string, unknown>> {
    const paramNames = Object.keys(variants).filter(
      p => Object.keys(variants[p] ?? {}).length > 0,
    );
    if (paramNames.length === 0) return [base];
    let combos: Array<Record<string, unknown>> = [{ ...base }];
    for (const p of paramNames) {
      const next: Array<Record<string, unknown>> = [];
      for (const combo of combos) {
        for (const value of Object.values(variants[p])) {
          next.push({ ...combo, [p]: value });
        }
      }
      combos = next;
    }
    return combos;
  }

  /**
   * Return the name of any existing variant whose resolved params deep-equal
   * `target`, or null if none match.  Used to drop duplicate per-sample
   * override variants whose value coincides with a sweep variant.
   */
  function findEqualVariant(
    existing: Record<string, Record<string, unknown>>,
    target: Record<string, unknown>,
  ): string | null {
    const tKeys = Object.keys(target).sort();
    const tFingerprint = JSON.stringify(tKeys.map(k => [k, target[k]]));
    for (const [name, params] of Object.entries(existing)) {
      const eKeys = Object.keys(params).sort();
      if (JSON.stringify(eKeys.map(k => [k, params[k]])) === tFingerprint) {
        return name;
      }
    }
    return null;
  }

  for (const n of state.nodes) {
    if (n.data.nodeType && n.data.nodeType !== 'method') continue;
    const nodeVariants: Record<string, Record<string, unknown>> = {};
    const sweepVariants: string[] = [];
    const overrideRows: Array<{ sample: string; variant: string }> = [];

    // Per-param variants → cartesian product → node-level variants.
    const perParamVariants = n.data.variants ?? {};
    const paramNames = Object.keys(perParamVariants).filter(
      p => Object.keys(perParamVariants[p] ?? {}).length > 0,
    );
    if (paramNames.length > 0) {
      // Build cartesian combos of variant names; each combo = one node variant.
      const namesList = paramNames.map(p => Object.keys(perParamVariants[p]));
      const combos: Array<Record<string, string>> = [{}];
      paramNames.forEach((p, idx) => {
        const next: Array<Record<string, string>> = [];
        for (const combo of combos) {
          for (const vname of namesList[idx]) {
            next.push({ ...combo, [p]: vname });
          }
        }
        combos.length = 0;
        combos.push(...next);
      });
      // When exactly one param has variants, keep its variant names as-is
      // so round-trip is clean.  Otherwise auto-name v1/v2/…
      if (paramNames.length === 1) {
        const p = paramNames[0];
        for (const vname of Object.keys(perParamVariants[p])) {
          nodeVariants[vname] = {
            ...n.data.paramValues,
            [p]: perParamVariants[p][vname],
          };
          sweepVariants.push(vname);
        }
      } else {
        combos.forEach((combo, i) => {
          const vname = `v${i + 1}`;
          const merged: Record<string, unknown> = { ...n.data.paramValues };
          for (const p of paramNames) {
            merged[p] = perParamVariants[p][combo[p]];
          }
          nodeVariants[vname] = merged;
          sweepVariants.push(vname);
        });
      }
    }

    // Per-sample overrides + per-sample sweeps → one or more override
    // variants per sample (1-indexed counter, e.g. X__o1, X__o2, ...).
    //
    // For sample X:
    //   base_for_X = {...paramValues, ...sampleOverrides[X]}
    //   variants_for_X = sampleVariants[X]  (per-param, like `variants`)
    //   combos = cartesian_expand(base_for_X, variants_for_X)
    //     — empty variants → combos = [base_for_X] iff sampleOverrides[X]
    //       was non-empty, else []
    //     — non-empty variants → combos = cartesian of base × variant picks
    //
    // Dedup: every emitted combo is compared against the existing
    // nodeVariants (sweep entries + overrides emitted earlier for other
    // samples). A combo that matches an existing variant is dropped.
    const perSampleOverrides = n.data.sampleOverrides ?? {};
    const perSampleVariants = n.data.sampleVariants ?? {};
    const perSampleSamples = new Set<string>([
      ...Object.keys(perSampleOverrides),
      ...Object.keys(perSampleVariants),
    ]);
    let overrideCounter = 1;
    for (const sample of perSampleSamples) {
      const ovr = perSampleOverrides[sample] ?? {};
      const svar = perSampleVariants[sample] ?? {};
      const hasOvr = Object.keys(ovr).length > 0;
      const hasSvar = Object.values(svar).some(vd => Object.keys(vd ?? {}).length > 0);
      // No actual per-sample state for this sample — skip cleanly even if
      // the key exists with empty content (e.g. left over after clearing).
      if (!hasOvr && !hasSvar) continue;
      const baseForSample = { ...n.data.paramValues, ...ovr };
      const combos = buildPerSampleCombos(baseForSample, svar);
      for (const resolved of combos) {
        if (findEqualVariant(nodeVariants, resolved) !== null) continue;
        const overrideName = `${sample}__o${overrideCounter++}`;
        nodeVariants[overrideName] = resolved;
        overrideRows.push({ sample, variant: overrideName });
      }
    }

    if (Object.keys(nodeVariants).length > 0) {
      param_sets[n.id] = nodeVariants;
    }
    nodeInfo[n.id] = { sweepVariants, overrideRows };
  }

  // ── Build explicit_combos (pass 2) ────────────────────────────────────────
  // Any override in the pipeline forces selective mode; we then emit the
  // full matrix.  Otherwise we leave explicit_combos empty and let the engine
  // cartesian-mode padding fill the matrix implicitly.
  const explicit_combos: ExplicitCombo[] = [];
  const hasAnyOverride = Object.values(nodeInfo).some(
    info => info.overrideRows.length > 0,
  );
  if (hasAnyOverride) {
    // Global sweep-variant names = union across every node's sweep variants.
    // Override variant names are node-local (bound to one sample each) and
    // are NOT part of the cartesian — they are emitted separately below.
    const globalSweepVariants = new Set<string>();
    for (const info of Object.values(nodeInfo)) {
      for (const v of info.sweepVariants) globalSweepVariants.add(v);
    }
    // Deterministic iteration order for stable output across runs.
    const sweepVariantList = Array.from(globalSweepVariants).sort();
    if (sweepVariantList.length > 0) {
      // Cartesian: every sample × every global sweep variant.
      for (const sample of state.samples) {
        for (const variant of sweepVariantList) {
          explicit_combos.push({ sample, variant });
        }
      }
    } else {
      // No sweeps, but overrides force selective mode. Without this branch
      // every non-overridden sample would be silently dropped (the cartesian
      // loop above would iterate an empty variant list and emit nothing).
      // Each non-overridden sample runs once with the synthetic "default"
      // variant — the engine falls back to node.params when "default" isn't
      // listed in a node's param_sets, so no per-node padding is needed.
      const overriddenSamples = new Set<string>();
      for (const info of Object.values(nodeInfo)) {
        for (const row of info.overrideRows) {
          overriddenSamples.add(row.sample);
        }
      }
      for (const sample of state.samples) {
        if (!overriddenSamples.has(sample)) {
          explicit_combos.push({ sample, variant: 'default' });
        }
      }
    }
    // Override rows: each (sample, override_variant) as produced per node.
    for (const info of Object.values(nodeInfo)) {
      for (const row of info.overrideRows) {
        explicit_combos.push(row);
      }
    }
  }

  // Apply variant-level bindings to param_sets[node][variant][param].
  // Key shape: `${nodeId}::${paramName}::${variantName}`.
  for (const key of Object.keys(boundMap)) {
    const parts = key.split('::');
    if (parts.length !== 3) continue;
    const [nodeId, paramName, variantName] = parts;
    if (!param_sets[nodeId] || !param_sets[nodeId][variantName]) continue;
    param_sets[nodeId][variantName][paramName] = { $var: boundMap[key] } as VarRef;
  }

  const result: PipelineJSON = {
    name: state.name,
    nodes: pipelineNodes,
    links: pipelineLinks,
    samples: state.samples,
  };
  if (Object.keys(param_sets).length > 0) result.param_sets = param_sets;
  if (explicit_combos.length > 0) result.explicit_combos = explicit_combos;
  // Track 2 (ADR-017): emit top-level `variables` block when any variable
  // is declared. Pre-substitution form — server-side resolve_variables
  // strips this block before _enrich_pipeline.
  if (state.pipelineVariables && Object.keys(state.pipelineVariables).length > 0) {
    result.variables = state.pipelineVariables;
  }
  return result;
}

/**
 * Inverse of `compilePipelineToJSON` — best-effort.
 *
 * Heuristic: variants in `param_sets[node_id]` matching the regex
 * `^(.+)__o\d+$` whose group-1 matches a known sample AND that have
 * a corresponding `explicit_combos` row binding them to that sample
 * are re-interpreted as `sampleOverrides`.  All other variants are
 * surfaced as regular `variants` keyed by `paramValues` diff against
 * the node's base params.
 *
 * This is best-effort: pipelines authored by hand that happen to
 * follow the `{sample}__o{n}` naming convention will appear as
 * overrides; that is intentional per the architect's "tradeoffs
 * accepted" — a 1:1 lossless round-trip is not required.
 */
export function parsePipelineJSON(pipeline: PipelineJSON): {
  nodeVariants: Record<string, Record<string, Record<string, unknown>>>;
  nodeSampleOverrides: Record<string, Record<string, Record<string, unknown>>>;
  nodeSampleVariants: Record<string, Record<string, Record<string, Record<string, unknown>>>>;
  /**
   * Per-row binding markers (Track 2, ADR-017). Same key shape as
   * `AuthoringState.boundVariables`. Threaded into `paramEditorActor`
   * spawn input as `boundVariable` so rows land in `bound` immediately.
   */
  boundVariables: BoundVariablesMap;
  /** Top-level pipeline variables block, if present. */
  pipelineVariables: PipelineVariables;
} {
  const nodeVariants: Record<string, Record<string, Record<string, unknown>>> = {};
  const nodeSampleOverrides: Record<string, Record<string, Record<string, unknown>>> = {};
  const nodeSampleVariants: Record<string, Record<string, Record<string, Record<string, unknown>>>> = {};
  const boundVariables: BoundVariablesMap = {};
  const pipelineVariables: PipelineVariables = pipeline.variables ?? {};

  const knownSamples = new Set<string>(pipeline.samples ?? []);
  for (const n of pipeline.nodes) {
    if (n.type === 'input_selector' && Array.isArray(n.samples)) {
      for (const s of n.samples) knownSamples.add(s);
    }
  }

  const overrideKey = new Set<string>(); // "node_id::variant_name"
  for (const combo of pipeline.explicit_combos ?? []) {
    overrideKey.add(`${combo.sample}::${combo.variant}`);
  }

  // Track 2: walk node.params for $var refs → record as base bindings,
  // and strip them from the literal-params view consumed by downstream
  // logic. We mutate `pipeline.nodes[*].params` in a copy to avoid
  // touching the caller's input, but the parse contract is that the
  // returned authoring state knows about bindings separately from
  // literal `paramValues`.
  const baseParamsByNode: Record<string, Record<string, unknown>> = {};
  for (const n of pipeline.nodes) {
    const params = n.params ?? {};
    const stripped: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) {
      if (isVarRef(v)) {
        boundVariables[`${n.id}::${k}`] = v.$var;
        // Don't seed a literal — the row will land in `bound`. The
        // actor's currentValue will be undefined; resolved value comes
        // from `pipelineVariables[name].value` at render time.
      } else {
        stripped[k] = v;
      }
    }
    baseParamsByNode[n.id] = stripped;
  }

  for (const [nodeId, variants] of Object.entries(pipeline.param_sets ?? {})) {
    const base = baseParamsByNode[nodeId] ?? {};
    // Track 2: walk variant params for $var refs → record as variant
    // bindings keyed `${nodeId}::${paramName}::${variantName}`.
    for (const [vname, variantParams] of Object.entries(variants)) {
      for (const [pname, pval] of Object.entries(variantParams)) {
        if (isVarRef(pval)) {
          boundVariables[`${nodeId}::${pname}::${vname}`] = pval.$var;
        }
      }
    }

    // First pass: bucket override variants by sample so we can split the
    // first-per-sample into sampleOverrides and any remainder into
    // sampleVariants (per-sample sweeps).
    const overrideBySample: Record<string, Array<{ vname: string; diff: Record<string, unknown> }>> = {};

    for (const [vname, variantParams] of Object.entries(variants)) {
      const m = /^(.+)__o\d+$/.exec(vname);
      const isOverride = !!(m && knownSamples.has(m[1]) && overrideKey.has(`${m[1]}::${vname}`));
      if (isOverride) {
        const sample = m![1];
        const diff: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(variantParams)) {
          if (JSON.stringify(base[k]) !== JSON.stringify(v)) diff[k] = v;
        }
        if (!overrideBySample[sample]) overrideBySample[sample] = [];
        overrideBySample[sample].push({ vname, diff });
        continue;
      }

      {
        // Regular variant — surface the full-value map for now.  The
        // Inspector shows a chip per (param, variantName) where the value
        // differs from the base.
        const diff: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(variantParams)) {
          if (JSON.stringify(base[k]) !== JSON.stringify(v)) diff[k] = v;
        }
        // Single-param variant convention: lift the diff into per-param view.
        const diffKeys = Object.keys(diff);
        if (diffKeys.length === 1) {
          const p = diffKeys[0];
          if (!nodeVariants[nodeId]) nodeVariants[nodeId] = {};
          if (!nodeVariants[nodeId][p]) nodeVariants[nodeId][p] = {};
          nodeVariants[nodeId][p][vname] = diff[p];
        } else if (diffKeys.length > 0) {
          // Multi-param variant — stash under a synthetic `__combo` param
          // bucket so the UI can show it read-only.  Chip editor will
          // refuse to edit these (best-effort round-trip).
          if (!nodeVariants[nodeId]) nodeVariants[nodeId] = {};
          if (!nodeVariants[nodeId].__combo) nodeVariants[nodeId].__combo = {};
          nodeVariants[nodeId].__combo[vname] = diff;
        }
      }
    }

    // Second pass: distribute each sample's override bucket.
    //
    //   1 X__o row  → sampleOverrides[sample] = diff (preserves the
    //                  single-value override authoring shape).
    //   2+ X__o rows → all diffs go into sampleVariants[sample][param] =
    //                  {v1: val, v2: val, ...}. sampleOverrides is left
    //                  empty so the recompiled run count matches the
    //                  imported count (N in → N combos out).
    //
    // Heuristic for 2+ case: the sweep param is whichever param varies
    // across the diffs. Single-param per-sample sweeps round-trip
    // cleanly; multi-param collapses to a best-effort slice on the
    // first varying param (documented lossy path).
    for (const [sample, bucket] of Object.entries(overrideBySample)) {
      if (bucket.length === 0) continue;

      if (bucket.length === 1) {
        if (!nodeSampleOverrides[nodeId]) nodeSampleOverrides[nodeId] = {};
        nodeSampleOverrides[nodeId][sample] = bucket[0].diff;
        continue;
      }

      // 2+ rows: collect all distinct values for each varying param.
      // Identify the (single) param that varies across the bucket;
      // fall back to whichever param appears in any diff if none vary.
      const paramValues: Record<string, unknown[]> = {};
      for (const entry of bucket) {
        for (const [k, v] of Object.entries(entry.diff)) {
          if (!paramValues[k]) paramValues[k] = [];
          paramValues[k].push(v);
        }
      }
      // Pick the param with the most distinct values (usually only one
      // param varies). If a param has the same value across all entries,
      // it's part of the shared baseline, not the sweep dimension.
      let sweepParam: string | null = null;
      let sweepDistinct = 0;
      for (const [p, values] of Object.entries(paramValues)) {
        const distinct = new Set(values.map(v => JSON.stringify(v))).size;
        if (distinct > sweepDistinct) {
          sweepParam = p;
          sweepDistinct = distinct;
        }
      }
      if (!sweepParam) continue;

      if (!nodeSampleVariants[nodeId]) nodeSampleVariants[nodeId] = {};
      if (!nodeSampleVariants[nodeId][sample]) nodeSampleVariants[nodeId][sample] = {};
      nodeSampleVariants[nodeId][sample][sweepParam] = {};
      let variantCounter = 1;
      for (const entry of bucket) {
        const v = entry.diff[sweepParam];
        if (v === undefined) continue;
        nodeSampleVariants[nodeId][sample][sweepParam][`v${variantCounter++}`] = v;
      }
    }
  }
  return {
    nodeVariants,
    nodeSampleOverrides,
    nodeSampleVariants,
    boundVariables,
    pipelineVariables,
  };
}
