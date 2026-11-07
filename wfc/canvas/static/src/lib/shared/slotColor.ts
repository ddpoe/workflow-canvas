/**
 * Deterministic slot colour, derived from the slot's declared `type`.
 *
 * The method-contract `type` field IS the file extension (the leading dot
 * is optional: `.h5ad` and `h5ad` are the same type), or the directory
 * marker `dir` / `directory`. `type` is open-ended, so colour is derived
 * client-side: a small curated map for common extensions, with a hash into
 * the module palette for anything else. A slot type carries no run state,
 * so every colour here is a palette colour, never a protected one (SOP
 * Canvas Colours, The Governing Rule).
 */
import { paletteColorFor, paletteHex } from './palette.js';

const CURATED: Record<string, string> = {
  csv: paletteHex('olive'),
  tsv: paletteHex('olive'),
  parquet: paletteHex('indigo'),
  json: paletteHex('steel'),
  txt: paletteHex('dusk'),
  png: paletteHex('raspberry'),
  jpg: paletteHex('raspberry'),
  jpeg: paletteHex('raspberry'),
  svg: paletteHex('raspberry'),
  pkl: paletteHex('mauve'),
  h5ad: paletteHex('magenta'),
  dir: paletteHex('petrol'),
  directory: paletteHex('petrol'),
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
 * Resolve a slot `type` / extension string to a stable display colour.
 *
 * Curated common extensions get a fixed palette colour; everything else
 * gets a palette colour picked by a hash of the type, so it is stable across
 * renders. Lookups are case-insensitive and dot-insensitive (`csv` and
 * `.csv` resolve to the same colour). A missing type is neutral grey.
 */
export function slotColor(slotType: string | null | undefined): string {
  if (!slotType) return '#888';
  const key = displaySlotType(slotType).toLowerCase();
  return CURATED[key] ?? paletteColorFor(key);
}

/** Curated colours for env and artifact kinds; keys are lowercase. */
const KIND_CURATED: Record<string, string> = {
  // Contract (artifact) kinds.
  input: paletteHex('steel'),
  output: paletteHex('indigo'),
  metric: paletteHex('petrol'),
  // Env backends and package sources.
  pixi: paletteHex('cobalt'),
  conda: paletteHex('moss'),
  pip: paletteHex('rosewood'),
  uv: paletteHex('plum'),
  byo: paletteHex('dusk'),
  // The env spec chip itself.
  env: paletteHex('iris'),
};

/**
 * The colour of a kind that carries no run state: an env backend, a
 * package source, an artifact (contract) kind.
 *
 * Like slot types, kinds take a palette colour and never a protected one
 * (SOP Canvas Colours, The Governing Rule). Known kinds have a fixed colour;
 * any other kind gets a stable palette colour from a hash of its name.
 *
 * Args:
 *     kind: The kind's name, e.g. ``"pixi"`` or ``"output"``.
 *
 * Returns:
 *     A palette ``#RRGGBB`` hex; neutral grey for an empty kind.
 */
export function kindColor(kind: string | null | undefined): string {
  if (!kind) return '#888';
  const key = kind.trim().toLowerCase();
  return KIND_CURATED[key] ?? paletteColorFor(key);
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
