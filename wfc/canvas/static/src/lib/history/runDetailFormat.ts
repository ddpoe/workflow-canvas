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

// The grouping sets. They decide a header, never a viewer: which viewer opens
// a file is the server's `viewer` field. IMAGE_EXTS groups pdf with images,
// which the server gives the pdf viewer.
const IMAGE_EXTS = new Set(['png','jpg','jpeg','gif','svg','webp','pdf','tif','tiff']);
const DATA_EXTS  = new Set(['h5ad','h5','hdf5','parquet','csv','tsv','json','pkl','npy','npz','arrow','feather']);

/**
 * Bucket an artifact for the artifact list's grouped rendering.
 *
 * Directories win over any extension; a file the server gives the image
 * viewer is always an image, so the Images header's show all reaches every
 * image; otherwise the lowercased extension picks the bucket, falling back
 * to "other".
 *
 * Args:
 *     a: The artifact to categorise.
 *
 * Returns:
 *     Exactly one category key.
 */
export function categoryOf(a: Artifact): CatKey {
  if (deriveArtifactType(a) === 'dir') return 'directories';
  if (a.viewer === 'image') return 'images';
  const ext = (a.extension || '').toLowerCase();
  if (IMAGE_EXTS.has(ext)) return 'images';
  if (DATA_EXTS.has(ext)) return 'data';
  return 'other';
}

/** The colour family of a file's type pill. */
export type PillKind = 'image' | 'data' | 'other';

/**
 * Pick a file's type pill colour family: images and PDFs (by the server's
 * viewer), data files (by the Data grouping set), anything else grey.
 *
 * Args:
 *     file: A file row or folder member, with the server's `viewer` and its
 *         `extension`.
 *
 * Returns:
 *     The pill's colour family.
 */
export function pillKind(file: { viewer?: string | null; extension?: string | null }): PillKind {
  if (file.viewer === 'image' || file.viewer === 'pdf') return 'image';
  if (DATA_EXTS.has((file.extension || '').toLowerCase())) return 'data';
  return 'other';
}

/** A name longer than this is cut in the middle where the row is too narrow. */
export const NAME_CUT_MIN = 16;
/** How many of a cut name's last characters always show. */
export const NAME_TAIL = 10;

/**
 * Split a file name for a middle cut: a head that may shrink behind an
 * ellipsis and a tail that always shows, so the end and the extension stay
 * readable (`cluster_assig…ments.csv`). Where the cut falls is the row's
 * width (CSS); this only decides what the tail keeps.
 *
 * Args:
 *     name: The file or folder name as listed.
 *
 * Returns:
 *     `head + tail === name`; a name of at most `NAME_CUT_MIN` characters is
 *     all head, with an empty tail.
 */
export function splitNameForCut(name: string): { head: string; tail: string } {
  // By code point, so a character outside the BMP is never split in two.
  const chars = Array.from(name);
  if (chars.length <= NAME_CUT_MIN) return { head: name, tail: '' };
  const cut = chars.length - NAME_TAIL;
  return { head: chars.slice(0, cut).join(''), tail: chars.slice(cut).join('') };
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

/** What the artifact list says of an output wfc found changed after its run wrote it. */
export const CHANGED_OUTPUT_TEXT = 'changed after the run wrote it; run the step again';

/**
 * Name the outputs of a run that are unavailable because they changed after
 * the run wrote them.
 *
 * Args:
 *     outputAvailability: The run's per-output availability, if sent.
 *
 * Returns:
 *     The output names marked ``changed``, sorted; empty when none is.
 */
export function changedOutputs(
  outputAvailability: Readonly<Record<string, string>> | null | undefined,
): string[] {
  return Object.entries(outputAvailability ?? {})
    .filter(([, availability]) => availability === 'changed')
    .map(([name]) => name)
    .sort();
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
