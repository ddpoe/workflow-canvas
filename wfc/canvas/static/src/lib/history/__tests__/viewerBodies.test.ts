/**
 * The viewer's table and text bodies, and the rules behind them.
 *
 * The table's Delimiter menu shows the server's guess under Auto and an
 * override is sent to the preview route, as is the Header row checkbox; a
 * cut table says so. Text keeps txt and log plain with WARN and ERROR lines
 * tinted; only code-like extensions are coloured by shiki.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, waitFor } from '@testing-library/svelte';
import ViewerTable from '../ViewerTable.svelte';
import { extraReasons, lineTint, textLang } from '../viewerFormat';

let urls: string[] = [];

function tableAnswer(url: string) {
  const delimiter = new URL(url, 'http://x').searchParams.get('delimiter') ?? 'tab';
  return {
    kind: 'table', truncated: true, shown_bytes: 10, total_bytes: 100,
    reason: 'showing the first 100 rows; the rows stop at a field csv cannot read (field larger than field limit)',
    table: {
      columns: ['gene', 'count'], rows: [['CD3E', '4'], ['LYZ', '9']],
      delimiter, delimiter_guessed: true, header: true, header_guessed: true,
    },
  };
}

beforeEach(() => {
  urls = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    const body = tableAnswer(url);
    return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('table body', () => {
  it('shows the guess under Auto, sends overrides, and says the table was cut', async () => {
    const view = render(ViewerTable, { props: { runId: 'r1', path: 'counts.tsv' } });
    await waitFor(() => expect(view.getByTestId('viewer-table')).toHaveTextContent('CD3E'));
    expect(urls).toEqual(['/api/wfc/run/r1/preview/counts.tsv']);
    expect(view.getByRole('option', { name: 'Auto (tab)' })).toBeInTheDocument();
    expect(view.getByTestId('viewer-table-note')).toHaveTextContent('Showing the first 100 rows. Download for the full file.');
    expect(view.getByText(/^The rows stop at a field csv cannot read/)).toBeInTheDocument();

    await fireEvent.change(view.getByTestId('viewer-delimiter'), { target: { value: 'comma' } });
    await waitFor(() => expect(urls.at(-1)).toBe('/api/wfc/run/r1/preview/counts.tsv?delimiter=comma'));
    // The guess shown under Auto stays the server's own.
    expect(view.getByRole('option', { name: 'Auto (tab)' })).toBeInTheDocument();

    await fireEvent.click(view.getByTestId('viewer-header-row'));
    await waitFor(() => expect(urls.at(-1)).toBe('/api/wfc/run/r1/preview/counts.tsv?delimiter=comma&header=false'));
  });
});

describe('text rules', () => {
  it('colours only py, yaml, yml, toml, md and sh', () => {
    expect(['py', 'yaml', 'YML', 'toml', 'md', 'sh'].map(textLang))
      .toEqual(['python', 'yaml', 'yaml', 'toml', 'markdown', 'shell']);
    expect(['txt', 'log', 'r', 'ini', '', null].map(textLang)).toEqual([null, null, null, null, null, null]);
  });

  it('tints ERROR and WARN lines as whole words', () => {
    expect(lineTint('14:02 ERROR disk full')).toBe('err');
    expect(lineTint('14:02 WARN qc dropped')).toBe('warn');
    expect(lineTint('WARNING: deprecated')).toBe('warn');
    expect(lineTint('ERRORS=0 WARNINGS_OFF')).toBe('');
  });

  it('leaves the row and byte caps to the fixed notes', () => {
    expect(extraReasons('showing the first 100 rows')).toEqual([]);
    expect(extraReasons(null)).toEqual([]);
  });
});
