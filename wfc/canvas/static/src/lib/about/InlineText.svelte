<script lang="ts">
  /**
   * One changelog line drawn from its markdown: inline code, bold and
   * http(s) links (opened in a new tab); everything else is plain text.
   */
  import { inlineMarkdown } from './versionMenu.js';

  let { text }: { text: string } = $props();
  const tokens = $derived(inlineMarkdown(text));
</script>

{#each tokens as t, i (i)}{#if t.kind === 'code'}<code>{t.text}</code>{:else if t.kind === 'bold'}<strong>{t.text}</strong>{:else if t.kind === 'link'}<a href={t.href} target="_blank" rel="noopener noreferrer">{t.text}</a>{:else}{t.text}{/if}{/each}

<style>
  code { font-family: ui-monospace, Consolas, monospace; font-size: 0.92em; color: #ddd; }
  strong { color: #e8e8e8; }
  a { color: var(--accent); }
</style>
