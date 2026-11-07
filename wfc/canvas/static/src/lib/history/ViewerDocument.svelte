<script lang="ts">
  /**
   * The artifact viewer's JSON and text bodies.
   *
   * Loads the file's preview: JSON parsed into a collapsible tree, or its Raw
   * text coloured by shiki; text with line numbers, coloured by shiki for py,
   * yaml, yml, toml, md and sh, plain otherwise. A JSON file the server
   * answers as text (too large, or not valid) is shown as plain text with
   * the server's reason. A cut preview says so.
   */
  import { fetchArtifactPreview } from './historyApi.js';
  import type { ArtifactPreview } from './historyApi.js';
  import { textLang } from './viewerFormat.js';
  import JsonNode from './JsonNode.svelte';
  import ViewerText from './ViewerText.svelte';

  interface Props {
    runId: string;
    path: string;
    viewer: 'json' | 'text';
    extension: string;
    wrap: boolean;
    jsonRaw: boolean;
  }

  let { runId, path, viewer, extension, wrap, jsonRaw }: Props = $props();

  let preview = $state<ArtifactPreview | null>(null);
  let error = $state<string | null>(null);

  $effect(() => {
    let live = true;
    fetchArtifactPreview(runId, path)
      .then(p => { if (live) preview = p; })
      .catch((e: unknown) => { if (live) error = e instanceof Error ? e.message : String(e); });
    return () => { live = false; };
  });

  /** The parsed document, when the server answered JSON. */
  let parsed = $derived.by((): { value: unknown } | null => {
    if (!preview || preview.kind !== 'json' || preview.text == null) return null;
    try {
      return { value: JSON.parse(preview.text) };
    } catch {
      return null;
    }
  });

  let note = $derived.by(() => {
    if (!preview?.reason) return null;
    const r = preview.reason;
    return `${r[0].toUpperCase()}${r.slice(1)}.${preview.truncated ? ' Download for the full file.' : ''}`;
  });
</script>

<div class="doc-body">
  {#if error}
    <div class="msg">No preview: {error}. Download to view.</div>
  {:else if !preview}
    <div class="msg">Loading…</div>
  {:else}
    {#if note}<div class="note" data-testid="viewer-text-note">{note}</div>{/if}
    {#if parsed && !jsonRaw}
      <div class="json" data-testid="viewer-json-tree"><JsonNode value={parsed.value} /></div>
    {:else if parsed}
      <ViewerText text={preview.text ?? ''} lang="json" {wrap} />
    {:else}
      <ViewerText text={preview.text ?? ''} lang={viewer === 'text' ? textLang(extension) : null} {wrap} />
    {/if}
  {/if}
</div>

<style>
  .doc-body { height: 100%; overflow: auto; }
  .json {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11.5px;
    padding: 12px 18px;
    line-height: 1.6;
    color: var(--text-primary);
  }
  .msg, .note {
    padding: 10px 14px 0;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    color: var(--text-muted);
  }
  .msg { color: var(--text-secondary); }
</style>
