/**
 * The History run panel's width, in pixels, which dragging its left edge
 * changes. One value for every run, remembered in this browser (a per-viewer
 * convenience: a storage that refuses only loses the memory).
 */
import { writable, type Writable } from 'svelte/store';

/** The narrowest and widest the run panel may be, in pixels. */
export const RUN_DETAIL_MIN = 360;
export const RUN_DETAIL_MAX = 720;
/** Where the panel starts, and what a double-click on its edge resets it to. */
export const RUN_DETAIL_DEFAULT = 360;
/** How far one arrow key moves the edge, in pixels. */
export const RUN_DETAIL_STEP = 16;

const KEY = 'wfc.history.runDetailWidth';

/**
 * A width kept within the bounds, rounded to a whole pixel.
 *
 * Args:
 *     px: The asked-for width; anything not a finite number is the default.
 *
 * Returns:
 *     The width the panel takes.
 */
export function clampRunDetailWidth(px: number): number {
  if (!Number.isFinite(px)) return RUN_DETAIL_DEFAULT;
  return Math.round(Math.min(RUN_DETAIL_MAX, Math.max(RUN_DETAIL_MIN, px)));
}

function stored(): number {
  try {
    const v = globalThis.localStorage?.getItem(KEY);
    return v === null || v === undefined ? RUN_DETAIL_DEFAULT : clampRunDetailWidth(Number(v));
  } catch {
    return RUN_DETAIL_DEFAULT;
  }
}

function clampedStore(initial: number): Writable<number> {
  const inner = writable<number>(initial);
  return {
    subscribe: inner.subscribe,
    set: (v: number) => inner.set(clampRunDetailWidth(v)),
    update: (fn: (v: number) => number) => inner.update(v => clampRunDetailWidth(fn(v))),
  };
}

/** The run panel's width in pixels; every value set is clamped to the bounds. */
export const runDetailWidth = clampedStore(stored());

runDetailWidth.subscribe(v => {
  try {
    globalThis.localStorage?.setItem(KEY, String(v));
  } catch {
    // Storage refused (private window, blocked site data): the width is not remembered.
  }
});
