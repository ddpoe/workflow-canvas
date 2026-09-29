/**
 * The one re-render tick for the Inspector.
 *
 * Svelte 5 runes re-evaluate a `$derived` only when a *reactive* dependency
 * changes. Two things the Inspector renders from are not reactive that way:
 * an xstate actor's snapshot, and a plain (non-`$state`) map mutated in an
 * effect. The standing workaround is to subscribe to the source and bump a
 * counter, then read `void <counter>` inside the derivation so it re-runs.
 *
 * This class is the one copy of that workaround in the Inspector: it owns the
 * counter, normalises the two unsubscribe shapes (a Svelte store hands back a
 * function, an xstate actor an object), and collects every disposer so a
 * component tears all of them down in one call.
 *
 * Both source kinds emit synchronously on subscribe, so a tick reads 1
 * immediately after construction.
 */

/** A change source: anything reporting through a `subscribe` call. */
export interface TickSource {
  subscribe(listener: () => void): (() => void) | { unsubscribe(): void };
}

/**
 * A reactive counter bumped by every change of every tracked source.
 *
 * Read `.count` inside a `$derived` / `$effect` to register the dependency;
 * the value itself carries no meaning beyond "something changed".
 */
export class RenderTick {
  #count = $state(0);
  #disposers: Array<() => void> = [];

  /**
   * Args:
   *     sources: Sources to track immediately. Omit to track later via
   *         `track` — the spawn-time case, where the source does not exist
   *         yet when the tick is created.
   */
  constructor(...sources: TickSource[]) {
    for (const source of sources) this.track(source);
  }

  /** The current count. Reading it registers the reactive dependency. */
  get count(): number {
    return this.#count;
  }

  /** Bump the count directly, for a change no source reports. */
  bump(): void {
    this.#count += 1;
  }

  /**
   * Track one more source, until this tick is disposed or the returned
   * release is called.
   *
   * Args:
   *     source: The store or actor whose changes should bump the count.
   *
   * Returns:
   *     A release that unsubscribes this one source and drops the tick's
   *     reference to its closure. A caller that outlives its sources — a
   *     component spawning and stopping one actor per rendered row —
   *     calls it as each source goes, so a stopped source is unreferenced
   *     rather than held until the component is destroyed. Calling it
   *     more than once is a no-op.
   */
  track(source: TickSource): () => void {
    const handle = source.subscribe(() => {
      this.#count += 1;
    });
    const stop = typeof handle === 'function' ? handle : () => handle.unsubscribe();
    let released = false;
    const release = (): void => {
      if (released) return;
      released = true;
      stop();
      const at = this.#disposers.indexOf(release);
      if (at >= 0) this.#disposers.splice(at, 1);
    };
    this.#disposers.push(release);
    return release;
  }

  /** Unsubscribe from every source still tracked. Call from `onDestroy`. */
  dispose(): void {
    // Drained first: each release splices itself out of the list, which
    // would skip entries if the list were iterated in place.
    for (const release of this.#disposers.splice(0)) release();
  }
}
