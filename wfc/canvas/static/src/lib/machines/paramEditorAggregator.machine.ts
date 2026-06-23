/**
 * Parent aggregator for per-row paramEditor / variant actors
 * (ADR-016 Phase 2 expand, cycle decision D-3).
 *
 * Replaces the legacy `commitAllSignal` writable + `dirtyParams` Set
 * pair from `stores.ts`. Each `ValueList.svelte` row registers its
 * paramEditorActor (or variantActor) here on mount; the aggregator
 * holds a lookup of registered children so the Run-button preflight
 * can fan COMMIT out to every editing child and `await` all of them
 * settling — replacing the 0.2.8 `setTimeout(0)` microtask race with
 * an explicit transition.
 *
 * State map:
 *
 *   idle  -- COMMIT_ALL --> committingAll
 *   committingAll -- (every editing child has reached settled) --> allCommitted
 *   allCommitted -- ANY (RESET / next COMMIT_ALL) --> idle
 *
 * `allCommitted` is intentionally NOT a final state — the Run-button
 * fires COMMIT_ALL on every preflight, so the aggregator must be
 * reusable across many runs in one session.
 *
 * Why an aggregator instead of flat siblings? See cycle D-3:
 *
 *   - Mirrors Phase 1's pipelineRunActor → nodeRunActor parent-child.
 *   - Stately Inspector renders one `paramEditorAggregator` root with
 *     all per-row children nested under it.
 *   - The Run-button preflight invokes a tiny `awaitAllCommitted` actor
 *     that subscribes to this machine and resolves on the
 *     `idle → committingAll → allCommitted` traversal.
 *
 * This module is pure xstate — Svelte runes integration lives in
 * `root.ts` (singleton actor) and `ValueList.svelte` (per-row REGISTER
 * / UNREGISTER on mount/destroy).
 */
import { setup, assign, type ActorRefFrom } from 'xstate';
import type { ParamEditorActor } from './paramEditor.machine';
import type { VariantActor } from './variant.machine';

export type ChildActor = ParamEditorActor | VariantActor;

export interface ParamEditorAggregatorContext {
  // Registered children, keyed by stable row identity (e.g.
  // "node_1::sample_name::base"). Components REGISTER on mount and
  // UNREGISTER on destroy so the aggregator never accumulates dead
  // refs across HMR / inspector tab swaps.
  children: Record<string, ChildActor>;
  // While `committingAll`, the set of child IDs we're still waiting
  // for. Each child that lands in a settled state ticks one off via
  // CHILD_SETTLED. Empty set → we transition to allCommitted.
  pending: Set<string>;
}

export type ParamEditorAggregatorEvent =
  | { type: 'REGISTER'; id: string; actor: ChildActor }
  | { type: 'UNREGISTER'; id: string }
  | { type: 'COMMIT_ALL' }
  | { type: 'CHILD_SETTLED'; id: string }
  | { type: 'RESET' };

/**
 * Predicates over a child actor's current snapshot.
 *
 * "Editing" = the child is in a state that should receive COMMIT
 * during a Lock All / preflight pulse. "Settled" = the child has
 * left editing-shaped states either by committing, cancelling, or
 * sitting in a terminal-ish state (committed/invalid/viewing/etc).
 *
 * We tolerate both paramEditor and variant value shapes since both
 * live in the same map.
 */
export function isChildEditing(actor: ChildActor): boolean {
  const v = actor.getSnapshot().value;
  if (typeof v !== 'string') return false;
  // paramEditor: editing | committing | invalid all carry pending work.
  // variant: addingVariant | editingValue | committing carry it too.
  // confirmingDelete is intentionally NOT counted — that's a modal
  // affordance, not a value-edit.
  return (
    v === 'editing' ||
    v === 'committing' ||
    v === 'invalid' ||
    v === 'addingVariant' ||
    v === 'editingValue'
  );
}

export function isChildSettled(actor: ChildActor): boolean {
  // Inverse of isChildEditing, plus excluding confirmingDelete (which
  // is not a commit-target). The aggregator calls this after sending
  // COMMIT to decide whether the child is still pending or done.
  const v = actor.getSnapshot().value;
  if (typeof v !== 'string') return false;
  // committing is mid-flight; still pending. Everything else is
  // either settled (committed, viewing, noVariants, mergingDuplicate,
  // committed-on-merge) or modal (confirmingDelete, deleted) — modal
  // states are settled for our purposes since they don't represent
  // a draft awaiting commit.
  if (v === 'committing') return false;
  if (v === 'editing' || v === 'addingVariant' || v === 'editingValue') return false;
  return true;
}

export function makeParamEditorAggregatorMachine() {
  return setup({
    types: {} as {
      context: ParamEditorAggregatorContext;
      events: ParamEditorAggregatorEvent;
    },
    actions: {
      registerChild: assign({
        children: ({ context, event }) => {
          if (event.type !== 'REGISTER') return context.children;
          return { ...context.children, [event.id]: event.actor };
        },
      }),
      unregisterChild: assign({
        children: ({ context, event }) => {
          if (event.type !== 'UNREGISTER') return context.children;
          const next = { ...context.children };
          delete next[event.id];
          return next;
        },
        pending: ({ context, event }) => {
          if (event.type !== 'UNREGISTER') return context.pending;
          if (!context.pending.has(event.id)) return context.pending;
          const next = new Set(context.pending);
          next.delete(event.id);
          return next;
        },
      }),
      // Send COMMIT to every editing child and seed `pending` with their
      // IDs. Children settle asynchronously via CHILD_SETTLED.
      commitAllChildren: assign({
        pending: ({ context }) => {
          const pending = new Set<string>();
          for (const [id, child] of Object.entries(context.children)) {
            if (isChildEditing(child)) {
              pending.add(id);
              try {
                child.send({ type: 'COMMIT' });
              } catch {
                // Child may have stopped between register and commit
                // (HMR, fast unmount). Treat as already-settled.
                pending.delete(id);
              }
            }
          }
          return pending;
        },
      }),
      tickPending: assign({
        pending: ({ context, event }) => {
          if (event.type !== 'CHILD_SETTLED') return context.pending;
          if (!context.pending.has(event.id)) return context.pending;
          const next = new Set(context.pending);
          next.delete(event.id);
          return next;
        },
      }),
      clearPending: assign({
        pending: () => new Set<string>(),
      }),
    },
    guards: {
      noPending: ({ context }) => context.pending.size === 0,
    },
  }).createMachine({
    id: 'paramEditorAggregator',
    initial: 'idle',
    context: () => ({
      children: {},
      pending: new Set<string>(),
    }),
    on: {
      REGISTER: { actions: 'registerChild' },
      UNREGISTER: { actions: 'unregisterChild' },
      RESET: { target: '.idle', actions: 'clearPending' },
    },
    states: {
      idle: {
        on: {
          COMMIT_ALL: [
            // If there are no editing children, we're already done —
            // skip straight to allCommitted so callers awaiting the
            // transition don't hang.
            {
              target: 'allCommitted',
              guard: ({ context }) => {
                for (const child of Object.values(context.children)) {
                  if (isChildEditing(child)) return false;
                }
                return true;
              },
            },
            {
              target: 'committingAll',
              actions: 'commitAllChildren',
            },
          ],
        },
      },
      committingAll: {
        on: {
          CHILD_SETTLED: [
            // Tick the child off the pending set. If the set is empty
            // afterwards, transition to allCommitted; otherwise stay.
            {
              target: 'allCommitted',
              actions: 'tickPending',
              guard: ({ context, event }) =>
                // Compute "would be empty after tick".
                context.pending.size === 1 &&
                context.pending.has(
                  (event as { id: string }).id,
                ),
            },
            { actions: 'tickPending' },
          ],
          // If a registered child unregisters mid-commit (e.g. variant
          // deleted while waiting), tickPending in unregisterChild will
          // remove it from the set; we may now be at zero. Re-evaluate
          // by sending ourselves a synthetic CHILD_SETTLED is awkward;
          // instead, the always-transition below catches the empty
          // pending case after any UNREGISTER.
          UNREGISTER: {
            actions: 'unregisterChild',
          },
        },
        always: [
          { target: 'allCommitted', guard: 'noPending' },
        ],
      },
      allCommitted: {
        // Reusable terminal — re-entering committingAll on the next
        // COMMIT_ALL is a normal Run-button cycle.
        on: {
          COMMIT_ALL: [
            {
              target: 'allCommitted',
              guard: ({ context }) => {
                for (const child of Object.values(context.children)) {
                  if (isChildEditing(child)) return false;
                }
                return true;
              },
            },
            {
              target: 'committingAll',
              actions: 'commitAllChildren',
            },
          ],
        },
      },
    },
  });
}

export type ParamEditorAggregatorActor = ActorRefFrom<
  ReturnType<typeof makeParamEditorAggregatorMachine>
>;
