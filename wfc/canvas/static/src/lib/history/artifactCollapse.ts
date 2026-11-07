/**
 * Which artifact categories the run panel's list shows collapsed.
 *
 * One state per category for the whole session: a category collapsed on one
 * run stays collapsed on every other run opened after it. Held in this module
 * only, so a reload opens every category again.
 */
import { writable } from 'svelte/store';
import type { CatKey } from './runDetailFormat.js';

/** The collapsed categories; a category absent from the record is open. */
export const collapsedCategories = writable<Partial<Record<CatKey, boolean>>>({});

/**
 * Collapse an open category or open a collapsed one.
 *
 * Args:
 *     key: The category whose header was clicked.
 */
export function toggleCategory(key: CatKey): void {
  collapsedCategories.update(state => ({ ...state, [key]: !state[key] }));
}
