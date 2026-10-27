<script lang="ts">
  /**
   * The open method's version picker, in its row: a button naming the version shown
   * (`version | v2`, "latest" on the newest) that drops down every version,
   * newest first. Each entry is its label, "latest" on the newest, an ⓘ
   * whose hover gives its commit, identity and runs, and its own ⋯, which
   * opens the version's menu (`onVersionMenu`). Choosing an entry shows that
   * version. Two labels sharing one identity are two entries.
   */
  import LabelTag from '../shared/LabelTag.svelte';
  import { attachFloating } from './floating.js';
  import type { MethodVersionRow } from './api.js';

  let {
    versions,
    shown,
    onSelect,
    onVersionMenu,
  }: {
    versions: MethodVersionRow[];
    /** The identity of the version shown. */
    shown: string;
    onSelect: (identity: string) => void;
    onVersionMenu: (version: MethodVersionRow, anchor: HTMLElement) => void;
  } = $props();

  let button = $state<HTMLButtonElement | undefined>();
  let list = $state<HTMLDivElement | undefined>();
  let opened = $state(false);
  let pos = $state<{ left: number; top: number }>({ left: -9999, top: -9999 });

  // The label shown: the newest label carrying the shown identity.
  const current = $derived(
    [...versions].reverse().find(v => v.identity === shown) ?? versions[versions.length - 1]);
  const newestFirst = $derived([...versions].reverse());

  $effect(() => {
    if (!opened || !list || !button) return;
    return attachFloating(list, button, 'left', p => { pos = p; }, () => { opened = false; });
  });

  function choose(v: MethodVersionRow): void {
    opened = false;
    onSelect(v.identity);
  }

  function short(h: string): string {
    return h.length > 22 ? `${h.slice(0, 12)}…${h.slice(-7)}` : h;
  }
</script>

<!-- Sits in the method's row: neither a click nor a key on it reaches the
     row, which would close the method. -->
<button type="button" class="vbtn" data-testid="version-picker" bind:this={button}
        aria-haspopup="listbox" aria-expanded={opened}
        onclick={(e) => { e.stopPropagation(); opened = !opened; }}
        onkeydown={(e) => e.stopPropagation()}>
  {#if current}
    <LabelTag kind="version" label="version" value={`v${current.label}`} />
    {#if current.newest}<span class="latest">latest</span>{/if}
  {/if}
  <span class="dim">▾</span>
</button>

{#if opened}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="vlist" role="listbox" tabindex="-1" aria-label="Method version" data-testid="version-toggle"
       bind:this={list} style="left: {pos.left}px; top: {pos.top}px;"
       onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()}>
    <!-- Keyed by label: two labels may share one identity (a version
         re-registered after a revert). -->
    {#each newestFirst as v (v.label)}
      <div class="vitem" class:cur={v.identity === shown}>
        <button type="button" class="vpick" role="option" data-testid="version-option"
                aria-selected={v.identity === shown} aria-pressed={v.identity === shown}
                title={v.identity} onclick={() => choose(v)}>
          <span class="version-label mono">v{v.label}</span>
          {#if v.newest}<span class="latest">latest</span>{/if}
        </button>
        <span class="info" tabindex="0" role="note" data-testid="version-info"
              aria-label={`commit ${v.commit ?? 'not recorded'}, identity ${v.identity}, ${v.runCount} runs`}>ⓘ
          <span class="tipcard">
            <span class="tl"><span class="tk">commit</span><span class="mono">{v.commit ?? 'not recorded'}</span></span>
            <span class="tl"><span class="tk">identity</span><span class="mono">{short(v.identity)}</span></span>
            <span class="tl"><span class="tk">runs</span>{v.runCount}</span>
          </span>
        </span>
        <button type="button" class="kebab" data-testid="version-menu-button"
                aria-label={`v${v.label} actions`}
                onclick={(e) => { e.stopPropagation(); onVersionMenu(v, e.currentTarget as HTMLElement); }}>⋯</button>
      </div>
    {/each}
  </div>
{/if}

<style>
  .vbtn {
    display: inline-flex; align-items: center; gap: 6px; max-width: 100%;
    background: var(--bg-panel); border: 1px solid var(--border); color: var(--text-primary);
    padding: 1px 6px; border-radius: 4px; cursor: pointer; font-size: 11px;
  }
  .vbtn:hover { background: var(--bg-header); }
  .latest { font-size: 10px; color: #7ea893; }
  .dim { color: var(--text-secondary); }
  .mono { font-family: "JetBrains Mono", Consolas, monospace; font-size: 11px; }
  .vlist {
    position: fixed; z-index: 1100; width: 230px; max-width: calc(100vw - 16px);
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.55); padding: 4px;
  }
  .vitem {
    display: grid; grid-template-columns: minmax(0, 1fr) 22px 26px; align-items: center; gap: 6px;
    padding: 2px 4px 2px 2px; border-radius: 4px;
  }
  .vitem:hover { background: var(--bg-header); }
  .vitem.cur { background: color-mix(in srgb, #7ea893 14%, transparent); }
  .vpick {
    display: flex; align-items: center; gap: 6px; min-width: 0; padding: 4px 6px;
    background: none; border: none; color: inherit; font: inherit; cursor: pointer; text-align: left;
  }
  .version-label { color: #e6e6e6; font-weight: 600; }
  .info { position: relative; color: var(--text-secondary); cursor: help; text-align: center; }
  .info:hover, .info:focus { color: #e6e6e6; }
  .tipcard {
    display: none; position: absolute; left: calc(100% + 10px); top: -8px; z-index: 1110;
    background: #0f0f10; border: 1px solid var(--border); border-radius: 5px; padding: 8px 10px;
    white-space: nowrap; box-shadow: 0 6px 18px rgba(0, 0, 0, 0.6); text-align: left; color: var(--text-primary);
  }
  .info:hover .tipcard, .info:focus .tipcard { display: block; }
  .tl { display: block; line-height: 1.7; }
  .tk { color: var(--text-secondary); display: inline-block; width: 64px; }
  .kebab {
    background: none; border: 1px solid transparent; color: var(--text-secondary); border-radius: 3px;
    width: 26px; height: 22px; cursor: pointer; font-size: 15px; line-height: 1;
  }
  .kebab:hover { border-color: var(--border); color: #e6e6e6; background: var(--bg-input); }
</style>
