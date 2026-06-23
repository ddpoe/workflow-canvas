<script lang="ts">
  /**
   * Unified per-param value list for the Inspector.
   *
   * (ADR-016 Phase 2 expand + D-9 fix) Base rows drive a `paramEditorActor`;
   * variant rows drive a `variantActor` with the full 7-state lifecycle
   * (`noVariants → addingVariant → editingValue → committing → committed
   * → mergingDuplicate → confirmingDelete → deleted`). Both shapes are
   * registered with the singleton `paramEditorAggregator` (its
   * `ChildActor` union accepts either), so Lock All and the Run-button
   * preflight fan COMMIT_ALL out across both kinds. Replaces the legacy
   * `editing[row.id]` / `localValue[row.id]` / `errorMsg[row.id]` maps
   * plus `commitAllSignal` / `markDirty`.
   *
   * Variant-specific affordances now wired (D-9):
   *   - `mergingDuplicate`: a "merged with sibling" inline notice plus
   *     ACK_MERGE button confirms the merge (→ committed). The user
   *     can also click ✎ to re-open the row for editing.
   *   - `confirmingDelete`: clicking × on a variant row sends DELETE to
   *     its variantActor (modal-shaped state per Edge Case #8). The row
   *     swaps its widget for a Confirm/Cancel pair that sends
   *     CONFIRM_DELETE / CANCEL_DELETE. NO parallel `$state` boolean
   *     for the prompt — the actor's state IS the prompt.
   *   - SIBLINGS_CHANGED broadcast: every variant commit re-broadcasts
   *     the updated sibling list to all OTHER variant actors (Edge
   *     Case #3) so the dedup guard sees the latest values.
   */
  import type { ContractType, ParamDef } from './types.js';
  import { onDestroy, untrack } from 'svelte';
  import { createActor } from 'xstate';
  import {
    makeParamEditorMachine,
    type ParamEditorActor,
    type ParamEditorType,
  } from './machines/paramEditor.machine.js';
  import {
    makeVariantMachine,
    type VariantActor,
  } from './machines/variant.machine.js';
  import {
    inspect as inspectCallback,
    registerEditorChild,
    unregisterEditorChild,
  } from './machines/root.js';
  import { pendingBoundVariables } from './pipeline.js';
  import { pipelineVariables } from './stores.js';
  import { get } from 'svelte/store';

  let {
    nodeId,
    param,
    baseValue,
    variants = {},
    onBaseChange,
    onVariantsChange,
    singleValue = false,
    dirtyKeySuffix = '',
    columnOptions = null,
  }: {
    nodeId: string;
    param: ParamDef;
    baseValue: unknown;
    variants?: Record<string, unknown>;
    onBaseChange: (value: unknown) => void;
    onVariantsChange?: (next: Record<string, unknown>) => void;
    /** When true: no variants, no + variant button, no row delete. Used for per-sample overrides. */
    singleValue?: boolean;
    /** Disambiguate dirty-state keys when the same (node, param) is rendered twice (e.g. per-sample tab). */
    dirtyKeySuffix?: string;
    /**
     * Track 1 (ADR-017) — `column_of_input` combobox options. When the
     * inspector resolves the upstream slot's declared columns, it passes
     * the response here. Shape: `{strict, from_params, patterns, all}`.
     * When `null`, the row renders as a plain string input (existing
     * behavior). When non-null AND non-empty, the editing widget for a
     * string row renders as a `<datalist>`-backed combobox. When
     * non-null AND empty, a hint chip is shown next to the row.
     */
    columnOptions?: {
      strict: string[];
      from_params: string[];
      patterns: string[];
      all: string[];
    } | null;
  } = $props();

  let ct = $derived<ContractType>((param.contractType ?? 'unknown') as ContractType);
  let isBoolean = $derived(param.type === 'boolean' || ct === 'bool');
  let isEnum = $derived(!!(param.constraints?.enum && param.constraints.enum.length > 0));
  let isNumber = $derived(param.type === 'number' || ct === 'int' || ct === 'float');
  let isJson = $derived(ct === 'list' || ct === 'dict' || param.type === 'list' || param.type === 'dict');
  let isString = $derived(!isBoolean && !isEnum && !isNumber && !isJson);
  let listDictDisabled = $derived(ct === 'list' || ct === 'dict');

  type Row = { id: string; variantName: string | null; isBase: boolean };
  let variantOrder = $derived(singleValue ? [] : Object.keys(variants).sort(lexVariantCmp));
  let rows = $derived<Row[]>([
    { id: 'base', variantName: null, isBase: true },
    ...variantOrder.map(vn => ({ id: `v:${vn}`, variantName: vn, isBase: false })),
  ]);

  let expanded = $state<Record<string, boolean>>({});

  function paramTypeForActor(): ParamEditorType {
    if (isBoolean) return 'bool';
    if (isEnum) return 'enum';
    if (param.type === 'number' || ct === 'int') return 'int';
    if (param.type === 'number' || ct === 'float') return 'float';
    if (ct === 'list' || param.type === 'list') return 'list';
    if (ct === 'dict' || param.type === 'dict') return 'dict';
    return 'string';
  }

  function rowAggregatorId(row: Row): string {
    return `${nodeId}::${param.name}${dirtyKeySuffix}::${row.id}`;
  }

  function committedValueFor(row: Row): unknown {
    if (row.isBase) return baseValue;
    return variants[row.variantName!];
  }

  /**
   * Sibling values for a variant row = every OTHER variant's committed
   * value. Used at spawn time and in SIBLINGS_CHANGED broadcasts so the
   * dedup guard always sees the latest dict (Edge Case #3).
   */
  function siblingValuesFor(variantName: string): unknown[] {
    const out: unknown[] = [];
    for (const [vn, v] of Object.entries(variants)) {
      if (vn === variantName) continue;
      out.push(v);
    }
    return out;
  }

  // Per-row spawned actors. Base rows hold ParamEditorActor; variant rows
  // hold VariantActor. The aggregator's ChildActor union accepts both. The
  // identity-change effect tears down all and re-spawns on
  // (nodeId, paramName, dirtyKeySuffix) change so the previous param's
  // actors don't bleed into the new param when SvelteFlow reuses the
  // component instance across `{#each data.params}` iterations.
  //
  // `actors` is intentionally a NON-reactive plain ref (not `$state`).
  // The spawn $effect both reads and writes the actors map, which under
  // Svelte 5 strict mode (the @testing-library/svelte mount path) trips
  // `effect_update_depth_exceeded`. Re-renders are driven explicitly via
  // `actorTick` (bumped by every actor transition AND by spawn/teardown
  // below). All template / `snapForRow` reads of `actors[row.id]` are
  // gated on `void actorTick` so they re-evaluate when the map mutates.
  type RowActor = ParamEditorActor | VariantActor;
  const actors: Record<string, RowActor> = {};
  let actorTick = $state(0);
  let prevIdentity = '';
  let prevRowIds: string[] = [];

  // Tracks which variant rows are freshly created (just added via the
  // + variant button). On first mount of a fresh variant's actor, we
  // send ADD_VARIANT to walk it from `noVariants → addingVariant` so
  // the inspector tells the right story (US-3 acceptance: "Add a
  // variant → inspector shows addingVariant"). Cleared after the
  // ADD_VARIANT send fires.
  let freshlyAdded = $state<Record<string, boolean>>({});

  function spawnBaseActor(row: Row): ParamEditorActor {
    const machine = makeParamEditorMachine();
    // Track 2 (ADR-017): consume any pending binding marker for this row
    // (set by loadPipeline after parsePipelineJSON). Removing it after
    // read makes the marker single-shot — re-spawning on suffix change
    // shouldn't re-seed bound state from a stale parse.
    const bindKey = `${nodeId}::${param.name}`;
    const pendingBV = get(pendingBoundVariables)[bindKey];
    if (pendingBV) {
      pendingBoundVariables.update(m => {
        const next = { ...m };
        delete next[bindKey];
        return next;
      });
    }
    const next = createActor(machine, {
      input: {
        nodeId,
        paramName: param.name,
        dirtyKeySuffix: `${dirtyKeySuffix}::${row.id}`,
        paramType: paramTypeForActor(),
        required: !!param.required,
        enumOptions: param.constraints?.enum,
        min: param.constraints?.min,
        max: param.constraints?.max,
        currentValue: committedValueFor(row),
        boundVariable: pendingBV ?? null,
      },
      inspect: inspectCallback,
    }) as unknown as ParamEditorActor;
    next.subscribe(() => { actorTick += 1; });
    // Forward committed values upstream regardless of what triggered
    // the commit. Without this, Lock-All (aggregator COMMIT_ALL) would
    // update the actor's context but never reach `data.paramValues`,
    // so the Run-button payload would still carry stale values — the
    // exact race US-2 was supposed to eliminate. Tracking lastValue
    // here suppresses redundant calls when RESET_TO echoes the parent's
    // own state back at us.
    let lastForwardedBase: unknown = committedValueFor(row);
    next.subscribe(snap => {
      if ((snap.value as string) !== 'committed') return;
      const cv = snap.context.currentValue;
      if (cv === lastForwardedBase) return;
      lastForwardedBase = cv;
      onBaseChange(cv);
    });
    next.start();
    registerEditorChild(rowAggregatorId(row), next);
    // Auto-EDIT base row from `viewing` (matches legacy first-render
    // unlock). Done after start so the initial transition fires inside
    // the actor's own loop, NOT inside a reactive Svelte effect.
    if (next.getSnapshot().value === 'viewing') {
      next.send({ type: 'EDIT' });
      autoEdited.add(row.id);
    }
    return next;
  }

  function spawnVariantActor(row: Row): VariantActor {
    const machine = makeVariantMachine();
    const cv = committedValueFor(row);
    const variantName = row.variantName!;
    const next = createActor(machine, {
      input: {
        paramName: param.name,
        variantId: variantName,
        paramType: paramTypeForActor(),
        required: !!param.required,
        enumOptions: param.constraints?.enum,
        min: param.constraints?.min,
        max: param.constraints?.max,
        currentValue: cv,
        siblingValues: siblingValuesFor(variantName),
      },
      inspect: inspectCallback,
    }) as unknown as VariantActor;
    next.subscribe(() => { actorTick += 1; });
    // Forward committed values upstream + re-broadcast siblings.
    // Same rationale as spawnBaseActor: aggregator-driven commits
    // (Lock All, Run preflight) bypass commitRow, so the only way to
    // keep `variants[variantName]` in lockstep with the actor is a
    // subscription installed at spawn time. `committed` AND
    // `mergingDuplicate` both update currentValue (variant.machine
    // committing.onDone actions); both are forwarded so the dict is
    // accurate either way.
    //
    // We compute the next dict from the LIVE actor map, not from the
    // closure-captured `variants` prop, because two concurrent commits
    // (A then B in the same microtask, e.g. Lock All) would otherwise
    // race: B's subscription would read `variants` before A's prop
    // update propagated, clobbering A. Reading currentValue from each
    // actor gives a self-consistent snapshot of "what every variant's
    // value should be right now" regardless of prop-update timing.
    let lastForwardedVariant: unknown = cv;
    next.subscribe(snap => {
      const v = snap.value as string;
      if (v !== 'committed' && v !== 'mergingDuplicate') return;
      const newVal = snap.context.currentValue;
      if (newVal === lastForwardedVariant) return;
      lastForwardedVariant = newVal;
      if (!onVariantsChange) return;
      const nextDict = currentVariantsFromActors(variantName, newVal);
      onVariantsChange(nextDict);
      broadcastSiblings(nextDict);
    });
    next.start();
    registerEditorChild(rowAggregatorId(row), next);
    // Auto-EDIT variant row from `committed` (matches legacy first-
    // render unlock). `noVariants` is handled by `freshlyAdded` ADD_VARIANT
    // flow instead.
    if (next.getSnapshot().value === 'committed') {
      next.send({ type: 'EDIT' });
      autoEdited.add(row.id);
    }
    return next;
  }

  /**
   * Build a `variants` dict from the live actor map, with `selfName`
   * overridden to `selfValue` (the value that just entered the actor's
   * context but hasn't yet propagated through props). Falls back to the
   * outer `variants` prop for any variant whose actor hasn't spawned yet
   * (shouldn't happen post-mount but guards against an edge case during
   * the spawn effect).
   */
  function currentVariantsFromActors(
    selfName: string,
    selfValue: unknown,
  ): Record<string, unknown> {
    const out: Record<string, unknown> = { ...variants };
    for (const r of rows) {
      if (r.isBase) continue;
      const a = actors[r.id];
      if (!a) continue;
      const cv = a.getSnapshot().context.currentValue;
      out[r.variantName!] = cv;
    }
    out[selfName] = selfValue;
    return out;
  }

  function teardownRowActor(rowId: string, actor: RowActor): void {
    unregisterEditorChild(`${nodeId}::${param.name}${dirtyKeySuffix}::${rowId}`, actor);
    actor.stop();
  }

  // Identity-change + row-list effect: keep `actors` in sync with the
  // current `rows` for the current (nodeId, paramName, suffix). On
  // identity change, tear down all; on row add/remove, diff.
  //
  // `actors` is a plain ref (not $state) — see the declaration comment
  // above. We bump `actorTick` after any mutation so consumers gated on
  // `void actorTick` re-render.
  $effect(() => {
    const id = `${nodeId}::${param.name}${dirtyKeySuffix}`;
    let mutated = false;
    if (id !== prevIdentity) {
      // Tear down everything from the previous identity.
      for (const [rowId, a] of Object.entries(actors)) {
        teardownRowActor(rowId, a);
        delete actors[rowId];
        mutated = true;
      }
      prevIdentity = id;
      prevRowIds = [];
      freshlyAdded = {};
      autoEdited.clear();
    }
    const currentIds = rows.map(r => r.id);
    // Remove actors for rows that disappeared (variant deleted).
    for (const rowId of prevRowIds) {
      if (!currentIds.includes(rowId)) {
        const a = actors[rowId];
        if (a) {
          teardownRowActor(rowId, a);
          delete actors[rowId];
          mutated = true;
        }
        delete freshlyAdded[rowId];
        autoEdited.delete(rowId);
      }
    }
    // Spawn actors for new rows.
    for (const row of rows) {
      if (!actors[row.id]) {
        actors[row.id] = row.isBase ? spawnBaseActor(row) : spawnVariantActor(row);
        mutated = true;
      }
    }
    prevRowIds = currentIds;
    if (mutated) {
      // Drive a re-render of consumers reading `actors[row.id]` (gated
      // on `void actorTick`). Without this bump, the template wouldn't
      // notice the new actors since `actors` is a plain ref.
      actorTick += 1;
    }
  });

  onDestroy(() => {
    for (const [rowId, a] of Object.entries(actors)) {
      teardownRowActor(rowId, a);
      delete actors[rowId];
    }
  });

  // Sync committed value back into the actor when parent updates
  // baseValue/variants from outside (e.g. another component edits the
  // node data). Only applied to settled actor states so we don't clobber
  // the user's draft mid-keystroke.
  $effect(() => {
    void baseValue;
    void variants;
    // Untrack so the actor's transition subscriber (which writes
    // actorTick) does NOT re-trigger this effect mid-mount, causing
    // effect_update_depth_exceeded under Svelte 5 strict mode.
    untrack(() => {
    for (const row of rows) {
      const a = actors[row.id];
      if (!a) continue;
      const v = committedValueFor(row);
      const snap = a.getSnapshot();
      const sv = snap.value as string;
      if (row.isBase) {
        if (sv === 'viewing' || sv === 'committed') {
          a.send({ type: 'RESET_TO', value: v });
        }
      } else {
        // Variant: settled-shaped states where RESET_TO is safe.
        if (sv === 'committed' || sv === 'noVariants') {
          a.send({ type: 'RESET_TO', value: v });
        }
      }
    }
    });
  });

  /**
   * Drive a freshly-added variant from `noVariants` into `addingVariant`
   * by sending ADD_VARIANT once. Acceptance for US-3: clicking
   * + variant should put the variantActor in `addingVariant` (visible
   * in Stately Inspector). Without this nudge the actor would sit in
   * `noVariants` until the user typed.
   */
  $effect(() => {
    void actorTick;
    for (const rowId of Object.keys(freshlyAdded)) {
      const a = actors[rowId];
      if (!a) continue;
      const snap = a.getSnapshot();
      if (snap.value === 'noVariants') {
        a.send({ type: 'ADD_VARIANT' });
        delete freshlyAdded[rowId];
      } else {
        // Already moved past noVariants — no nudge needed.
        delete freshlyAdded[rowId];
      }
    }
  });

  /**
   * Snapshot view for a row. Returns a uniform shape across paramEditor
   * and variant snapshots so the template doesn't have to branch.
   *   - editing: should the row render input widgets (vs locked display)?
   *   - draft: current draft value bound to inputs.
   *   - error: validationError to display.
   *   - merging: variant-only — actor is in mergingDuplicate.
   *   - confirmingDelete: variant-only — actor is in confirmingDelete.
   */
  // Track 2 (ADR-017) — Bind picker plumbing.
  // The picker is opened by sending OPEN_BIND_PICKER to the row's actor;
  // we read context.bindPickerOpen / context.boundVariable from the
  // snapshot via snapForRow's extended return. NO local Svelte state.
  function openBindPicker(row: Row): void {
    actors[row.id]?.send({ type: 'OPEN_BIND_PICKER' });
  }
  function closeBindPicker(row: Row): void {
    actors[row.id]?.send({ type: 'CLOSE_BIND_PICKER' });
  }
  function bindToVariable(row: Row, name: string): void {
    actors[row.id]?.send({ type: 'BIND_VARIABLE', name });
  }
  function unbindRow(row: Row): void {
    actors[row.id]?.send({ type: 'UNBIND_VARIABLE' });
  }
  function unbindAndEdit(row: Row): void {
    actors[row.id]?.send({ type: 'UNBIND_VARIABLE' });
    actors[row.id]?.send({ type: 'EDIT' });
  }
  /**
   * Subscribe to pipelineVariables so the picker re-renders when the
   * user adds/removes/edits a variable in the panel. Without this the
   * picker would render once and miss subsequent changes.
   */
  // Subscribe to pipelineVariables so the picker re-renders when the
  // user adds/removes/edits a variable in the panel. Use an explicit
  // store.subscribe (cleaned up onDestroy) instead of an `$effect` that
  // reads `$pipelineVariables` and writes a tick — that pattern hits
  // `effect_update_depth_exceeded` under Svelte 5 strict mode (the
  // jsdom/@testing-library mount path), because the auto-store's
  // tracking signal gets re-fired by the tick write itself.
  let varsTick = $state(0);
  const __pvUnsub = pipelineVariables.subscribe(() => { varsTick += 1; });
  onDestroy(() => { __pvUnsub(); });

  function variablesForPicker(): Array<{ name: string; type: string; value: unknown; compatible: boolean }> {
    void varsTick;
    // Local name avoids Svelte 5's reserved $-prefix for runes/store
    // auto-subscriptions; using `$vars` here was a compile error when
    // the component was mounted via @testing-library/svelte (caught by
    // incarnation 4's bind-UI tests; no production runtime would have
    // reached this code path without the same compile).
    const varsMap = get(pipelineVariables);
    const wantType = paramTypeForActor();
    return Object.entries(varsMap).map(([name, v]) => {
      const vtype = String(v.type);
      // Compatibility: exact match, or both "primitive-ish" (str/int/float).
      const compatible = vtype === wantType
        || (wantType === 'string' && vtype === 'str')
        || (wantType === 'enum' && (vtype === 'str' || vtype === 'string'));
      return { name, type: vtype, value: v.value, compatible };
    });
  }

  function snapForRow(row: Row): {
    editing: boolean;
    draft: string | boolean;
    error: string | null;
    merging: boolean;
    confirmingDelete: boolean;
    boundVariable: string | null;
    bindPickerOpen: boolean;
  } {
    void actorTick;
    const a = actors[row.id];
    if (!a) {
      return {
        editing: false, draft: '', error: null, merging: false, confirmingDelete: false,
        boundVariable: null, bindPickerOpen: false,
      };
    }
    const snap = a.getSnapshot();
    const v = snap.value as string;
    if (row.isBase) {
      const editing = v === 'editing' || v === 'committing' || v === 'invalid';
      return {
        editing,
        draft: snap.context.draftValue as string | boolean,
        error: snap.context.validationError,
        merging: false,
        confirmingDelete: false,
        boundVariable: (snap.context as { boundVariable?: string | null }).boundVariable ?? null,
        bindPickerOpen: !!(snap.context as { bindPickerOpen?: boolean }).bindPickerOpen,
      };
    }
    // Variant
    const editing =
      v === 'addingVariant' || v === 'editingValue' || v === 'committing';
    return {
      editing,
      draft: snap.context.draftValue as string | boolean,
      error: snap.context.validationError,
      merging: v === 'mergingDuplicate',
      confirmingDelete: v === 'confirmingDelete',
      boundVariable: null,
      bindPickerOpen: false,
    };
  }

  // On first mount of a row's actor, send EDIT to unlock it. Matches the
  // legacy baseline behavior (pre-actor-machine ValueList auto-unlocked
  // every row on first render). Applies to base rows in `viewing` and
  // variant rows in `committed` regardless of whether currentValue is
  // empty — saved values open unlocked too. Variants in `noVariants`
  // are handled by freshlyAdded above (separate flow). Variants in
  // `addingVariant` / `editingValue` are already unlocked.
  //
  // Tracked per-row in `autoEdited` so the effect fires EXACTLY once per
  // actor lifetime. Without this guard the effect would re-trigger on
  // every actorTick bump (incl. the bump from the EDIT it just sent),
  // producing an `effect_update_depth_exceeded` runaway under Svelte 5
  // strict mode (caught by incarnation 4's UI tests). Plain Set (not
  // $state) so reading/writing it does NOT track as a dependency or
  // trigger a re-run.
  // Auto-EDIT on first mount is now handled inline in spawnBaseActor /
  // spawnVariantActor (right before `next.start()`). Doing it via a
  // tracked $effect on `actorTick` triggered an
  // `effect_update_depth_exceeded` cascade under Svelte 5: the EDIT
  // send fires the actor's subscriber, which writes `actorTick`, which
  // re-runs the effect, etc. Inline-on-spawn fires once per actor and
  // doesn't touch reactive deps.
  const autoEdited = new Set<string>();

  function startEdit(row: Row): void {
    const a = actors[row.id];
    if (!a) return;
    if (row.isBase) {
      a.send({ type: 'EDIT' });
    } else {
      // Variant: EDIT re-opens a committed (or merged) row for editing.
      // From `mergingDuplicate`, the user clicks ✎ to dismiss the merge
      // notice and re-edit; we ACK_MERGE first so the actor lands in
      // `committed`, then EDIT.
      const snap = a.getSnapshot();
      if (snap.value === 'mergingDuplicate') {
        a.send({ type: 'ACK_MERGE' });
      }
      a.send({ type: 'EDIT' });
    }
  }

  function changeRow(row: Row, value: string | boolean): void {
    actors[row.id]?.send({ type: 'CHANGE_VALUE', value });
  }

  /**
   * Broadcast SIBLINGS_CHANGED to every OTHER variant actor in the
   * current rows. Called after a variant's value changes (commit or
   * merge) so dedup guards always reflect the latest dict (Edge Case #3).
   */
  function broadcastSiblings(updated: Record<string, unknown>): void {
    for (const r of rows) {
      if (r.isBase) continue;
      const a = actors[r.id];
      if (!a) continue;
      const others: unknown[] = [];
      for (const [vn, v] of Object.entries(updated)) {
        if (vn === r.variantName) continue;
        others.push(v);
      }
      a.send({ type: 'SIBLINGS_CHANGED', siblingValues: others });
    }
  }

  function commitRow(row: Row): void {
    // The spawn-time subscription installed in spawnBaseActor /
    // spawnVariantActor forwards committed values upstream; we don't
    // need a one-shot here. Sending COMMIT triggers the same
    // committing → committed (or → mergingDuplicate, → editingValue,
    // → invalid) path regardless of who initiated.
    actors[row.id]?.send({ type: 'COMMIT' });
  }

  function cancelRow(row: Row): void {
    actors[row.id]?.send({ type: 'CANCEL' });
  }

  /**
   * Variant-only ACK_MERGE: dismiss the dedup notice. The variant stays
   * in the parent's variants dict (with its duplicate value) until the
   * user explicitly deletes it; this preserves caller responsibility
   * for dict semantics per the variant.machine.ts contract.
   */
  function ackMerge(row: Row): void {
    if (row.isBase) return;
    actors[row.id]?.send({ type: 'ACK_MERGE' });
  }

  /**
   * Click × on a variant row: send DELETE to the variantActor. The
   * actor moves into `confirmingDelete` (modal-shaped state, Edge
   * Case #8) and the template swaps the widget for a confirm/cancel
   * affordance. The parent's variants dict is NOT mutated until the
   * user confirms — replaces the legacy synchronous deleteVariant()
   * path that mutated immediately.
   */
  function requestDeleteVariant(row: Row): void {
    if (row.isBase || !onVariantsChange) return;
    actors[row.id]?.send({ type: 'DELETE' });
  }

  function confirmDeleteVariant(row: Row): void {
    if (row.isBase || !onVariantsChange) return;
    const a = actors[row.id];
    if (!a) return;
    const variantName = row.variantName!;
    // Subscribe one-shot to detect the actor reaching the `deleted`
    // final state, then mutate the parent's variants dict. The row-list
    // effect will tear down the actor on the next render.
    const sub = a.subscribe(snap => {
      const v = snap.value as string;
      if (v === 'deleted' || snap.status === 'done') {
        sub.unsubscribe();
        const next = { ...variants };
        delete next[variantName];
        onVariantsChange!(next);
        delete expanded[row.id];
        // Re-broadcast siblings to remaining variant actors so their
        // dedup guards drop the now-deleted value from consideration.
        broadcastSiblings(next);
      }
    });
    a.send({ type: 'CONFIRM_DELETE' });
  }

  function cancelDeleteVariant(row: Row): void {
    if (row.isBase) return;
    actors[row.id]?.send({ type: 'CANCEL_DELETE' });
  }

  function nextVariantName(): string {
    const existing = Object.keys(variants);
    let n = existing.length + 1;
    while (existing.includes(`v${n}`)) n += 1;
    return `v${n}`;
  }

  function lexVariantCmp(a: string, b: string): number {
    const am = a.match(/^v(\d+)$/);
    const bm = b.match(/^v(\d+)$/);
    if (am && bm) return parseInt(am[1], 10) - parseInt(bm[1], 10);
    return a.localeCompare(b);
  }

  function addVariant(): void {
    if (singleValue || listDictDisabled || !onVariantsChange) return;
    const vn = nextVariantName();
    const next = { ...variants, [vn]: '' };
    // Mark the new row as freshly-added BEFORE the parent re-renders
    // with the new variants dict, so the spawn effect's
    // `freshlyAdded[row.id]` lookup will fire ADD_VARIANT on first
    // mount of the new actor.
    freshlyAdded[`v:${vn}`] = true;
    onVariantsChange(next);
  }

  function toggleExpand(row: Row): void {
    expanded[row.id] = !expanded[row.id];
  }

  function handleKeydown(row: Row, e: KeyboardEvent): void {
    const isTextarea = (e.currentTarget as HTMLElement).tagName === 'TEXTAREA';
    if (e.key === 'Enter' && (isTextarea ? e.ctrlKey : true)) {
      e.preventDefault();
      commitRow(row);
    } else if (e.key === 'Escape') {
      cancelRow(row);
    }
  }

  function displayValue(v: unknown): string {
    if (v === undefined || v === null) return '';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (isJson || (typeof v === 'object' && v !== null)) {
      try { return JSON.stringify(v); } catch { return String(v); }
    }
    return String(v);
  }

  function rowValueForDisplay(row: Row): string {
    return displayValue(committedValueFor(row));
  }

  let isSingleRow = $derived(rows.length === 1);
</script>

<div class="value-list">
  <div class:single-box={isSingleRow} class:multi-box={!isSingleRow}>
    {#each rows as row (row.id)}
      {@const snap = snapForRow(row)}
      {@const isEditing = snap.editing}
      {@const isExpanded = !!expanded[row.id]}
      {@const rowError = snap.error}
      {@const localStr = snap.draft as string}
      {@const localBool = snap.draft as boolean}
      {@const isMerging = snap.merging}
      {@const isConfirmingDelete = snap.confirmingDelete}
      {@const boundName = snap.boundVariable}
      {@const isBound = !!boundName}
      {@const pickerOpen = snap.bindPickerOpen}
      {@const _vt = varsTick /* re-render chip when pipelineVariables changes */}
      {@const resolvedVal = isBound ? (_vt, get(pipelineVariables))[boundName!]?.value : undefined}
      <div class="row"
           class:editing={isEditing}
           class:invalid={!!rowError}
           class:merging={isMerging}
           class:confirming-delete={isConfirmingDelete}
           class:bound={isBound}>
        <div class="gutter" title={isEditing ? 'unlocked' : 'locked in'}>
          {isEditing ? '🔓' : '🔒'}
        </div>

        {#if isConfirmingDelete}
          <!-- Variant confirmingDelete: modal-shaped state. Replaces
               the legacy synchronous deleteVariant() path. No parallel
               $state flag — the actor IS the prompt (Edge Case #8). -->
          <div class="widget confirm-prompt">
            <span class="confirm-text">Remove variant {row.variantName}?</span>
          </div>
          <div class="actions">
            <button type="button" class="act confirm-yes"
              onclick={() => confirmDeleteVariant(row)}
              title="confirm delete">✓</button>
            <button type="button" class="act confirm-no"
              onclick={() => cancelDeleteVariant(row)}
              title="cancel delete">×</button>
          </div>
        {:else if isBound}
          <!-- Track 2 (ADR-017): bound row — chip + greyed resolved value. -->
          <div class="widget bound-widget" data-testid="bound-row">
            <span class="bound-chip" title="Bound to pipeline variable">→ {boundName}</span>
            <span class="bound-resolved">{displayValue(resolvedVal)}</span>
          </div>
          <div class="actions">
            <button type="button" class="act unbind-edit" data-testid="unbind-edit"
              onclick={() => unbindAndEdit(row)}
              title="unbind and edit literal">✎</button>
            <button type="button" class="act unbind" data-testid="unbind"
              onclick={() => unbindRow(row)}
              title="unbind variable">×</button>
          </div>
        {:else}
          <div class="widget">
            {#if isBoolean}
              {#if isEditing}
                <button type="button" class="toggle" class:on={localBool === true}
                  onclick={() => changeRow(row, !localBool)}
                  aria-label="toggle">
                  <span class="knob"></span>
                </button>
                <span class="toggle-label">{localBool === true ? 'true' : 'false'}</span>
              {:else}
                <span class="toggle locked" class:on={committedValueFor(row) === true}>
                  <span class="knob"></span>
                </span>
                <span class="toggle-label">{committedValueFor(row) === true ? 'true' : 'false'}</span>
              {/if}
            {:else if isEnum}
              {#if isEditing}
                <select class="enum"
                  value={localStr ?? ''}
                  onchange={(e) => changeRow(row, (e.currentTarget as HTMLSelectElement).value)}
                  onkeydown={(e) => handleKeydown(row, e)}>
                  <option value="" disabled>-- pick --</option>
                  {#each param.constraints!.enum! as opt}
                    <option value={opt} selected={localStr === opt}>{opt}</option>
                  {/each}
                </select>
              {:else}
                <span class="readonly">{rowValueForDisplay(row) || '—'}</span>
              {/if}
            {:else if isString && isExpanded}
              {#if isEditing}
                <textarea class="multi"
                  value={localStr}
                  oninput={(e) => changeRow(row, (e.currentTarget as HTMLTextAreaElement).value)}
                  onkeydown={(e) => handleKeydown(row, e)}
                  rows="4" autofocus></textarea>
              {:else}
                <textarea class="multi" readonly value={rowValueForDisplay(row)} rows="4"></textarea>
              {/if}
            {:else}
              {#if isEditing}
                {#if columnOptions && !param.new_column && isString}
                  <!-- Track 1 (ADR-017): column_of_input combobox.
                       Datalist-backed input — dropdown of declared columns
                       with free-text fallback. Empty `all` falls through
                       to the plain input below via the `all.length > 0`
                       guard. patterns are shown as a hint chip. -->
                  {#if columnOptions.all.length > 0}
                    <input class="text" type="text"
                      list="cols-{nodeId}-{param.name}-{row.id}"
                      value={localStr}
                      data-testid="column-combobox"
                      oninput={(e) => changeRow(row, (e.currentTarget as HTMLInputElement).value)}
                      onkeydown={(e) => handleKeydown(row, e)}
                      placeholder={param.default !== undefined ? displayValue(param.default) : 'column'}
                      autofocus />
                    <datalist id="cols-{nodeId}-{param.name}-{row.id}">
                      {#each columnOptions.all as col}
                        <option value={col}></option>
                      {/each}
                    </datalist>
                    {#if columnOptions.patterns.length > 0}
                      <span class="pattern-hint" data-testid="column-pattern-hint"
                        title={`patterns: ${columnOptions.patterns.join(', ')}`}>
                        ⓘ {columnOptions.patterns.length}
                      </span>
                    {/if}
                  {:else}
                    <input class="text" type="text"
                      value={localStr}
                      data-testid="column-combobox-empty"
                      oninput={(e) => changeRow(row, (e.currentTarget as HTMLInputElement).value)}
                      onkeydown={(e) => handleKeydown(row, e)}
                      placeholder="no columns declared upstream — free text"
                      autofocus />
                  {/if}
                {:else}
                  <input class="text"
                    type={isNumber ? 'number' : 'text'}
                    value={localStr}
                    oninput={(e) => changeRow(row, (e.currentTarget as HTMLInputElement).value)}
                    onkeydown={(e) => handleKeydown(row, e)}
                    placeholder={param.default !== undefined ? displayValue(param.default) : (isNumber ? 'number' : 'value')}
                    autofocus />
                {/if}
              {:else}
                <input class="text" readonly value={rowValueForDisplay(row)} />
              {/if}
            {/if}
          </div>

          <div class="actions">
            {#if isString}
              <button type="button" class="act expand" onclick={() => toggleExpand(row)}
                title={isExpanded ? 'collapse' : 'expand to multi-line'}>{isExpanded ? '⤡' : '⤢'}</button>
            {/if}
            {#if isMerging}
              <button type="button" class="act ack-merge"
                onclick={() => ackMerge(row)}
                title="acknowledge merge with sibling">⇆</button>
              <button type="button" class="act edit"
                onclick={() => startEdit(row)}
                title="re-edit value">✎</button>
            {:else if isEditing}
              <button type="button" class="act commit"
                onclick={() => commitRow(row)}
                title={rowError ?? 'commit (Enter)'}>✓</button>
            {:else}
              <button type="button" class="act edit"
                onclick={() => startEdit(row)}
                title="unlock to edit">✎</button>
            {/if}
            {#if !row.isBase}
              <button type="button" class="act delete" onclick={() => requestDeleteVariant(row)}
                title="remove variant">×</button>
            {:else}
              <!-- Track 2 (ADR-017): chain icon opens the bind picker
                   for this row's actor. Sits next to the lock toggle by
                   the convention of "row-level affordances live here". -->
              <button type="button" class="act bind" data-testid="open-bind-picker"
                onclick={() => openBindPicker(row)}
                title="bind to pipeline variable">🔗</button>
            {/if}
          </div>
        {/if}
      </div>

      {#if pickerOpen}
        <!-- Track 2 (ADR-017) bind picker (per D-6: existing-only). -->
        {@const pickerVars = variablesForPicker()}
        <div class="bind-picker" data-testid="bind-picker">
          {#if pickerVars.length === 0}
            <div class="bind-picker-empty" data-testid="bind-picker-empty">
              No pipeline variables yet — create one in the Pipeline Variables panel.
            </div>
          {:else}
            <div class="bind-picker-list">
              {#each pickerVars as v}
                <button type="button"
                  class="bind-picker-item"
                  class:incompatible={!v.compatible}
                  data-testid="bind-picker-item"
                  data-variable-name={v.name}
                  disabled={!v.compatible}
                  title={v.compatible ? '' : `type ${v.type} not compatible with ${paramTypeForActor()}`}
                  onclick={() => bindToVariable(row, v.name)}>
                  <span class="picker-name">{v.name}</span>
                  <span class="picker-type">{v.type}</span>
                  <span class="picker-value">{displayValue(v.value)}</span>
                </button>
              {/each}
            </div>
          {/if}
          <button type="button" class="bind-picker-cancel"
            onclick={() => closeBindPicker(row)}
            data-testid="bind-picker-cancel">Cancel</button>
        </div>
      {/if}

      {#if isMerging}
        <div class="row-merge-notice">
          merged with sibling — duplicate of an existing variant value
        </div>
      {/if}

      {#if rowError}
        <div class="row-error">{rowError}</div>
      {/if}
    {/each}

    {#if !isSingleRow && !singleValue}
      <button type="button" class="add-variant-inline" onclick={addVariant}
        disabled={listDictDisabled}
        title={listDictDisabled ? 'List/dict params cannot be swept' : 'add another value'}>+ variant</button>
    {/if}
  </div>

  {#if isSingleRow && !singleValue}
    <button type="button" class="add-variant-btn" onclick={addVariant}
      disabled={listDictDisabled}
      title={listDictDisabled ? 'List/dict params cannot be swept' : 'add another value'}>+ variant</button>
  {/if}
</div>

<style>
  .value-list { width: 100%; }

  .single-box, .multi-box {
    border: 1px solid #3e3e42;
    border-radius: 4px;
    overflow: hidden;
    background: #1e1e1e;
  }

  .row {
    display: grid;
    grid-template-columns: 28px 1fr auto;
    align-items: stretch;
  }
  .multi-box .row + .row { border-top: 1px solid #2a2a2d; }
  .row.invalid .gutter { color: #E74C3C; }
  .row.merging .gutter { color: #C68E17; }
  .row.confirming-delete .gutter { color: #E74C3C; }

  .gutter {
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; background: #1b1b1d;
    border-right: 1px solid #2a2a2d;
    color: #6aa84f;
  }
  .row.editing .gutter { color: #E0B040; }

  .widget {
    display: flex; align-items: center; gap: 8px;
    padding: 0 8px; min-height: 32px;
  }
  .widget .readonly {
    color: #aaa; font-family: Consolas, monospace; font-size: 12px;
  }

  .widget.confirm-prompt {
    color: #E74C3C; font-family: inherit; font-size: 12px;
  }
  .confirm-text { color: #ddd; }

  input.text {
    background: transparent; border: none; outline: none;
    color: #ddd; width: 100%; padding: 6px 0;
    font-family: Consolas, monospace; font-size: 12px;
    box-sizing: border-box; min-height: 30px;
  }
  input.text:read-only { color: #aaa; }
  input.text[type=number]::-webkit-inner-spin-button,
  input.text[type=number]::-webkit-outer-spin-button {
    -webkit-appearance: none; margin: 0;
  }
  input.text[type=number] { -moz-appearance: textfield; }

  textarea.multi {
    background: transparent; border: none; outline: none;
    color: #ddd; width: 100%; padding: 6px 0;
    font-family: Consolas, monospace; font-size: 12px;
    box-sizing: border-box; resize: none; line-height: 1.4;
  }
  textarea.multi:read-only { color: #aaa; }

  select.enum {
    background: #1e1e1e; border: 1px solid #2a2a2d; outline: none;
    color: #ddd; width: 100%; padding: 5px 8px; min-height: 30px;
    font-family: Consolas, monospace; font-size: 12px;
    appearance: none; -webkit-appearance: none;
    padding-right: 22px;
    background-image:
      linear-gradient(45deg, transparent 50%, #888 50%),
      linear-gradient(135deg, #888 50%, transparent 50%);
    background-position: calc(100% - 12px) 50%, calc(100% - 7px) 50%;
    background-size: 5px 5px; background-repeat: no-repeat;
  }

  /* toggle */
  button.toggle, span.toggle {
    position: relative; display: inline-block;
    width: 32px; height: 18px; background: #3e3e42;
    border-radius: 10px; cursor: pointer; border: none; padding: 0;
    transition: background .15s;
  }
  button.toggle.on, span.toggle.on { background: #2d5a2d; }
  span.toggle.locked { cursor: default; }
  .toggle .knob {
    position: absolute; top: 2px; left: 2px;
    width: 14px; height: 14px; background: #888;
    border-radius: 50%; transition: .15s;
  }
  .toggle.on .knob { left: 16px; background: #6aa84f; }
  .toggle-label {
    color: #ccc; font-size: 12px; font-family: Consolas, monospace;
  }

  .actions {
    display: flex; align-items: stretch;
  }
  .act {
    display: flex; align-items: center; justify-content: center;
    background: transparent; border: none; cursor: pointer;
    font-size: 12px; color: #888; width: 28px; padding: 0;
  }
  .act.edit:hover { color: #4A90D9; }
  .act.commit { color: #6aa84f; font-size: 13px; }
  .act.commit:hover { color: #88c864; }
  .act.delete { color: #555; font-size: 14px; }
  .act.delete:hover { color: #E74C3C; }
  .act.expand:hover { color: #4A90D9; }
  .act.ack-merge { color: #C68E17; font-size: 13px; }
  .act.ack-merge:hover { color: #E0B040; }
  .act.confirm-yes { color: #E74C3C; font-size: 13px; }
  .act.confirm-yes:hover { color: #ff6b5b; }
  .act.confirm-no { color: #888; font-size: 14px; }
  .act.confirm-no:hover { color: #ccc; }
  .act-spacer { width: 28px; }

  .row-error {
    padding: 4px 8px 6px 36px;
    font-size: 11px; color: #E74C3C;
    background: #1e1e1e; border-top: 1px solid #2a2a2d;
  }
  .row-merge-notice {
    padding: 4px 8px 6px 36px;
    font-size: 11px; color: #C68E17;
    background: #1e1e1e; border-top: 1px solid #2a2a2d;
  }

  .add-variant-btn {
    margin-top: 6px; background: transparent; color: #888;
    border: 1px dashed #3e3e42; border-radius: 4px;
    width: 100%; padding: 6px 8px; font-size: 11px;
    cursor: pointer; font-family: inherit;
  }
  .add-variant-btn:hover:not(:disabled) { color: #ccc; border-color: #555; }
  .add-variant-btn:disabled { color: #444; border-color: #2a2a2d; cursor: not-allowed; }

  .add-variant-inline {
    width: 100%; padding: 6px 8px; background: transparent; color: #888;
    border: none; border-top: 1px dashed #3e3e42;
    font-size: 11px; cursor: pointer; font-family: inherit; text-align: center;
  }
  .add-variant-inline:hover:not(:disabled) { color: #ccc; background: #202022; }
  .add-variant-inline:disabled { color: #444; cursor: not-allowed; }

  /* Track 2 (ADR-017) bind chip + picker */
  .row.bound .gutter { color: #4A90D9; }
  .bound-widget { gap: 8px; }
  .bound-chip {
    background: rgba(74,144,217,0.15);
    color: #4A90D9; border: 1px solid rgba(74,144,217,0.4);
    padding: 2px 6px; border-radius: 3px; font-size: 11px;
    font-family: Consolas, monospace;
  }
  .bound-resolved { color: #888; font-size: 11px; font-family: Consolas, monospace; }
  .act.bind:hover { color: #4A90D9; }
  .act.unbind:hover { color: #E74C3C; }

  .bind-picker {
    background: #1b1b1d; border: 1px solid #4A90D9; border-radius: 3px;
    padding: 4px; margin: 4px 0;
  }
  .bind-picker-empty {
    color: #888; font-size: 11px; padding: 8px; font-style: italic;
  }
  .bind-picker-list { display: flex; flex-direction: column; gap: 2px; }
  .bind-picker-item {
    display: grid; grid-template-columns: 1fr auto auto; gap: 8px;
    background: transparent; border: none; color: #ccc;
    text-align: left; padding: 4px 8px; cursor: pointer; font-size: 11px;
    font-family: Consolas, monospace;
  }
  .bind-picker-item:hover:not(:disabled) { background: #094771; }
  .bind-picker-item.incompatible { color: #555; cursor: not-allowed; }
  .bind-picker-item.incompatible:hover { background: transparent; }
  .picker-type { color: #888; font-size: 10px; }
  .picker-value { color: #6aa84f; font-size: 10px; max-width: 80px; overflow: hidden; text-overflow: ellipsis; }
  .bind-picker-cancel {
    margin-top: 4px; width: 100%;
    background: transparent; border: 1px solid #3e3e42; color: #888;
    padding: 4px; border-radius: 3px; cursor: pointer; font-size: 11px;
  }
  .bind-picker-cancel:hover { color: #ccc; border-color: #555; }

  /* Track 1 (ADR-017): patterns hint chip on column_of_input combobox */
  .pattern-hint {
    color: #C68E17; font-size: 10px; padding: 0 4px;
    border: 1px solid #3e3e42; border-radius: 2px;
    cursor: help;
  }
</style>
