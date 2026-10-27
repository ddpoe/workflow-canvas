<script lang="ts">
  /**
   * A method version's details, floating over the row (✕, Escape or a click
   * outside closes it): when it was registered, its commit ("not recorded"
   * for a version this database did not register), its identity, the runs
   * on it and on every version, its env with the env fingerprint and image,
   * and its code tree. Each hash has a copy icon.
   */
  import LabelTag from '../shared/LabelTag.svelte';
  import CopyButton from './CopyButton.svelte';
  import { attachFloating } from './floating.js';
  import type { MethodRow, MethodVersionRow } from './api.js';

  let {
    method,
    version,
    anchor,
    onClose,
  }: {
    method: MethodRow;
    version: MethodVersionRow;
    anchor: HTMLElement;
    onClose: () => void;
  } = $props();

  let el = $state<HTMLDivElement | undefined>();
  let pos = $state<{ left: number; top: number }>({ left: -9999, top: -9999 });

  $effect(() => {
    if (!el) return;
    return attachFloating(el, anchor, 'right', p => { pos = p; }, onClose);
  });

  const allRuns = $derived((method.versions ?? []).reduce((n, v) => n + (v.runCount ?? 0), 0));

  /** A registration time as `YYYY-MM-DD HH:MM UTC`, `—` when unknown. */
  function when(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
  }

  /** A long hash as its first 12 and last 7 characters. */
  function short(h: string): string {
    return h.length > 22 ? `${h.slice(0, 12)}…${h.slice(-7)}` : h;
  }

  /** An image ref with its digest cut to 8 hex characters (the copy icon copies all of it). */
  function shortImage(ref: string): string {
    return ref.replace(/@sha256:([0-9a-f]{8})[0-9a-f]+/, '@sha256:$1…');
  }
</script>

<div class="dcard" role="dialog" aria-label="Version details" data-testid="version-details"
     bind:this={el} style="left: {pos.left}px; top: {pos.top}px;">
  <div class="hd">
    <LabelTag kind="version" label="version" value={`v${version.label}`} />
    {#if version.newest}<span class="latest">latest</span>{/if}
    <span class="dim mono name">{method.name}</span>
    <button type="button" class="x" aria-label="Close" data-testid="version-details-close"
            onclick={onClose}>✕</button>
  </div>
  <div class="dl">
    <span class="k">Registered</span>
    <span class="v" data-testid="details-registered">{when(version.registered_at)}</span>
    <span class="k">Commit</span>
    <span class="v mono" data-testid="details-commit" title={version.commit ?? undefined}>
      {#if version.commit}{version.commit.slice(0, 10)}<CopyButton value={version.commit} label="Copy commit" testid="copy-commit" />
      {:else}<span class="dim">not recorded</span>{/if}
    </span>
    <span class="k">Version identity</span>
    <span class="v mono" data-testid="details-identity" title={version.identity}>{short(version.identity)}<CopyButton value={version.identity} label="Copy version identity" testid="copy-identity" /></span>
    <span class="k">Runs</span>
    <span class="v" data-testid="details-runs">{version.runCount} on this version <span class="dim">({allRuns} on all)</span></span>
  </div>
  <div class="dsec">Environment</div>
  <div class="dl">
    <span class="k">Env</span>
    <span class="v"><LabelTag kind="env" label="env" value={version.env} /></span>
    <span class="k">Env fingerprint</span>
    <span class="v mono" data-testid="details-env-fingerprint" title={version.envFingerprint}>{short(version.envFingerprint)}<CopyButton value={version.envFingerprint} label="Copy env fingerprint" testid="copy-env-fingerprint" /></span>
    <span class="k">Image</span>
    <span class="v mono" data-testid="details-image" title={version.image}>{shortImage(version.image)}<CopyButton value={version.image} label="Copy image" testid="copy-image" /></span>
  </div>
  <div class="dsec">Code</div>
  <div class="dl">
    <span class="k">Code tree</span>
    <span class="v mono" data-testid="details-code-tree" title={version.codeTree}>{version.codeTree.slice(0, 12)}<CopyButton value={version.codeTree} label="Copy code tree" testid="copy-code-tree" /></span>
  </div>
</div>

<style>
  .dcard {
    position: fixed; z-index: 1100; width: 400px; max-width: calc(100vw - 16px);
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.55); font-size: 12px; color: var(--text-primary);
  }
  .hd { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid var(--border); min-width: 0; }
  .name { min-width: 0; overflow-wrap: anywhere; }
  .latest { font-size: 10px; color: #7ea893; }
  .x {
    margin-left: auto; flex-shrink: 0; background: none; border: none; color: var(--text-secondary);
    cursor: pointer; padding: 0 4px; font-size: 12px;
  }
  .x:hover { color: #e6e6e6; }
  .dl { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 5px 10px; padding: 10px 12px; }
  .k { color: var(--text-secondary); }
  .v { color: #e6e6e6; overflow-wrap: anywhere; min-width: 0; }
  .dsec {
    padding: 6px 12px 2px; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.6px;
    color: var(--text-secondary); font-weight: 600; border-top: 1px solid rgba(62, 62, 66, 0.5);
  }
  .dim { color: var(--text-secondary); }
  .mono { font-family: "JetBrains Mono", Consolas, monospace; font-size: 11px; }
</style>
