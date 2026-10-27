<script lang="ts">
  /**
   * One method in the Registry's list: its method, version and env as
   * labelled tags, then ⋯ at the right. Clicking the row opens or closes
   * the method; ⋯ opens the method's menu and never the row.
   *
   * A closed row reads its newest version. An open row reads the version
   * shown, its version tag becoming the version picker, and its env tag
   * that version's env.
   */
  import LabelTag from '../shared/LabelTag.svelte';
  import VersionPicker from './VersionPicker.svelte';
  import type { MethodRow, MethodVersionRow } from './api.js';

  let {
    method,
    expanded,
    shown = '',
    menuOpen = false,
    onToggle,
    onMenu,
    onSelect = () => {},
    onVersionMenu = () => {},
  }: {
    method: MethodRow;
    expanded: boolean;
    /** The identity of the version the open row shows. */
    shown?: string;
    menuOpen?: boolean;
    onToggle: () => void;
    onMenu: (anchor: HTMLElement) => void;
    onSelect?: (identity: string) => void;
    onVersionMenu?: (version: MethodVersionRow, anchor: HTMLElement) => void;
  } = $props();

  const versions = $derived(method.versions ?? []);
  const newest = $derived(versions.find(v => v.newest) ?? versions.at(-1));
  // The newest label carrying the shown identity, else the newest.
  const current = $derived(
    expanded ? ([...versions].reverse().find(v => v.identity === shown) ?? newest) : newest);
  const env = $derived(current?.env ?? method.env);
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="mrow" class:open={expanded} role="button" tabindex="0" aria-expanded={expanded}
     data-testid="method-row" data-method-key={`${method.module}::${method.name}`}
     onclick={onToggle}
     onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}>
  <span class="chev">{expanded ? '▼' : '▶'}</span>
  <span class="cell"><LabelTag kind="method" label="method" value={method.name} title={method.name} /></span>
  <span class="cell">
    {#if expanded && versions.length > 0}
      <VersionPicker {versions} {shown} {onSelect} {onVersionMenu} />
    {:else if newest}
      <LabelTag kind="version" label="version" value={`v${newest.label}`} />
    {/if}
  </span>
  <span class="cell">
    {#if expanded}
      <LabelTag kind="env" label="env" value={env} title={env} data-testid="version-env" />
    {:else}
      <LabelTag kind="env" label="env" value={env} title={env} />
    {/if}
  </span>
  <span class="cell end">
    <button type="button" class="kebab" class:on={menuOpen} data-testid="method-menu-button"
            aria-label={`${method.name} actions`} aria-haspopup="menu" aria-expanded={menuOpen}
            onclick={(e) => { e.stopPropagation(); onMenu(e.currentTarget as HTMLElement); }}
            onkeydown={(e) => e.stopPropagation()}>⋯</button>
  </span>
</div>

<style>
  .mrow {
    display: grid; grid-template-columns: var(--reg-grid); column-gap: 10px; align-items: center;
    padding: 6px 10px 6px 28px; border-top: 1px solid rgba(62, 62, 66, 0.5); cursor: pointer;
    min-height: 35px;
  }
  .mrow:hover, .mrow.open { background: #262628; }
  .mrow:focus-visible { outline: 1px solid var(--accent); outline-offset: -1px; }
  .chev { color: var(--text-secondary); font-size: 9px; text-align: center; user-select: none; }
  .cell { min-width: 0; display: flex; align-items: center; }
  .end { justify-content: flex-end; }
  .kebab {
    background: none; border: 1px solid transparent; color: var(--text-secondary); border-radius: 3px;
    width: 26px; height: 22px; cursor: pointer; font-size: 15px; line-height: 1;
  }
  .kebab:hover, .kebab.on { border-color: var(--border); color: #e6e6e6; background: var(--bg-input); }
</style>
