/**
 * Deterministic slot colour, derived from the slot's declared `type`.
 *
 * The method-contract `type` field IS the file extension (the leading dot
 * is optional: `.h5ad` and `h5ad` are the same type), or the directory
 * marker `dir` / `directory`. `type` is open-ended, so colour is derived
 * client-side: a small curated map for
 * common extensions, with a deterministic HSL-hash fallback (mirroring
 * `historyUtils.ts::getModuleColor`) for anything else.
 */

const CURATED: Record<string, string> = {
  csv: '#F39C12',
  tsv: '#F39C12',
  parquet: '#9B59B6',
  json: '#4A90D9',
  txt: '#95A5A6',
  png: '#E74C3C',
  jpg: '#E74C3C',
  jpeg: '#E74C3C',
  svg: '#E74C3C',
  pkl: '#E9A847',
  h5ad: '#50C878',
  dir: '#1ABC9C',
  directory: '#1ABC9C',
};

/**
 * Undotted display form of a slot `type`: `.csv` -> `csv`, `csv` -> `csv`,
 * `dir` / `directory` untouched. Compound extensions keep their inner dots
 * (`.tar.gz` -> `tar.gz`). This is display-only — the canonical contract
 * form stays dotted on the backend.
 */
export function displaySlotType(slotType: string | null | undefined): string {
  if (!slotType) return '';
  const trimmed = slotType.trim();
  return trimmed.startsWith('.') ? trimmed.slice(1) : trimmed;
}

/**
 * Deterministic string -> hue HSL colour (same scheme as getModuleColor).
 */
function hashColor(value: string): string {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = value.charCodeAt(i) + ((hash << 5) - hash);
    hash = hash & hash;
  }
  const hue = ((hash % 360) + 360) % 360;
  return `hsl(${hue}, 55%, 55%)`;
}

/**
 * Resolve a slot `type` / extension string to a stable display colour.
 *
 * Curated common extensions get a fixed colour; everything else gets a
 * deterministic hash colour so distinct extensions stay visually distinct
 * across renders. Lookups are case-insensitive and dot-insensitive
 * (`csv` and `.csv` resolve to the same colour).
 */
export function slotColor(slotType: string | null | undefined): string {
  if (!slotType) return '#888';
  const key = displaySlotType(slotType).toLowerCase();
  return CURATED[key] ?? hashColor(key);
}

/**
 * Truncate a long `type`/extension for compact display, in the undotted
 * display form. The full value should be surfaced on hover (e.g. via a
 * `title` attribute) using `displaySlotType`.
 */
export function truncateSlotType(slotType: string | null | undefined, max = 8): string {
  const display = displaySlotType(slotType);
  if (!display) return '';
  return display.length > max ? display.slice(0, max - 1) + '…' : display;
}
