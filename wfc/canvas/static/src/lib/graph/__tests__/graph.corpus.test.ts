/**
 * The shared shape corpus, Vitest half (Graph unit, testing requirement
 * "Agreement: the shared shape corpus").
 *
 * Reads the same literal entries as `tests/test_graph_corpus.py` — the
 * corpus sits outside the Vite root, so it is read with `fs` relative to
 * this file — and calls the pure functions under `src/lib/graph/`. An entry
 * the client does not answer is skipped visibly with its reason. Neither
 * side derives its own expectation.
 *
 * `CORPUS_MATRIX=1` runs the entries flagged `divergesUntilCommitB` instead
 * of skipping them — the executed agreement matrix.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, resolve } from 'node:path';
import { isDuplicateSlotEdge, type SlotLink } from '../legality';
import { collapsedNodeIds, collapsedBundledSamples, projectRuns } from '../projection';

// This file's directory: vite-node injects `__dirname`; fall back to the
// module URL where it does not.
const HERE = typeof __dirname === 'string'
  ? __dirname
  : fileURLToPath(new URL('.', import.meta.url));
const CORPUS = resolve(HERE, '../../../../../../../tests/shapes/graph');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Entry = any;

function entries(family: string): Entry[] {
  const dir = join(CORPUS, family);
  return readdirSync(dir)
    .filter(f => f.endsWith('.json'))
    .sort()
    .map(f => JSON.parse(readFileSync(join(dir, f), 'utf-8')));
}

const sameLink = (a: SlotLink, b: SlotLink) =>
  a.source === b.source && a.target === b.target
  && (a.targetHandle ?? null) === (b.targetHandle ?? null);

describe('shape corpus — legality (the connect-time predicate)', () => {
  for (const entry of entries('legality')) {
    const client = entry.client;
    if (!client.answers) {
      it.skip(`${entry.id} — client does not answer: ${client.reason}`, () => {});
      continue;
    }
    it(`${entry.id} → ${client.verdict}`, () => {
      const candidate: SlotLink = client.candidate;
      const existing: SlotLink[] = entry.document.links.filter(
        (l: SlotLink) => !sameLink(l, candidate),
      );
      const references = new Set<string>(
        entry.document.nodes
          .filter((n: { type?: string }) => n.type === 'run_reference')
          .map((n: { id: string }) => n.id),
      );
      const refused = isDuplicateSlotEdge(
        candidate, existing, id => !!id && references.has(id),
      );
      expect(refused ? 'refuse' : 'accept').toBe(client.verdict);
    });
  }
});

describe('shape corpus — projection (the runs preview)', () => {
  const matrixRun = process.env.CORPUS_MATRIX === '1';
  for (const entry of entries('projection')) {
    const client = entry.client;
    if (!client.answers) {
      it.skip(`${entry.id} — client does not answer: ${client.reason}`, () => {});
      continue;
    }
    if (client.divergesUntilCommitB && !matrixRun) {
      it.skip(`${entry.id} — preview diverges from the engine until commit B (agreement matrix)`, () => {});
      continue;
    }
    it(entry.id, () => {
      const doc = entry.document;
      expect([...collapsedNodeIds(doc)].sort()).toEqual([...entry.expect.collapsed].sort());
      expect(collapsedBundledSamples(doc)).toEqual(entry.expect.bundle);
      const rows = projectRuns(doc).map(r => ({
        node: r.nodeId, sample: r.sample, variant: r.variant, reused: r.reused,
      }));
      expect(rows).toEqual(entry.expect.rows);
    });
  }
});
