<script lang="ts">
  /**
   * The artifact viewer's table body: the first 100 rows of a csv or tsv.
   *
   * The server guesses the delimiter and the header row; the Delimiter menu
   * (Auto shows the guess) and the Header row checkbox override them, each
   * change asking the preview route again. No sorting, no filtering and no
   * row count: the preview holds only the start of the file.
   */
  import { fetchArtifactPreview } from './historyApi.js';
  import type { ArtifactPreview, TableDelimiter } from './historyApi.js';
  import { extraReasons, TABLE_CUT_NOTE } from './viewerFormat.js';

  interface Props {
    runId: string;
    path: string;
  }

  let { runId, path }: Props = $props();

  const DELIMITERS: TableDelimiter[] = ['comma', 'tab', 'semicolon', 'pipe', 'space'];

  let choice = $state<'auto' | TableDelimiter>('auto');
  let header = $state<boolean | null>(null);
  /** The server's own guess, from the last answer with no delimiter override. */
  let guess = $state<TableDelimiter | null>(null);
  let preview = $state<ArtifactPreview | null>(null);
  let error = $state<string | null>(null);
  let loading = $state(true);

  $effect(() => {
    const delimiter = choice === 'auto' ? null : choice;
    const wantHeader = header;
    let live = true;
    loading = true;
    fetchArtifactPreview(runId, path, { delimiter, header: wantHeader })
      .then(p => {
        if (!live) return;
        preview = p;
        error = null;
        if (delimiter === null && p.table) guess = p.table.delimiter;
      })
      .catch((e: unknown) => {
        if (live) error = e instanceof Error ? e.message : String(e);
      })
      .finally(() => { if (live) loading = false; });
    return () => { live = false; };
  });

  let table = $derived(preview?.table ?? null);
  let columns = $derived.by(() => {
    if (!table) return [];
    if (table.columns) return table.columns;
    const width = table.rows.reduce((n, r) => Math.max(n, r.length), 0);
    return Array.from({ length: width }, (_, i) => `column_${i + 1}`);
  });
  let reasons = $derived(extraReasons(preview?.reason));
</script>

<div class="table-body">
  <div class="csv-bar">
    <label>
      Delimiter
      <select
        data-testid="viewer-delimiter"
        value={choice}
        onchange={(e) => { choice = (e.currentTarget as HTMLSelectElement).value as 'auto' | TableDelimiter; }}
      >
        <option value="auto">Auto{guess ? ` (${guess})` : ''}</option>
        {#each DELIMITERS as d (d)}
          <option value={d}>{d}</option>
        {/each}
      </select>
    </label>
    <label>
      <input
        type="checkbox"
        data-testid="viewer-header-row"
        checked={table?.header ?? true}
        onchange={(e) => { header = (e.currentTarget as HTMLInputElement).checked; }}
      />
      Header row
    </label>
    {#if loading}<span class="muted">loading…</span>{/if}
  </div>
  {#if error}
    <div class="msg">No preview: {error}. Download to view.</div>
  {:else if table}
    <table class="csv" data-testid="viewer-table">
      <thead>
        <tr><th class="rn"></th>{#each columns as c, i (i)}<th>{c}</th>{/each}</tr>
      </thead>
      <tbody>
        {#each table.rows as row, r (r)}
          <tr><td class="rn">{r + 1}</td>{#each row as cell, i (i)}<td>{cell}</td>{/each}</tr>
        {/each}
      </tbody>
    </table>
    {#if preview?.truncated}<div class="trunc" data-testid="viewer-table-note">{TABLE_CUT_NOTE}</div>{/if}
    {#each reasons as r (r)}<div class="trunc">{r}.</div>{/each}
  {/if}
</div>

<style>
  .table-body { height: 100%; overflow: auto; }
  .csv-bar {
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 8px 14px;
    font-size: 10.5px;
    color: var(--text-secondary);
    font-family: 'Consolas', 'Courier New', monospace;
    flex-wrap: wrap;
  }
  .csv-bar label { display: flex; gap: 5px; align-items: center; }
  .csv-bar select {
    background: var(--bg-input);
    color: var(--text-primary);
    border: 1px solid var(--border);
    font-size: 11px;
    padding: 2px 4px;
  }
  .muted { color: var(--text-muted); }
  table.csv {
    border-collapse: collapse;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    margin: 0 14px 14px;
  }
  table.csv th {
    position: sticky;
    top: 0;
    background: var(--bg-header);
    color: var(--text-primary);
    text-align: left;
    padding: 5px 10px;
    border-bottom: 1px solid var(--border);
    border-right: 1px solid #333;
    white-space: nowrap;
  }
  table.csv td {
    padding: 3px 10px;
    border-bottom: 1px solid #2c2c2e;
    border-right: 1px solid #2c2c2e;
    white-space: nowrap;
    color: var(--text-primary);
  }
  table.csv td.rn { color: var(--text-muted); text-align: right; background: #212122; }
  table.csv tr:hover td { background: #2a2d33; }
  .trunc, .msg {
    margin: 0 14px 14px;
    font-size: 10.5px;
    color: var(--text-muted);
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .msg { margin-top: 14px; color: var(--text-secondary); }
</style>
