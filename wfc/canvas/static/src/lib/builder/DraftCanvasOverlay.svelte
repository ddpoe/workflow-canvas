<!--
  What the canvas shows over the flow while it holds a draft: the Draft mode
  pill (top centre, with the link to the first draft's Draft run tab in the
  Registry) and the "Creating draft node…" / "Checking drafts…" popup that
  a folder check raises after 300 ms.
-->
<script lang="ts">
  import { nodes } from './stores.js';
  import { isDraftNode } from './nodeData.js';
  import { DRAFT_MODE_PILL, REVIEW_IN_REGISTRY, draftBusy } from './drafts.js';
  import { openDraftInRegistry } from '../shared/draftLinks.js';

  const firstDraft = $derived($nodes.find(n => isDraftNode(n.data))?.data ?? null);
</script>

{#if firstDraft}
  <div class="draft-modebar" data-testid="draft-mode-pill">
    <b>Draft mode</b> · {DRAFT_MODE_PILL}
    <button type="button" class="lnk" title="Opens {firstDraft.method}’s Draft run tab in the Registry"
            onclick={() => openDraftInRegistry(firstDraft.module, firstDraft.method)}>{REVIEW_IN_REGISTRY}</button>
  </div>
{/if}
{#if $draftBusy}
  <div class="draft-busy" role="status" data-testid="draft-busy">
    <span class="spinner" aria-hidden="true"></span>{$draftBusy}
  </div>
{/if}

<style>
  .draft-modebar {
    position: absolute;
    left: 50%;
    top: 10px;
    transform: translateX(-50%);
    z-index: 6;
    background: var(--bg-header);
    border: 1px solid var(--marker-dark);
    color: var(--text-primary);
    border-radius: 20px;
    padding: 5px 14px;
    font-size: 12px;
    white-space: nowrap;
  }
  .draft-modebar b { color: var(--marker-light); }
  .lnk {
    background: none;
    border: none;
    padding: 0;
    margin-left: 4px;
    color: var(--accent);
    font: inherit;
    cursor: pointer;
    white-space: nowrap;
  }
  .lnk:hover { text-decoration: underline; }
  .draft-busy {
    position: fixed;
    left: 50%;
    top: 45%;
    transform: translate(-50%, -50%);
    z-index: 1002;
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--bg-header);
    border: 1px solid var(--border);
    color: var(--text-primary);
    border-radius: 6px;
    padding: 12px 18px;
    font-size: 13px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  }
  .spinner {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 2px solid var(--dash-neutral);
    border-top-color: transparent;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
