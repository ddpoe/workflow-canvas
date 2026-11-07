<script lang="ts">
  /**
   * One draft in the Methods tab's Drafts group, laid out exactly like a
   * method row: its method and env as labelled tags, then Update and ⋯.
   * The only draft marker is the plain grey version chip, `version | DRAFT
   * | version` for a draft of a registered method's next version or
   * `version | DRAFT | method` for a new method. A draft whose Registration
   * check fails reads ⚠ INVALID beside its name, its failed lines on hover.
   *
   * Clicking the row opens or closes the draft; Update and ⋯ never do.
   * Update is on every draft row, the only place it lives.
   */
  import LabelTag from '../shared/LabelTag.svelte';
  import type { DraftEntry } from './api.js';

  let {
    draft,
    expanded,
    menuOpen = false,
    updating = false,
    onToggle,
    onMenu,
    onUpdate,
  }: {
    draft: DraftEntry;
    expanded: boolean;
    menuOpen?: boolean;
    /** True while an Update is in flight. */
    updating?: boolean;
    onToggle: () => void;
    onMenu: (anchor: HTMLElement) => void;
    onUpdate: () => void;
  } = $props();

  const env = $derived(draft.env ?? '—');
  const invalidTip = $derived(
    `${(draft.reasons ?? []).join('\n')}${(draft.reasons ?? []).length ? '\n' : ''}`
    + "This draft can't run or register until it is fixed. Details in Registration check.");
  const updateTip = $derived(draft.stale
    ? 'method.yaml changed since it was read: Update reads it again (nothing is registered).'
    : 'Read method.yaml again (nothing is registered).');
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="mrow" class:open={expanded} role="button" tabindex="0" aria-expanded={expanded}
     data-testid="draft-row" data-draft-key={`${draft.module}::${draft.method}`}
     onclick={onToggle}
     onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}>
  <span class="chev">{expanded ? '▼' : '▶'}</span>
  <span class="cell name">
    <LabelTag kind="method" label="method" value={draft.method} title={draft.method} />
    {#if !draft.valid}
      <span class="inv" data-testid="draft-invalid" title={invalidTip}>⚠ INVALID</span>
    {/if}
  </span>
  <span class="cell">
    <span class="vch" data-testid="draft-version-chip"
          title={draft.kind === 'method' ? 'A new method: registering it adds v1'
                 : `The next version: registering it adds v${draft.registersAs}`}
    ><span class="k">version</span><span class="v">DRAFT</span><span class="l">{draft.kind}</span></span>
  </span>
  <span class="cell">
    <LabelTag kind="env" label="env" value={env} title={env} />
  </span>
  <span class="cell">
    <button type="button" class="update" data-testid="draft-update" disabled={updating}
            title={updateTip}
            onclick={(e) => { e.stopPropagation(); onUpdate(); }}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}>↻ {updating ? 'Updating…' : 'Update'}</button>
  </span>
  <span class="cell end">
    <button type="button" class="kebab" class:on={menuOpen} data-testid="draft-menu-button"
            aria-label={`${draft.method} draft actions`} aria-haspopup="menu" aria-expanded={menuOpen}
            onclick={(e) => { e.stopPropagation(); onMenu(e.currentTarget as HTMLElement); }}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}>⋯</button>
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
  .name { gap: 8px; }
  .end { justify-content: flex-end; }
  .inv {
    flex-shrink: 0; color: var(--status-failed-ink); font-weight: 700; font-size: 10px;
    letter-spacing: 0.05em; cursor: help; white-space: nowrap;
  }
  /* The plain grey version chip: the Registry's only draft marker. */
  .vch {
    display: inline-flex; border-radius: 3px; border: 1px solid var(--marker-dark);
    font-family: Consolas, monospace; font-size: 10px; line-height: 15px; white-space: nowrap;
  }
  .vch .k {
    padding: 0 4px; font-size: 9px; color: #b8b8b8; background: #3a3a3a; border-radius: 2px 0 0 2px;
  }
  .vch .v {
    padding: 0 7px; font-weight: 700; letter-spacing: 0.04em; color: var(--marker-light);
    background: var(--bg-canvas);
  }
  .vch .l {
    padding: 0 6px; color: var(--text-secondary); background: var(--bg-header); border-radius: 0 2px 2px 0;
  }
  .update {
    background: var(--accent); border: none; color: white; font-size: 11.5px; padding: 3px 9px;
    border-radius: 3px; cursor: pointer; white-space: nowrap;
  }
  .update:disabled { opacity: 0.6; cursor: default; }
  .kebab {
    background: none; border: 1px solid transparent; color: var(--text-secondary); border-radius: 3px;
    width: 26px; height: 22px; cursor: pointer; font-size: 15px; line-height: 1;
  }
  .kebab:hover, .kebab.on { border-color: var(--border); color: #e6e6e6; background: var(--bg-input); }
</style>
