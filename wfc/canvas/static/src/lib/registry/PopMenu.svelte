<script lang="ts" module>
  /** One action of a ⋯ menu. A disabled item shows its hint and runs nothing. */
  export type MenuItem = {
    label: string;
    icon?: string;
    hint?: string;
    testid: string;
    disabled?: boolean;
    run: () => void;
    /** Draw a separator above this item. */
    separated?: boolean;
  };
</script>

<script lang="ts">
  /**
   * A ⋯ menu floating beside the element that opened it (`floating.ts`).
   * It closes on a choice, Escape, or a click anywhere outside it; a click
   * on the anchor is left to the anchor, which toggles the menu itself.
   */
  import { attachFloating } from './floating.js';
  let {
    anchor,
    items,
    title = '',
    align = 'right',
    testid = 'pop-menu',
    onClose,
  }: {
    anchor: HTMLElement;
    items: MenuItem[];
    title?: string;
    align?: 'right' | 'left' | 'side';
    testid?: string;
    onClose: () => void;
  } = $props();

  let el = $state<HTMLDivElement | undefined>();
  let pos = $state<{ left: number; top: number }>({ left: -9999, top: -9999 });

  $effect(() => {
    if (!el) return;
    return attachFloating(el, anchor, align, p => { pos = p; }, onClose);
  });

  function choose(e: MouseEvent, item: MenuItem): void {
    e.stopPropagation();
    if (item.disabled) return;
    onClose();
    item.run();
  }
</script>

<div class="pop" role="menu" data-testid={testid} bind:this={el}
     style="left: {pos.left}px; top: {pos.top}px;">
  {#if title}<div class="ptitle">{title}</div>{/if}
  {#each items as item (item.testid)}
    {#if item.separated}<div class="msep"></div>{/if}
    <button type="button" class="mi" role="menuitem" data-testid={item.testid}
            disabled={item.disabled} onclick={(e) => choose(e, item)}>
      <span class="ml"><span class="ic">{item.icon ?? ''}</span>{item.label}</span>
      {#if item.hint}<span class="hint mono">{item.hint}</span>{/if}
    </button>
  {/each}
</div>

<style>
  .pop {
    position: fixed; z-index: 1100; min-width: 200px; max-width: calc(100vw - 16px);
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.55); padding: 4px;
  }
  .ptitle {
    padding: 4px 10px 6px; color: var(--text-secondary); font-size: 10px;
    text-transform: uppercase; letter-spacing: 0.6px; overflow-wrap: anywhere;
  }
  .mi {
    display: flex; justify-content: space-between; align-items: center; gap: 16px;
    width: 100%; padding: 6px 10px; border: none; border-radius: 4px; background: none;
    color: var(--text-primary); font: inherit; font-size: 12px; text-align: left; cursor: pointer;
  }
  .mi:hover:not(:disabled) { background: var(--bg-header); }
  .mi:disabled { color: var(--text-muted); cursor: default; }
  .ml { white-space: nowrap; }
  .ic { display: inline-block; width: 18px; color: var(--text-secondary); }
  .hint { color: var(--text-secondary); font-size: 10.5px; white-space: nowrap; }
  .mono { font-family: "JetBrains Mono", Consolas, monospace; }
  .msep { height: 1px; background: var(--border); margin: 4px 2px; }
</style>
