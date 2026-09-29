/**
 * Formatting helpers for the History tab's run detail panel.
 *
 * The panel renders parameters, metrics and the overview highlights, while
 * its artifact-list child groups artifacts by category, so both need these.
 * A plain module is also testable without mounting the panel.
 */
import { deriveArtifactType } from './historyApi.js';
import type { Artifact } from './historyApi.js';

/** The artifact grouping buckets, in no particular order. */
export type CatKey = 'data' | 'images' | 'directories' | 'other';

// The grouping sets. IMAGE_EXTS is deliberately WIDER than the backend's
// `is_image` flag: it includes pdf/tif/tiff, which group visually with
// images but which an <img> cannot paint, so they never get an inline
// preview. See the preview gate in ArtifactList.svelte.
const IMAGE_EXTS = new Set(['png','jpg','jpeg','gif','svg','webp','pdf','tif','tiff']);
const DATA_EXTS  = new Set(['h5ad','h5','hdf5','parquet','csv','tsv','json','pkl','npy','npz','arrow','feather']);

/**
 * Bucket an artifact for the artifact list's grouped rendering.
 *
 * Directories win over any extension; otherwise the lowercased extension
 * picks the bucket, falling back to "other".
 *
 * Args:
 *     a: The artifact to categorise.
 *
 * Returns:
 *     Exactly one category key.
 */
export function categoryOf(a: Artifact): CatKey {
  if (deriveArtifactType(a) === 'dir') return 'directories';
  const ext = (a.extension || '').toLowerCase();
  if (IMAGE_EXTS.has(ext)) return 'images';
  if (DATA_EXTS.has(ext)) return 'data';
  return 'other';
}

/**
 * Render a metric value as the panel shows it.
 *
 * Integers get locale grouping; other numbers are fixed to four decimals.
 * A non-number (including a missing metric) is stringified as-is.
 *
 * Args:
 *     v: The metric value.
 *
 * Returns:
 *     The display string.
 */
export function formatMetric(v: unknown): string {
  if (typeof v !== 'number') return String(v);
  return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(4);
}

/**
 * Render a recorded parameter value as the panel shows it.
 *
 * Null and undefined render as the empty mark; booleans as their literal;
 * any object — including a ``{"$var": name}`` bound-variable reference and a
 * list — as compact JSON; everything else via String().
 *
 * Args:
 *     v: The parameter value.
 *
 * Returns:
 *     The display string.
 */
export function formatParamValue(v: unknown): string {
  if (v === null || v === undefined) return '∅';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

/**
 * Render a run's recorded sample reads as the panel's lines under its
 * parent chips.
 *
 * One line per entry, in the order the server sent them, reading
 * "reads <sample> -> <slot>".
 *
 * Args:
 *     sampleInputs: The run's recorded sample reads, if any.
 *
 * Returns:
 *     One display line per read; empty when the field is absent or empty.
 */
export function formatSampleReads(
  sampleInputs: ReadonlyArray<{ slot: string; sample: string }> | null | undefined,
): string[] {
  return (sampleInputs ?? []).map(({ slot, sample }) => `reads ${sample} -> ${slot}`);
}

/**
 * Pick the overview tab's highlight cards from a run's metrics.
 *
 * Takes the first four metrics in insertion order, humanising the key and
 * formatting the value the same way the metrics tab does.
 *
 * Args:
 *     metrics: The run's metrics, keyed by name.
 *
 * Returns:
 *     Up to four label/value pairs, in the metrics' own order.
 */
export function pickHighlights(metrics: Record<string, number>): Array<{ label: string; value: string }> {
  const entries = Object.entries(metrics);
  return entries.slice(0, 4).map(([k, v]) => ({
    label: k.replace(/_/g, ' '),
    value: formatMetric(v),
  }));
}
