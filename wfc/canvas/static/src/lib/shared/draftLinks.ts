/**
 * Links from the Builder and Inspector into the Registry for a method's
 * versions and its draft.
 *
 * Both ride the Registry's focus request: a version opens its method's row
 * expanded on that version; the draft asks for the version identity
 * `"draft"`, which the Registry reads as "open the draft's row on its Draft
 * run tab".
 */
import { focusMethod } from '../registry/registryFocus.js';

/** The version identity a Registry focus request uses for a method's draft. */
export const DRAFT_FOCUS_IDENTITY = 'draft';

/**
 * Open a method's draft in the Registry, on its Draft run tab.
 *
 * Args:
 *     module: The draft's module.
 *     method: The draft's method name.
 */
export function openDraftInRegistry(module: string, method: string): void {
  focusMethod(module, method, DRAFT_FOCUS_IDENTITY, 'DRAFT');
}

/**
 * Open one registered version of a method in the Registry.
 *
 * Args:
 *     module: The method's module.
 *     method: The method's name.
 *     identity: The version's content identity, or null for the newest.
 *     label: The version's label (`v3`), or null.
 */
export function openVersionInRegistry(
  module: string,
  method: string,
  identity: string | null,
  label: string | null,
): void {
  focusMethod(module, method, identity, label);
}
