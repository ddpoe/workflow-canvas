/**
 * Vitest table for the run detail panel's formatting helpers.
 *
 * Every row records what the helper answers, including the answers a
 * reader would not predict —
 *
 *   - `formatMetric` stringifies a MISSING metric, so an absent key reads
 *     "undefined" rather than a dash or an empty string;
 *   - `formatParamValue` renders a bound `{"$var": name}` reference as its
 *     compact JSON, which names the variable, rather than as the ∅ it uses
 *     for an genuinely empty value.
 *
 * Integer formatting goes through toLocaleString, so the grouped row is
 * asserted against toLocaleString rather than a locale-pinned literal; the
 * exact-string rows carry the weight.
 */
import { describe, it, expect } from 'vitest';
import {
  categoryOf,
  formatMetric,
  formatParamValue,
  formatSampleReads,
  pickHighlights,
} from '../runDetailFormat';
import type { Artifact } from '../historyApi';

function art(partial: Partial<Artifact>): Artifact {
  return {
    name: partial.name ?? 'result.txt',
    size: partial.size ?? 0,
    is_image: partial.is_image ?? false,
    extension: partial.extension ?? 'txt',
    ...partial,
  };
}

describe('categoryOf', () => {
  it('buckets each artifact by kind, with directories winning over extension', () => {
    const rows: Array<[Artifact, string]> = [
      // A trailing slash is the directory heuristic; an explicit type wins too.
      [art({ name: 'figures/', extension: '' }), 'directories'],
      [art({ name: 'figures', extension: 'png', type: 'dir' }), 'directories'],
      // Images — including the wider set an <img> cannot paint.
      [art({ name: 'umap.png', extension: 'png' }), 'images'],
      [art({ name: 'report.pdf', extension: 'pdf' }), 'images'],
      [art({ name: 'SCAN.TIF', extension: 'TIF' }), 'images'],
      // Data.
      [art({ name: 'adata.h5ad', extension: 'h5ad' }), 'data'],
      [art({ name: 'counts.csv', extension: 'csv' }), 'data'],
      // Anything else, including a file with no extension at all.
      [art({ name: 'notes.txt', extension: 'txt' }), 'other'],
      [art({ name: 'LICENSE', extension: '' }), 'other'],
    ];

    expect(rows.map(([a]) => categoryOf(a))).toEqual(rows.map(([, want]) => want));
  });

  it('puts every artifact in exactly one category', () => {
    const all = [
      art({ name: 'figures/', extension: '' }),
      art({ name: 'umap.png', extension: 'png' }),
      art({ name: 'counts.csv', extension: 'csv' }),
      art({ name: 'notes.txt', extension: 'txt' }),
    ];

    const grouped: Record<string, Artifact[]> = {
      data: [], images: [], directories: [], other: [],
    };
    for (const a of all) grouped[categoryOf(a)].push(a);

    // The buckets partition the input: nothing dropped, nothing counted twice.
    const placed = Object.values(grouped).flat();
    expect(placed).toHaveLength(all.length);
    expect(new Set(placed).size).toBe(all.length);
  });
});

describe('formatMetric', () => {
  it('renders present metrics by number kind and stringifies a missing one', () => {
    // Floats: fixed to four decimals, so a long float truncates and a short
    // one pads.
    expect(formatMetric(0.87654321)).toBe('0.8765');
    expect(formatMetric(0.8)).toBe('0.8000');
    // Integers: locale grouping, so a small integer is bare.
    expect(formatMetric(3)).toBe('3');
    expect(formatMetric(1234)).toBe((1234).toLocaleString());
    // A missing or non-numeric metric is stringified as-is.
    expect(formatMetric(undefined)).toBe('undefined');
    expect(formatMetric(null)).toBe('null');
    expect(formatMetric('n/a')).toBe('n/a');
  });
});

describe('formatParamValue', () => {
  it('renders a literal, a bound variable reference and a list each in its own form', () => {
    // Genuinely empty values get the empty mark.
    expect(formatParamValue(null)).toBe('∅');
    expect(formatParamValue(undefined)).toBe('∅');
    // Literals.
    expect(formatParamValue(true)).toBe('true');
    expect(formatParamValue(false)).toBe('false');
    expect(formatParamValue('sample_a')).toBe('sample_a');
    expect(formatParamValue(42)).toBe('42');
    // A bound row reads as its variable, not as an empty value.
    expect(formatParamValue({ $var: 'threshold' })).toBe('{"$var":"threshold"}');
    // A list renders as compact JSON.
    expect(formatParamValue([1, 2, 3])).toBe('[1,2,3]');
  });
});

describe('formatSampleReads', () => {
  it('gives one "reads <sample> -> <slot>" line per recorded read, in order', () => {
    expect(formatSampleReads([
      { slot: 'dapi_image', sample: 'dapi_crop5' },
      { slot: 'raw', sample: 's1' },
    ])).toEqual([
      'reads dapi_crop5 -> dapi_image',
      'reads s1 -> raw',
    ]);
  });

  it('gives a run with no recorded reads no lines', () => {
    expect(formatSampleReads(undefined)).toEqual([]);
    expect(formatSampleReads(null)).toEqual([]);
    expect(formatSampleReads([])).toEqual([]);
  });
});

describe('pickHighlights', () => {
  it('takes the first four metrics in order, humanising keys and formatting values', () => {
    const highlights = pickHighlights({
      accuracy: 0.95,
      n_cells: 1200,
      f1_score: 0.8,
      loss: 0.01,
      dropped_because_fifth: 7,
    });

    expect(highlights).toEqual([
      { label: 'accuracy', value: '0.9500' },
      { label: 'n cells', value: (1200).toLocaleString() },
      { label: 'f1 score', value: '0.8000' },
      { label: 'loss', value: '0.0100' },
    ]);
  });

  it('gives a run with no metrics no highlights', () => {
    expect(pickHighlights({})).toEqual([]);
  });
});
