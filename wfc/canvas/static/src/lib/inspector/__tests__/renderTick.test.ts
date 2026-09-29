/**
 * `RenderTick` source lifetime.
 *
 * No other test reaches `renderTick.svelte.ts` directly; the
 * `valueList.*` tests mount the component that uses it, but a component
 * test cannot see whether a stopped row actor's subscription was released
 * or merely left tracked until the component is destroyed.
 *
 * The rows of interest are the two lifetimes a caller can have: a source
 * that outlives the tick (released by `dispose`) and a source the caller
 * drops first (released by the handle `track` returns) — `ValueList`
 * spawns and stops one actor per rendered row, so the second is the
 * common case.
 */
import { describe, expect, it } from 'vitest';
import { RenderTick } from '../renderTick.svelte.js';

/** A source shaped like a Svelte store: `subscribe` returns a function. */
function storeSource() {
  const listeners = new Set<() => void>();
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      listener();  // stores emit synchronously on subscribe
      return () => listeners.delete(listener);
    },
    emit() { for (const l of [...listeners]) l(); },
    get listenerCount() { return listeners.size; },
  };
}

/** A source shaped like an xstate actor: `subscribe` returns an object. */
function actorSource() {
  const listeners = new Set<() => void>();
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      return { unsubscribe: () => listeners.delete(listener) };
    },
    emit() { for (const l of [...listeners]) l(); },
    get listenerCount() { return listeners.size; },
  };
}

describe('RenderTick', () => {
  it('stops counting a released source and keeps counting the others', () => {
    const tick = new RenderTick();
    const kept = storeSource();
    const dropped = actorSource();
    tick.track(kept);
    const release = tick.track(dropped);
    const before = tick.count;

    release();

    expect(dropped.listenerCount).toBe(0);
    dropped.emit();
    expect(tick.count).toBe(before);

    kept.emit();
    expect(tick.count).toBe(before + 1);
  });

  it('releasing twice is a no-op, and dispose still releases the rest', () => {
    const tick = new RenderTick();
    const first = actorSource();
    const second = actorSource();
    const release = tick.track(first);
    tick.track(second);

    release();
    release();

    tick.dispose();

    expect(first.listenerCount).toBe(0);
    expect(second.listenerCount).toBe(0);
    second.emit();
    expect(tick.count).toBe(0);  // neither source ever reported a change
  });
});
