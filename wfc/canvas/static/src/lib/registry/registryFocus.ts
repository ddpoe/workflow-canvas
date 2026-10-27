/**
 * A request from another view to show something on the Registry tab.
 *
 * The History run detail's Cache-key inputs card links a method version and
 * a sample here. `App.svelte` watches the store to switch to the Registry
 * tab, and `RegistryTab.svelte` takes the request once, when its rows have
 * loaded, and clears it.
 */
import { writable } from 'svelte/store';

/** What the Registry tab is asked to show. */
export type RegistryFocus =
  | {
      /** The Methods sub-tab, with the method's row expanded on a version. */
      sub: 'methods';
      module: string;
      method: string;
      /** The version's content identity; null opens the newest. */
      versionIdentity: string | null;
      /** The version's label, for the note when it is no longer listed. */
      versionLabel: string | null;
    }
  | {
      /** The Samples sub-tab, scrolled to the top. */
      sub: 'samples';
    };

/** The pending request, or null when there is none. */
export const registryFocus = writable<RegistryFocus | null>(null);

/**
 * Ask the Registry tab to show a method's row, expanded on a version.
 *
 * Args:
 *     module: The method's module.
 *     method: The method's name.
 *     versionIdentity: The version's content identity, or null for the newest.
 *     versionLabel: The version's label (`v3`), or null.
 */
export function focusMethod(
  module: string,
  method: string,
  versionIdentity: string | null,
  versionLabel: string | null,
): void {
  registryFocus.set({ sub: 'methods', module, method, versionIdentity, versionLabel });
}

/** Ask the Registry tab to show its Samples sub-tab, at the top. */
export function focusSamples(): void {
  registryFocus.set({ sub: 'samples' });
}
