/**
 * Per-row actor wiring for the Inspector's value list.
 *
 * `ValueList.svelte` renders one row per value — the base row plus one per
 * variant — and each row is driven by its own spawned actor: a
 * `paramEditorActor` for the base, a `variantActor` for a variant. Spawning
 * one means creating it with the row's input, subscribing so committed
 * values reach the parent, starting it, registering it with the singleton
 * aggregator (so Lock All and the Run-button preflight can fan COMMIT_ALL
 * across every row), and unlocking the first render. Tearing one down means
 * unregistering it, releasing the render tick's subscription, and stopping
 * it.
 *
 * That wiring is plain logic with no markup and no runes, so it lives here
 * rather than in the component. The reconcile `$effect` lives in
 * `ValueList.svelte` and calls these functions, so the component decides
 * when a row's actor spawns and when it is torn down.
 *
 * Everything the wiring needs from the component — the current props, the
 * value lookups, the change callbacks — arrives through `RowActorContext`,
 * whose reactive fields are getters so a spawn always reads the live prop
 * rather than whatever it held when the context object was built.
 */
import { get } from 'svelte/store';
import { createActor } from 'xstate';
import {
  makeParamEditorMachine,
  type ParamEditorActor,
  type ParamEditorType,
} from '../machines/paramEditor.machine.js';
import {
  makeVariantMachine,
  type VariantActor,
} from '../machines/variant.machine.js';
import {
  inspect as inspectCallback,
  registerEditorChild,
  unregisterEditorChild,
} from '../machines/root.js';
import { pendingBoundVariables } from '../builder/pipeline.js';
import type { ParamDef } from '../shared/types.js';
import type { RenderTick } from './renderTick.svelte.js';

/** The constraint fields the machines take, exactly as `ParamDef` types them. */
type Constraints = NonNullable<ParamDef['constraints']>;

/** One rendered value: the base value, or one named variant. */
export type Row = { id: string; variantName: string | null; isBase: boolean };

/** Base rows hold a ParamEditorActor; variant rows hold a VariantActor. */
export type RowActor = ParamEditorActor | VariantActor;

/**
 * What the wiring needs from the component that owns the rows.
 *
 * The plain fields are declared readonly because the component supplies
 * them as getters over `$props()`; the functions close over the
 * component's live state.
 */
export interface RowActorContext {
  readonly nodeId: string;
  readonly paramName: string;
  readonly dirtyKeySuffix: string;
  readonly paramType: ParamEditorType;
  readonly required: boolean;
  readonly enumOptions: Constraints['enum'] | undefined;
  readonly min: Constraints['min'] | undefined;
  readonly max: Constraints['max'] | undefined;
  /** The tick every spawned actor's transitions bump. */
  readonly tick: RenderTick;
  /** Whether the component was given an `onVariantsChange` sink. */
  readonly hasVariantsSink: boolean;
  /** The row's committed value, from the component's props. */
  committedValueFor(row: Row): unknown;
  /** Every OTHER variant's committed value. */
  siblingValuesFor(variantName: string): unknown[];
  /** Forward a committed base value to the parent. */
  onBaseChange(value: unknown): void;
  /** Forward the updated variants dict to the parent. */
  onVariantsChange(next: Record<string, unknown>): void;
  /** Build the variants dict from the live actors, with one override. */
  nextVariants(selfName: string, selfValue: unknown): Record<string, unknown>;
  /** Re-broadcast SIBLINGS_CHANGED to the other variant rows. */
  broadcastSiblings(next: Record<string, unknown>): void;
  /** Record that a row was auto-unlocked on first render. */
  onAutoEdit(rowId: string): void;
}

/**
 * The render tick's per-actor release, kept here rather than in the
 * component so `teardownRowActor` keeps its (rowId, actor) signature.
 * Weak, so a dropped actor takes its entry with it.
 */
const trackReleases = new WeakMap<RowActor, () => void>();

/**
 * The aggregator id each actor was registered under, recorded at spawn.
 * Teardown unregisters this id rather than recomputing one: the context's
 * `nodeId` is a live prop getter, and on a node switch it already names the
 * next node by the time the old rows are torn down. Weak, like
 * `trackReleases`.
 */
const registeredIds = new WeakMap<RowActor, string>();

/** Register an actor with the aggregator and remember the id it went in under. */
function register(ctx: RowActorContext, rowId: string, actor: RowActor): void {
  const id = aggregatorIdFor(ctx, rowId);
  registeredIds.set(actor, id);
  registerEditorChild(id, actor);
}

/**
 * The id a row's actor is registered with in the aggregator.
 *
 * Args:
 *     ctx: The owning component's context.
 *     rowId: The row's id (`base`, or `v:<name>`).

 * Returns:
 *     The aggregator child id.
 */
export function aggregatorIdFor(ctx: RowActorContext, rowId: string): string {
  return `${ctx.nodeId}::${ctx.paramName}${ctx.dirtyKeySuffix}::${rowId}`;
}

/**
 * Spawn, start and register the base row's `paramEditorActor`.
 *
 * Args:
 *     ctx: The owning component's context.
 *     row: The base row.

 * Returns:
 *     The started actor.
 */
export function spawnBaseActor(ctx: RowActorContext, row: Row): ParamEditorActor {
  const machine = makeParamEditorMachine();
  // Consume any pending binding marker for this row
  // (set by loadPipeline after parsePipelineJSON). Removing it after
  // read makes the marker single-shot — re-spawning on suffix change
  // shouldn't re-seed bound state from a stale parse.
  const bindKey = `${ctx.nodeId}::${ctx.paramName}`;
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
      nodeId: ctx.nodeId,
      paramName: ctx.paramName,
      dirtyKeySuffix: `${ctx.dirtyKeySuffix}::${row.id}`,
      paramType: ctx.paramType,
      required: ctx.required,
      enumOptions: ctx.enumOptions,
      min: ctx.min,
      max: ctx.max,
      currentValue: ctx.committedValueFor(row),
      boundVariable: pendingBV ?? null,
    },
    inspect: inspectCallback,
  }) as unknown as ParamEditorActor;
  trackReleases.set(next, ctx.tick.track(next));
  // Forward committed values upstream regardless of what triggered
  // the commit. Without this, Lock-All (aggregator COMMIT_ALL) would
  // update the actor's context but never reach `data.paramValues`,
  // so the Run-button payload would carry stale values. Tracking lastValue
  // here suppresses redundant calls when RESET_TO echoes the parent's
  // own state back at us.
  let lastForwardedBase: unknown = ctx.committedValueFor(row);
  next.subscribe(snap => {
    if ((snap.value as string) !== 'committed') return;
    const cv = snap.context.currentValue;
    if (cv === lastForwardedBase) return;
    lastForwardedBase = cv;
    ctx.onBaseChange(cv);
  });
  next.start();
  register(ctx, row.id, next);
  // Auto-EDIT base row from `viewing`, so the row opens unlocked on its
  // first render. Done after start so the initial transition fires inside
  // the actor's own loop, NOT inside a reactive Svelte effect.
  if (next.getSnapshot().value === 'viewing') {
    next.send({ type: 'EDIT' });
    ctx.onAutoEdit(row.id);
  }
  return next;
}

/**
 * Spawn, start and register a variant row's `variantActor`.
 *
 * Args:
 *     ctx: The owning component's context.
 *     row: The variant row.

 * Returns:
 *     The started actor.
 */
export function spawnVariantActor(ctx: RowActorContext, row: Row): VariantActor {
  const machine = makeVariantMachine();
  const cv = ctx.committedValueFor(row);
  const variantName = row.variantName!;
  const next = createActor(machine, {
    input: {
      paramName: ctx.paramName,
      variantId: variantName,
      paramType: ctx.paramType,
      required: ctx.required,
      enumOptions: ctx.enumOptions,
      min: ctx.min,
      max: ctx.max,
      currentValue: cv,
      siblingValues: ctx.siblingValuesFor(variantName),
    },
    inspect: inspectCallback,
  }) as unknown as VariantActor;
  trackReleases.set(next, ctx.tick.track(next));
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
    if (!ctx.hasVariantsSink) return;
    const nextDict = ctx.nextVariants(variantName, newVal);
    ctx.onVariantsChange(nextDict);
    ctx.broadcastSiblings(nextDict);
  });
  next.start();
  register(ctx, row.id, next);
  // Auto-EDIT variant row from `committed`, so the row opens unlocked on
  // its first render. `noVariants` is handled by `freshlyAdded` ADD_VARIANT
  // flow instead.
  if (next.getSnapshot().value === 'committed') {
    next.send({ type: 'EDIT' });
    ctx.onAutoEdit(row.id);
  }
  return next;
}

/**
 * Unregister, untrack and stop one row's actor.
 *
 * The actor leaves the aggregator under the id it was registered with, not
 * one derived from the context now: after a node switch the context already
 * describes the next node, and a recomputed id would miss, leaving a stopped
 * actor in the commit set for good.
 *
 * Releasing the render tick's subscription here is what keeps a stopped
 * actor unreferenced: the tick holds one closure per tracked source, and
 * a component that spawns a row actor per rendered row would otherwise
 * accumulate them until it is destroyed.
 *
 * Args:
 *     _ctx: The owning component's context. Not read: see above.
 *     _rowId: The row's id, which may already be gone from the row list.
 *         Not read either; the registered id already carries it.
 *     actor: The actor to tear down.
 */
export function teardownRowActor(_ctx: RowActorContext, _rowId: string, actor: RowActor): void {
  const id = registeredIds.get(actor);
  if (id !== undefined) unregisterEditorChild(id, actor);
  registeredIds.delete(actor);
  const release = trackReleases.get(actor);
  if (release) {
    release();
    trackReleases.delete(actor);
  }
  actor.stop();
}
