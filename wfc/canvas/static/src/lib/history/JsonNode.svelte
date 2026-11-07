<script lang="ts">
  /**
   * One value of the JSON viewer's tree: a primitive, or an object or array
   * with a toggle that folds it to a one-line summary. Levels deeper than
   * `OPEN_DEPTH` start folded, so a large file renders only what is open.
   */
  import { untrack } from 'svelte';
  import JsonNode from './JsonNode.svelte';

  interface Props {
    value: unknown;
    /** The key this value sits under in its object; null in an array or at the root. */
    keyName?: string | null;
    /** Whether a comma follows (not the last entry of its parent). */
    comma?: boolean;
    depth?: number;
  }

  let { value, keyName = null, comma = false, depth = 0 }: Props = $props();

  const OPEN_DEPTH = 3;

  let isArray = $derived(Array.isArray(value));
  let isBranch = $derived(value !== null && typeof value === 'object');
  let entries = $derived(
    isBranch ? Object.entries(value as Record<string, unknown>) : [],
  );
  let open = $state(untrack(() => depth) < OPEN_DEPTH);
</script>

<div class="jn">
  {#if keyName !== null}<span class="k">"{keyName}"</span>: {/if}
  {#if isBranch}
    <button
      class="tog"
      aria-expanded={open}
      aria-label={open ? 'Fold' : 'Unfold'}
      onclick={() => { open = !open; }}
    >{open ? '▾' : '▸'}</button>{isArray ? '[' : '{'}
    {#if open}
      <div class="ind">
        {#each entries as [k, v], i (k)}
          <JsonNode value={v} keyName={isArray ? null : k} comma={i < entries.length - 1} depth={depth + 1} />
        {/each}
      </div>{isArray ? ']' : '}'}{comma ? ',' : ''}
    {:else}
      <span class="sum">{entries.length} {isArray ? 'items' : 'keys'} …</span>{isArray ? ']' : '}'}{comma ? ',' : ''}
    {/if}
  {:else if typeof value === 'string'}
    <span class="s">"{value}"</span>{comma ? ',' : ''}
  {:else if typeof value === 'number'}
    <span class="nm">{value}</span>{comma ? ',' : ''}
  {:else}
    <span class="b">{String(value)}</span>{comma ? ',' : ''}
  {/if}
</div>

<style>
  /* Dark-plus syntax colours, as shiki's Raw view: code colouring, no run state. */
  .k { color: #9CDCFE; }
  .s { color: #CE9178; }
  .nm { color: #B5CEA8; }
  .b { color: #569CD6; }
  .sum { color: var(--text-muted); font-style: italic; padding: 0 4px; }
  .tog {
    cursor: pointer;
    color: var(--text-muted);
    display: inline-block;
    width: 14px;
    padding: 0;
    border: none;
    background: none;
    font: inherit;
    user-select: none;
  }
  .tog:hover { color: #fff; }
  .ind { padding-left: 18px; border-left: 1px dotted #3a3a3a; margin-left: 5px; }
</style>
