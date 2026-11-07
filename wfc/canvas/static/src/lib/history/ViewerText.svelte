<script lang="ts">
  /**
   * Text with line numbers: shiki-coloured for a code-like language, else
   * plain with WARN and ERROR lines tinted. Shared by the text viewer, the
   * JSON viewer's Raw view and a JSON file shown as text.
   */
  import { highlight } from '../shared/shikiHighlight.js';
  import { lineTint } from './viewerFormat.js';

  interface Props {
    text: string;
    /** A shiki language, or null for plain text. */
    lang: string | null;
    wrap: boolean;
  }

  let { text, lang, wrap }: Props = $props();

  let html = $state<string | null>(null);
  let lines = $derived(lang ? [] : text.split(/\r?\n/));

  $effect(() => {
    html = null;
    if (!lang) return;
    let live = true;
    highlight(text, lang).then(h => { if (live) html = h; }).catch(() => { /* stays plain */ });
    return () => { live = false; };
  });
</script>

{#if lang && html}
  <div class="code" class:wrap data-testid="viewer-code">{@html html}</div>
{:else if lang}
  <div class="text" class:wrap data-testid="viewer-text">
    {#each text.split(/\r?\n/) as line, i (i)}
      <div class="ln"><span class="no">{i + 1}</span><span class="tx">{line}</span></div>
    {/each}
  </div>
{:else}
  <div class="text" class:wrap data-testid="viewer-text">
    {#each lines as line, i (i)}
      <div class="ln"><span class="no">{i + 1}</span><span class="tx {lineTint(line)}">{line}</span></div>
    {/each}
  </div>
{/if}

<style>
  .text, .code {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11.5px;
    line-height: 1.55;
    padding: 10px 0;
  }
  .ln { display: flex; }
  .no {
    width: 46px;
    text-align: right;
    padding-right: 12px;
    color: #555;
    user-select: none;
    flex: none;
  }
  .tx { white-space: pre; color: #d4d4d4; padding-right: 20px; }
  .wrap .tx { white-space: pre-wrap; overflow-wrap: anywhere; }
  .tx.warn { color: #E9A847; }
  .tx.err { color: var(--status-failed-ink); }

  /* Shiki's markup: one .line per line, numbered by a counter. */
  .code :global(pre.shiki) {
    margin: 0;
    background: transparent !important;
    white-space: pre;
  }
  .code.wrap :global(pre.shiki) { white-space: pre-wrap; overflow-wrap: anywhere; }
  .code :global(code) { counter-reset: line; display: block; }
  .code :global(.line) { display: inline-block; padding-right: 20px; }
  .code :global(.line)::before {
    counter-increment: line;
    content: counter(line);
    display: inline-block;
    width: 46px;
    padding-right: 12px;
    text-align: right;
    color: #555;
    user-select: none;
  }
</style>
