/**
 * The open method's split: the contract column's share of the panel's
 * width, in percent, which the divider between the contract and the code
 * drags. One value for every open method, remembered in this browser (a
 * per-viewer convenience: a storage that refuses only loses the memory).
 */
import { writable } from 'svelte/store';

/** The narrowest and widest the contract column may be, in percent. */
export const CONTRACT_MIN = 18;
export const CONTRACT_MAX = 60;
/** Where the split starts: contract 30%, code 70%. */
export const CONTRACT_DEFAULT = 30;

const KEY = 'wfc.registry.contractWidth';

/** A share kept within the bounds, rounded to a tenth. */
export function clampWidth(percent: number): number {
  if (!Number.isFinite(percent)) return CONTRACT_DEFAULT;
  return Math.round(Math.min(CONTRACT_MAX, Math.max(CONTRACT_MIN, percent)) * 10) / 10;
}

function stored(): number {
  try {
    const v = globalThis.localStorage?.getItem(KEY);
    return v === null || v === undefined ? CONTRACT_DEFAULT : clampWidth(Number(v));
  } catch {
    return CONTRACT_DEFAULT;
  }
}

/** The contract column's share, in percent. */
export const contractWidth = writable<number>(stored());

contractWidth.subscribe(v => {
  try {
    globalThis.localStorage?.setItem(KEY, String(v));
  } catch {
    // Storage refused (private window, blocked site data): the split is not remembered.
  }
});
