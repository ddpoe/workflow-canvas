/**
 * Pure helpers behind the artifact viewer: what the amber note says, how a
 * text file is coloured and which lines are tinted, the 1:1 size an image
 * preview can reach.
 *
 * Kept out of the components so the rules are testable without a DOM.
 */
import type { ImageInfo, ViewerKind } from './historyApi.js';

/** One file the viewer can step to, as the artifact list hands it over. */
export interface ViewerItem {
  /** The displayed path: `name`, or `folder/member`. */
  path: string;
  name: string;
  size: number;
  /** The server's viewer for the file. */
  viewer: ViewerKind;
  /** The extension, without the dot. */
  extension: string;
  /** The file's type-pill colour, for its chip in the strip. */
  color: string;
}

/** The longer side of the image route's `large` size: the most the browser is sent. */
export const LARGE_SIDE = 4096;

/**
 * An image's size scaled down so its longer side is at most `longest`,
 * never scaled up: the image route's rule for each preview size.
 *
 * Args:
 *     width: The image's width in pixels.
 *     height: The image's height in pixels.
 *     longest: The most pixels the longer side may have.
 *
 * Returns:
 *     `{ width, height }`, each at least 1.
 */
export function fitWithin(width: number, height: number, longest: number): { width: number; height: number } {
  const side = Math.max(width, height);
  if (side <= longest) return { width, height };
  const scale = longest / side;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/**
 * The amber line under an image preview that differs from its file, or null
 * when the preview shows the whole file.
 *
 * Lists only what applies, in this order: a smaller size, a reduced bit
 * depth, a single page of several; then "Download for the full image".
 *
 * Args:
 *     info: The image-info answer for the size on screen.
 *
 * Returns:
 *     The note, e.g. "Preview 2048 x 1536 of 8192 x 6144 · 16-bit shown as
 *     8-bit · page 1 of 30 · Download for the full image", or null.
 */
export function previewNote(info: ImageInfo): string | null {
  const parts: string[] = [];
  if (info.preview_width < info.width || info.preview_height < info.height) {
    parts.push(`Preview ${info.preview_width} x ${info.preview_height} of ${info.width} x ${info.height}`);
  }
  if (info.bit_depth_reduced) parts.push(`${info.bit_depth}-bit shown as 8-bit`);
  if (info.pages > 1) parts.push(`page 1 of ${info.pages}`);
  if (parts.length === 0) return null;
  parts.push('Download for the full image');
  return parts.join(' · ');
}

/**
 * The text an image too large to preview shows in place of its preview:
 * its size, and the `[previews] memory_mb` that would let it show.
 *
 * Args:
 *     info: The image facts of the refused size: the file's width and
 *         height and the smallest memory_mb that admits it.
 *
 * Returns:
 *     "Too large to preview (W x H). To view it, set [previews] memory_mb to
 *     at least N in wf-canvas.toml, or download it."
 */
export function tooLargeText(info: Pick<ImageInfo, 'width' | 'height' | 'memory_mb_needed'>): string {
  return `Too large to preview (${info.width} x ${info.height}). `
    + `To view it, set [previews] memory_mb to at least ${info.memory_mb_needed} in wf-canvas.toml, or download it.`;
}

// Code-like text files shiki colours; every other text file stays plain.
const SHIKI_LANG: Record<string, string> = {
  py: 'python', yaml: 'yaml', yml: 'yaml', toml: 'toml', md: 'markdown', sh: 'shell',
};

/**
 * The shiki language a text file is coloured with, by its extension.
 *
 * Args:
 *     extension: The file's extension, without the dot, any case.
 *
 * Returns:
 *     A shiki language for py, yaml, yml, toml, md and sh; null (plain) for
 *     the rest, txt and log included.
 */
export function textLang(extension: string | null | undefined): string | null {
  return SHIKI_LANG[(extension || '').toLowerCase()] ?? null;
}

/**
 * The tint of one plain-text line: a line naming ERROR, or else WARN or
 * WARNING, as a whole word.
 *
 * Args:
 *     line: One line of the file.
 *
 * Returns:
 *     `'err'`, `'warn'` or `''`.
 */
export function lineTint(line: string): 'err' | 'warn' | '' {
  if (/\bERROR\b/.test(line)) return 'err';
  if (/\bWARN(ING)?\b/.test(line)) return 'warn';
  return '';
}

/** The fixed note under a table preview that stopped before the end of its file. */
export const TABLE_CUT_NOTE = 'Showing the first 100 rows. Download for the full file.';

/**
 * The parts of a preview's `reason` the viewer shows beside its own notes:
 * every reason except the row and byte caps, which the fixed notes say.
 *
 * Args:
 *     reason: The preview's `reason`, its parts joined by "; ", or null.
 *
 * Returns:
 *     The remaining parts, first letter capitalised.
 */
export function extraReasons(reason: string | null | undefined): string[] {
  return (reason ?? '')
    .split('; ')
    .filter(r => r && !r.startsWith('showing the first'))
    .map(r => r[0].toUpperCase() + r.slice(1));
}
