/**
 * The artifact loaders put an artifact's path into the URL so the server reads
 * back exactly the name listed: a space, `#`, `?` or `%` in a name never ends
 * the path, starts a query or fragment, or decodes to another name; the `/`
 * between a folder and its member stays a separator.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  artifactDownloadUrl,
  artifactImageUrl,
  fetchArtifactPreview,
  fetchImageInfo,
} from '../historyApi';

const AWKWARD = ['plots #1/umap? 50%.png', 'a b.csv', 'qc#notes.txt', '100%.json', 'what?.log'];

/** The artifact path the server would decode from a loader's URL. */
function decodedPath(url: string, route: string): string {
  const u = new URL(url, 'http://canvas.test');
  const prefix = `/api/wfc/run/run%201/${route}/`;
  expect(u.pathname.startsWith(prefix)).toBe(true);
  // Nothing of the name leaked into a fragment.
  expect(u.hash).toBe('');
  return u.pathname.slice(prefix.length).split('/').map(decodeURIComponent).join('/');
}

function okJson(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('artifact loaders', () => {
  it('the download URL round-trips every name, a folder member keeping its separator', () => {
    for (const name of AWKWARD) {
      const url = artifactDownloadUrl('run 1', name);
      expect(decodedPath(url, 'artifact')).toBe(name);
      expect(new URL(url, 'http://canvas.test').search).toBe('');
    }
  });

  it('the image URL round-trips the name and asks for the size', () => {
    for (const name of AWKWARD) {
      const url = artifactImageUrl('run 1', name, 'thumb');
      expect(decodedPath(url, 'image')).toBe(name);
      expect([...new URL(url, 'http://canvas.test').searchParams]).toEqual([['size', 'thumb']]);
    }
  });

  it('the preview and image-info loaders fetch the encoded path with only their own query', async () => {
    const fetchMock = vi.fn(async () => okJson({}));
    vi.stubGlobal('fetch', fetchMock);

    await fetchArtifactPreview('run 1', 'tables #2/counts?.tsv', { delimiter: 'tab', header: false });
    await fetchArtifactPreview('run 1', 'a b.csv');
    await fetchImageInfo('run 1', 'plots #1/umap? 50%.png', 'view');

    const urls = fetchMock.mock.calls.map(call => String((call as unknown[])[0]));
    expect(decodedPath(urls[0], 'preview')).toBe('tables #2/counts?.tsv');
    expect(Object.fromEntries(new URL(urls[0], 'http://canvas.test').searchParams))
      .toEqual({ delimiter: 'tab', header: 'false' });
    // No override: the server guesses both, so neither is sent.
    expect(decodedPath(urls[1], 'preview')).toBe('a b.csv');
    expect(new URL(urls[1], 'http://canvas.test').search).toBe('');
    expect(decodedPath(urls[2], 'image-info')).toBe('plots #1/umap? 50%.png');
    expect(Object.fromEntries(new URL(urls[2], 'http://canvas.test').searchParams)).toEqual({ size: 'view' });
  });

  it('a refused preview raises the server\'s own words', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'no preview for this file type' }), { status: 415 },
    )));
    await expect(fetchArtifactPreview('run 1', 'markers.parquet')).rejects.toThrow('no preview for this file type');
  });
});
