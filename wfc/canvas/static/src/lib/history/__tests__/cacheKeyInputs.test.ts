/**
 * Vitest for the structure of the run detail's "Cache-key inputs" card.
 *
 * Three runs, as the History payload sends them: a freshly executed run
 * with every value recorded, a cached run that read a sample, and an old
 * run that recorded nothing and was keyed under an earlier rule. They pin
 * the rows, every hash tag's kind, short and full value and meaning, the
 * params table's declared types and long-value detection, the slots
 * table's two row shapes, "not recorded" and the one earlier-rule line.
 */
import { describe, it, expect } from 'vitest';
import {
  cacheKeyInputs,
  EARLIER_RULE_LINE,
  NOT_RECORDED,
} from '../cacheKeyInputs';
import type { HashTag, KeyInputsRun } from '../cacheKeyInputs';

const CODE_FP = 'c0de0f1a2b3c4d5e6f708192a3b4c5d6';
const ENV_FP = 'e9b1d2c3a4f5061728394a5b6c7d8e9f';
const PARAMS_HASH = '3b7c0e55aa11bb22cc33dd44ee55ff66';
const INPUTS_HASH = 'e41f9a06' + '1'.repeat(56);
const PARENT_KEY = '5ca1ab1e' + '0'.repeat(56);
const SAMPLE_HASH = 'abcdef0123456789abcdef0123456789';
const COMMIT = '1fb99bc5d3e2a1b0c9f8e7d6c5b4a3f2e1d0c9b8';
const DOC_HASH = 'd0c0ffee112233445566778899aabbcc';

function fullRun(): KeyInputsRun {
  return {
    module: 'viz',
    method: 'plot',
    version: 3,
    versionIdentity: 'id-v3',
    pipelineId: 'pl-1',
    inputs: { threshold: 0.5, title: 'umap', extra: 1 },
    paramTypes: { threshold: 'float', title: 'str' },
    paramsHash: PARAMS_HASH,
    inputsHash: INPUTS_HASH,
    parents: [
      { slot: 'adata', sourceRunId: '12', sourceSlot: 'adata_out', cacheKey: PARENT_KEY },
    ],
    sampleInputs: [],
    codeFingerprint: CODE_FP,
    envName: 'scanpy-env',
    envFingerprint: ENV_FP,
    gitCommit: COMMIT,
    wfcVersion: '0.6.1',
    pipelineDocHash: DOC_HASH,
    keyedUnderEarlierRule: false,
  };
}

/** A tag's kind, label and short value, for compact comparison. */
function tagOf(t: HashTag): [string, string, string] {
  return [t.kind, t.label, t.short];
}

describe('cacheKeyInputs', () => {
  it('builds a fresh run\'s card: method with version and code tag, env, params, slots, then the recorded rows', () => {
    const card = cacheKeyInputs(fullRun());

    expect(card.earlierRule).toBeNull();
    expect(card.method).toMatchObject({
      name: 'viz.plot', module: 'viz', method: 'plot', version: 'v3', versionIdentity: 'id-v3',
    });
    expect(tagOf(card.method.tag)).toEqual(['code', 'code', 'c0de0f1a']);
    expect(card.env.name).toBe('scanpy-env');
    expect(tagOf(card.env.tag)).toEqual(['env', 'env', 'e9b1d2c3']);

    expect(card.params.label).toBe('3 params');
    expect(tagOf(card.params.tag)).toEqual(['params', 'params', '3b7c0e55']);
    expect(card.params.rows.map(r => [r.name, r.display, r.type, r.long])).toEqual([
      ['threshold', '0.5', 'float', false],
      ['title', '"umap"', 'str', false],
      // Not in the contract: no type, shown as a dash.
      ['extra', '1', null, false],
    ]);

    expect(card.slots.label).toBe('1 input slot');
    expect(tagOf(card.slots.tag)).toEqual(['inputs', 'inputs', 'e41f9a06']);
    expect(card.slots.rows).toHaveLength(1);
    const parent = card.slots.rows[0];
    expect(parent.kind).toBe('parent');
    if (parent.kind !== 'parent') throw new Error('unreachable');
    expect([parent.slot, parent.runId, parent.sourceSlot]).toEqual(['adata', '12', 'adata_out']);
    expect(tagOf(parent.tag)).toEqual(['run-key', 'run key', '5ca1ab1e']);

    // The commit is a tag like the hashes, its short form git's seven characters.
    expect(tagOf(card.recorded.commit)).toEqual(['commit', 'commit', '1fb99bc']);
    expect(card.recorded.commit.full).toBe(COMMIT);
    expect(card.recorded.wfcVersion).toBe('0.6.1');
    expect(tagOf(card.recorded.pipeline.tag)).toEqual(['pipeline', 'pipeline', 'd0c0ffee']);
    expect(card.recorded.pipeline.pipelineId).toBe('pl-1');
  });

  it('keeps every hash\'s full value for its copy icon and says on hover what it is computed from', () => {
    const card = cacheKeyInputs(fullRun());
    const tags = [
      card.method.tag, card.env.tag, card.params.tag, card.slots.tag,
      (card.slots.rows[0] as { tag: HashTag }).tag, card.recorded.commit, card.recorded.pipeline.tag,
    ];
    expect(tags.map(t => t.full)).toEqual([
      CODE_FP, ENV_FP, PARAMS_HASH, INPUTS_HASH, PARENT_KEY, COMMIT, DOC_HASH,
    ]);
    for (const t of tags) expect(t.meaning.length).toBeGreaterThan(0);
    // The env line names the env's contents only, never tags.
    expect(card.env.tag.meaning).toMatch(/contents of the env/);
    expect(card.env.tag.meaning).not.toMatch(/tag/i);
  });

  it('gives a cached run that read a sample one sample row with its content hash, and "none" for no params', () => {
    const card = cacheKeyInputs({
      ...fullRun(),
      inputs: {},
      paramTypes: {},
      parents: [],
      sampleInputs: [{ slot: 'raw', sample: 'pbmc3k', contentHash: SAMPLE_HASH }],
    });

    expect(card.params.label).toBe('none');
    expect(card.params.rows).toEqual([]);
    expect(card.slots.rows).toHaveLength(1);
    const read = card.slots.rows[0];
    expect(read.kind).toBe('sample');
    if (read.kind !== 'sample') throw new Error('unreachable');
    expect([read.slot, read.sample]).toEqual(['raw', 'pbmc3k']);
    expect(tagOf(read.tag)).toEqual(['content', 'content', 'abcdef01']);
    expect(read.tag.full).toBe(SAMPLE_HASH);
  });

  it('marks a value too long for one line with its size and a pretty-printed form; short values stay inline', () => {
    const regions = { roi: Array.from({ length: 100 }, (_, i) => ({ name: `r${i}`, x: i })) };
    const card = cacheKeyInputs({
      ...fullRun(),
      inputs: {
        regions,
        note: 'x'.repeat(61),
        fits: 'x'.repeat(58),
        four: [1, 2, 3, 4],
        three: [1, 2, 3],
      },
      paramTypes: { regions: 'dict', note: 'str', fits: 'str', four: 'list', three: 'list' },
    });
    const byName = Object.fromEntries(card.params.rows.map(r => [r.name, r]));

    expect(byName.regions.long).toBe(true);
    expect(byName.regions.size).toMatch(/^\d+(\.\d)? KB$/);
    expect(byName.regions.pretty).toBe(JSON.stringify(regions, null, 2));
    // Its hover previews the first eight lines, then "…".
    expect(byName.regions.preview).toBe(
      JSON.stringify(regions, null, 2).split('\n').slice(0, 8).join('\n') + '\n…');
    // Over 60 characters on one line (the quotes count).
    expect(byName.note.long).toBe(true);
    expect(byName.note.size).toBe('63 B');
    expect(byName.fits.long).toBe(false);
    // An array of more than three entries is long, three is not.
    expect(byName.four.long).toBe(true);
    expect(byName.three.long).toBe(false);
    expect(byName.three.size).toBeNull();
    expect(byName.three.pretty).toBeNull();
    expect(byName.three.preview).toBeNull();
  });

  it('reads "not recorded" for every missing value and says once, with no number, that the key used an earlier rule', () => {
    const card = cacheKeyInputs({
      module: 'viz',
      method: 'plot',
      version: null,
      versionIdentity: null,
      pipelineId: null,
      inputs: { threshold: 0.5 },
      paramTypes: {},
      paramsHash: null,
      inputsHash: null,
      parents: [{ slot: 'adata', sourceRunId: '7', sourceSlot: null, cacheKey: null }],
      sampleInputs: [{ slot: 'raw', sample: 's1', contentHash: null }],
      codeFingerprint: null,
      envName: null,
      envFingerprint: null,
      gitCommit: null,
      wfcVersion: null,
      pipelineDocHash: null,
      keyedUnderEarlierRule: true,
    });

    expect(card.method.version).toBeNull();
    expect(card.env.name).toBeNull();
    const tags = [
      card.method.tag, card.env.tag, card.params.tag, card.slots.tag,
      ...card.slots.rows.map(r => r.tag), card.recorded.commit, card.recorded.pipeline.tag,
    ];
    for (const t of tags) {
      expect(t.short).toBe(NOT_RECORDED);
      expect(t.full).toBeNull();
    }
    const parent = card.slots.rows[0];
    if (parent.kind !== 'parent') throw new Error('unreachable');
    expect(parent.sourceSlot).toBeNull();
    expect(card.recorded.wfcVersion).toBe(NOT_RECORDED);
    expect(card.earlierRule).toBe(EARLIER_RULE_LINE);
    expect(EARLIER_RULE_LINE).not.toMatch(/\d/);
  });
});
