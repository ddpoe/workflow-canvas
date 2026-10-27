/**
 * Open-lineage refusals: when the server answers the lineage route with 422,
 * the flash the run detail panel shows is the server's own `detail`.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { fetchLineagePipeline, lineageFailureMessage } from '../historyApi';

afterEach(() => vi.unstubAllGlobals());

const DETAIL =
  'Cannot open this lineage: run 2 (align.filter) ran a version that no version ' +
  'list records, so its node cannot be pinned to it. Add align.filter from the ' +
  'palette to run one of its registered versions.';

function stubLineageResponse(status: number, body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })),
  );
}

async function flashFor(runId: string): Promise<string> {
  try {
    await fetchLineagePipeline(runId);
  } catch (err) {
    return lineageFailureMessage(err);
  }
  throw new Error('fetchLineagePipeline resolved; a refusal was expected');
}

describe('opening a lineage the server refuses', () => {
  it('flashes the 422 body\'s detail as the server wrote it', async () => {
    stubLineageResponse(422, { detail: DETAIL });
    expect(await flashFor('2')).toBe(DETAIL);
  });

  it('still says the run was not found on 404', async () => {
    stubLineageResponse(404, { detail: 'Run not found: 9' });
    expect(await flashFor('9')).toBe('Run not found.');
  });
});
