/**
 * The sixteen named palette colours (SOP Canvas Colours, Module Colours).
 *
 * Module colours come from the server (`color` on each module); the canvas
 * never decides whether a colour may be a module colour. The palette is
 * listed here for two uses only: the Colour picker's swatches, and the
 * colours of kinds that carry no run state (slot types, env kinds, artifact
 * kinds), which take a palette colour and never a protected one.
 */

/** One named palette colour. */
export interface PaletteColor {
  name: string;
  hex: string;
}

/** The palette, in the SOP's table order (left column, then right). */
export const PALETTE: readonly PaletteColor[] = [
  { name: 'moss', hex: '#687B00' },
  { name: 'drab', hex: '#627143' },
  { name: 'petrol', hex: '#06809C' },
  { name: 'steel', hex: '#3F708D' },
  { name: 'denim', hex: '#4D6B98' },
  { name: 'cobalt', hex: '#3C65DE' },
  { name: 'dusk', hex: '#676794' },
  { name: 'rose', hex: '#AA4B70' },
  { name: 'indigo', hex: '#6E5CD6' },
  { name: 'magenta', hex: '#B53887' },
  { name: 'mauve', hex: '#8A5C75' },
  { name: 'plum', hex: '#AC5B89' },
  { name: 'raspberry', hex: '#CE3C7C' },
  { name: 'iris', hex: '#6467B4' },
  { name: 'olive', hex: '#737A38' },
  { name: 'rosewood', hex: '#A06579' },
];

/**
 * The hex of a named palette colour.
 *
 * Args:
 *     name: A palette name, e.g. ``"steel"``.
 *
 * Returns:
 *     Its ``#RRGGBB`` hex.
 *
 * Raises:
 *     Error: When the name is not in the palette.
 */
export function paletteHex(name: string): string {
  const found = PALETTE.find(c => c.name === name);
  if (!found) throw new Error(`no palette colour named ${name}`);
  return found.hex;
}

/**
 * A stable palette colour for a kind key (a slot type, an env kind, an
 * artifact kind) that has no curated colour.
 *
 * Args:
 *     key: The kind's name.
 *
 * Returns:
 *     A palette hex chosen by a hash of the key.
 */
export function paletteColorFor(key: string): string {
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = (hash * 31 + key.charCodeAt(i)) | 0;
  }
  return PALETTE[((hash % PALETTE.length) + PALETTE.length) % PALETTE.length].hex;
}
