/**
 * The one answer to "what colour is this module" (SOP Canvas Colours,
 * Module Colours).
 *
 * The server stores every module's colour and sends it as `color` on each
 * module (`/api/modules`, `/api/registry/modules`, the colour PATCH). The
 * canvas only remembers what it was sent: every view (the Builder node
 * header and palette, the Inspector header, History cards and Descendants
 * nodes, Registry chips) calls `moduleColor(name)` when it renders. The
 * colours live in one reactive map, so a colour changed in the picker shows
 * in every view at once, with no reload and no copy held in node data.
 */

/** The colour of a module the canvas has not been sent a colour for yet. */
export const MODULE_COLOR_UNKNOWN = '#5a5a60';

const colors = $state<Record<string, string>>({});

/**
 * The colour of a module.
 *
 * Reading it inside a component's markup or a `$derived` makes that render
 * follow later changes to the module's colour.
 *
 * Args:
 *     name: The module's name.
 *
 * Returns:
 *     The module's stored ``#rrggbb`` colour, or `MODULE_COLOR_UNKNOWN`
 *     when no colour has arrived for it.
 */
export function moduleColor(name: string | null | undefined): string {
  if (!name) return MODULE_COLOR_UNKNOWN;
  return colors[name] ?? MODULE_COLOR_UNKNOWN;
}

/**
 * Remember the colours the server sent with a list of modules.
 *
 * A module whose `color` is missing or null keeps whatever colour was
 * remembered before.
 *
 * Args:
 *     modules: Modules as any route sends them, each with its ``name`` and
 *         ``color``.
 */
export function rememberModuleColors(
  modules: Iterable<{ name: string; color?: string | null }>,
): void {
  for (const mod of modules) {
    if (mod.color) colors[mod.name] = mod.color;
  }
}

/** Forget every remembered colour. For tests that start from no modules. */
export function forgetModuleColors(): void {
  for (const name of Object.keys(colors)) delete colors[name];
}
