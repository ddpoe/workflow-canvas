/**
 * Deterministic slot colour, derived from the slot's declared `type`.
 *
 * The method-contract `type` field IS the file extension (dotted, e.g.
 * `.h5ad`), or the directory marker `dir` / `directory`. Slot colour is no
 * longer sourced from a fixed semantic-type enum (the old `/api/types`
 * `DATA_TYPES`); `type` is now open-ended, so colour is derived client-side:
 * a small curated map for common extensions, with a deterministic HSL-hash
 * fallback (mirroring `historyUtils.ts::getModuleColor`) for anything else.
 */

const CURATED: Record<string, string> = {
  '.csv': '#F39C12',
  '.tsv': '#F39C12',
  '.parquet': '#9B59B6',
  '.json': '#4A90D9',
  '.txt': '#95A5A6',
  '.png': '#E74C3C',
  '.jpg': '#E74C3C',
  '.jpeg': '#E74C3C',
  '.svg': '#E74C3C',
  '.pkl': '#E9A847',
  '.h5ad': '#50C878',
  dir: '#1ABC9C',
  directory: '#1ABC9C',
};

/**
 * Deterministic string -> hue HSL colour (ported from getModuleColor).
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
 * across renders. Lookups are case-insensitive.
 */
export function slotColor(slotType: string | null | undefined): string {
  if (!slotType) return '#888';
  const key = slotType.trim().toLowerCase();
  return CURATED[key] ?? hashColor(key);
}

/**
 * Truncate a long `type`/extension for compact display. The full value
 * should be surfaced on hover (e.g. via a `title` attribute).
 */
export function truncateSlotType(slotType: string | null | undefined, max = 8): string {
  if (!slotType) return '';
  return slotType.length > max ? slotType.slice(0, max - 1) + '…' : slotType;
}
