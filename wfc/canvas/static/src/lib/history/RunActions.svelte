<script lang="ts">
  /**
   * The run detail panel's run actions: the header strip (copy run id, share
   * link, export JSON, close) and the action row (favorite, rename, archive),
   * together with the confirm dialogs and the copied-toast they drive.
   *
   * `run` is bound because the favorite and archive actions patch it
   * optimistically; load failures are reported back through `onError`.
   *
   * The dialogs and the toast are absolutely positioned against
   * `.detail-panel`, which stays in the parent, so they render here but still
   * cover the whole panel.
   */
  import {
    selectRun,
    setFavoriteOptimistic,
    renameRunOptimistic,
    deleteRunOptimistic,
    setArchivedOptimistic,
  } from './historyStore.js';
  import { fetchRun } from './historyApi.js';
  import type { WfcRun, Artifact } from './historyApi.js';

  interface Props {
    run: WfcRun;
    artifacts: Artifact[];
    onError: (message: string) => void;
  }

  let { run = $bindable(), artifacts, onError }: Props = $props();

  let renaming = $state(false);
  let renameValue = $state('');
  let confirmDelete = $state(false);
  let confirmArchive = $state(false);
  let copiedToast = $state<string | null>(null);

  let isArchived = $derived(!!run?.archivedAt);

  function flashToast(msg: string): void {
    copiedToast = msg;
    setTimeout(() => { copiedToast = null; }, 1400);
  }

  async function copy(text: string, label = 'Copied'): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      flashToast(label);
    } catch {
      flashToast('Copy failed');
    }
  }

  function shareLink(): void {
    if (!run) return;
    const url = `${window.location.origin}${window.location.pathname}?run=${encodeURIComponent(run.id)}`;
    copy(url, 'Link copied');
  }

  function exportJson(): void {
    if (!run) return;
    const payload = {
      metadata: {
        id: run.id,
        nid: run.nid,
        method: run.method,
        module: run.module,
        status: run.status,
        timestamp: run.timestamp,
        duration: run.duration,
        dataSource: run.dataSource,
        parents: run.parents,
      },
      inputs: run.inputs,
      metrics: run.metrics,
      artifacts,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${run.id}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function startRename(): void {
    if (!run) return;
    // Seed with current nid if it's a user label (not an auto v-version).
    renameValue = run.nid && !/^v\d+$/.test(run.nid) ? run.nid : '';
    renaming = true;
  }

  function commitRename(): void {
    if (!run) { renaming = false; return; }
    const next = renameValue.trim();
    renaming = false;
    const current = run.nid && !/^v\d+$/.test(run.nid) ? run.nid : '';
    if (next !== current) {
      setFavoriteOrRename(() => renameRunOptimistic(run!.id, next));
    }
  }

  function cancelRename(): void {
    renaming = false;
  }

  async function toggleFavoriteClick(): Promise<void> {
    if (!run) return;
    const next = !run.favorite;
    // Optimistic UI: the store patches `runs[]`, so update the local `run`
    // pointer too so this panel reflects the change immediately.
    run = { ...run, favorite: next };
    try {
      await setFavoriteOptimistic(run.id, next);
    } catch (err) {
      if (run) run = { ...run, favorite: !next };
    }
  }

  async function setFavoriteOrRename(op: () => Promise<void>): Promise<void> {
    try {
      await op();
      // reflect the updated value from the store
      if (run) {
        const fresh = await fetchRun(run.id).catch(() => null);
        if (fresh) run = fresh;
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }

  async function performDelete(): Promise<void> {
    if (!run) return;
    const id = run.id;
    confirmDelete = false;
    try {
      await deleteRunOptimistic(id);
      selectRun(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }

  async function toggleArchive(): Promise<void> {
    if (!run) return;
    const next = !run.archivedAt;
    if (next) {
      // require confirm for archive; unarchive is a one-click reverse.
      confirmArchive = true;
      return;
    }
    try {
      run = { ...run, archivedAt: null };
      await setArchivedOptimistic(run.id, false);
      flashToast('Unarchived');
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }

  async function performArchive(): Promise<void> {
    if (!run) return;
    confirmArchive = false;
    const id = run.id;
    try {
      run = { ...run, archivedAt: Date.now() };
      await setArchivedOptimistic(id, true);
      flashToast('Archived');
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }
</script>

<!-- Header strip -->
<div class="detail-header">
  <div class="crumb">
    <span class="crumb-text">History</span>
    <span class="crumb-sep">›</span>
    <button class="crumb-id" onclick={() => copy(run!.id, 'Run ID copied')} title="Copy run ID">{run.id}</button>
  </div>
  <div class="header-actions">
    <button class="icon-btn" onclick={shareLink} title="Copy share link">⎘</button>
    <button class="icon-btn" onclick={exportJson} title="Export JSON">⤓</button>
    <button class="icon-btn" onclick={() => selectRun(null)} title="Close">×</button>
  </div>
</div>

<!-- Action row -->
<div class="action-row">
  <button
    class="fav-btn"
    class:active={run.favorite}
    onclick={toggleFavoriteClick}
    title={run.favorite ? 'Unfavorite' : 'Favorite'}
  >{run.favorite ? '★' : '☆'}</button>

  {#if renaming}
    <!-- svelte-ignore a11y_autofocus -->
    <input
      class="rename-input"
      bind:value={renameValue}
      onblur={commitRename}
      onkeydown={(e) => {
        if (e.key === 'Enter') commitRename();
        else if (e.key === 'Escape') cancelRename();
      }}
      autofocus
    />
  {:else}
    <button class="rename-btn" onclick={startRename} title="Rename run">
      <span class="g">✎</span><span>Rename</span>
    </button>
  {/if}

  {#if isArchived}
    <button
      class="archive-btn unarchive"
      onclick={toggleArchive}
      title="Restore run to active list"
    >
      <span class="g">↺</span><span>Unarchive</span>
    </button>
  {:else}
    <button
      class="archive-btn"
      onclick={toggleArchive}
      title="Archive run (soft-delete; can be restored)"
    >
      <span class="g">🗄</span><span>Archive</span>
    </button>
  {/if}
</div>

{#if confirmDelete}
  <div class="confirm-overlay" role="dialog" aria-modal="true">
    <div class="confirm-card">
      <div class="confirm-title">Delete run?</div>
      <div class="confirm-body">
        Delete run <span class="confirm-id">{run.id}</span>? This cannot be undone.
      </div>
      <div class="confirm-actions">
        <button class="footer-btn" onclick={() => { confirmDelete = false; }}>Cancel</button>
        <button class="footer-btn danger" onclick={performDelete}>Delete</button>
      </div>
    </div>
  </div>
{/if}

{#if confirmArchive}
  <div class="confirm-overlay" role="dialog" aria-modal="true">
    <div class="confirm-card">
      <div class="confirm-title">Archive run?</div>
      <div class="confirm-body">
        Archive run <span class="confirm-id">{run.id}</span>? It will be hidden
        from the default history view but can be restored at any time.
      </div>
      <div class="confirm-actions">
        <button class="footer-btn" onclick={() => { confirmArchive = false; }}>Cancel</button>
        <button class="footer-btn primary" onclick={performArchive}>Archive</button>
      </div>
    </div>
  </div>
{/if}

{#if copiedToast}
  <div class="toast" aria-live="polite">{copiedToast}</div>
{/if}

<style>
  /* Header strip */
  .detail-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    background: var(--bg-header, #2d2d30);
    border-bottom: 1px solid var(--border, #3e3e42);
    flex-shrink: 0;
  }
  .crumb {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--text-secondary, #888);
    min-width: 0;
  }
  .crumb-text, .crumb-sep { color: var(--text-muted, #666); }
  .crumb-id {
    font-family: 'Consolas', 'Courier New', monospace;
    color: var(--text-primary, #ccc);
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 180px;
  }
  .crumb-id:hover { color: var(--accent, #4A90D9); }
  .header-actions { display: flex; gap: 2px; }
  .icon-btn {
    width: 26px;
    height: 24px;
    background: none;
    border: none;
    border-radius: 3px;
    color: var(--text-muted, #666);
    font-size: 13px;
    cursor: pointer;
    line-height: 1;
  }
  .icon-btn:hover { color: var(--text-primary, #ccc); }

  /* Action row */
  .action-row {
    display: flex;
    align-items: stretch;
    gap: 6px;
    padding: 10px 12px 0;
    flex-shrink: 0;
  }
  .fav-btn {
    width: 36px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-secondary, #888);
    border-radius: 3px;
    cursor: pointer;
    font-size: 18px;
    font-family: inherit;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .fav-btn.active {
    color: #E9A847;
    border-color: #E9A847;
    background: rgba(233, 168, 71, 0.15);
  }
  .rename-btn, .rename-input, .archive-btn {
    border-radius: 3px;
    font-size: 11px;
    font-family: inherit;
  }
  .rename-btn {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px 10px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-primary, #ccc);
    font-weight: 500;
    cursor: pointer;
  }
  .rename-btn:hover { border-color: var(--accent, #4A90D9); }
  .rename-input {
    flex: 1;
    padding: 0 10px;
    background: var(--bg-input, #1e1e1e);
    border: 1px solid var(--accent, #4A90D9);
    color: var(--text-primary, #ccc);
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    outline: none;
  }
  .archive-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px 10px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-secondary, #888);
    font-weight: 500;
    cursor: pointer;
  }
  .archive-btn:hover {
    border-color: var(--accent, #4A90D9);
    color: var(--accent, #4A90D9);
    background: rgba(74, 144, 217, 0.10);
  }
  .archive-btn.unarchive {
    color: var(--accent, #4A90D9);
    border-color: var(--accent, #4A90D9);
    background: rgba(74, 144, 217, 0.10);
  }
  .archive-btn.unarchive:hover {
    background: rgba(74, 144, 217, 0.18);
  }
  .g { font-size: 12px; }

  /* Buttons shared with the panel footer, as used by the confirm dialogs. */
  .footer-btn {
    flex: 1;
    padding: 7px 10px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-primary, #ccc);
    border-radius: 3px;
    font-size: 11px;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
  }
  .footer-btn:hover:not(:disabled) { border-color: var(--accent, #4A90D9); }
  .footer-btn.primary {
    background: var(--accent, #4A90D9);
    border-color: var(--accent, #4A90D9);
    color: #fff;
    font-weight: 600;
  }
  .footer-btn.danger {
    border-color: var(--color-failed, #E74C3C);
    color: var(--color-failed, #E74C3C);
    background: rgba(231, 76, 60, 0.08);
  }

  /* Confirm dialog */
  .confirm-overlay {
    position: absolute;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 10;
  }
  .confirm-card {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 3px;
    padding: 14px 16px;
    width: 100%;
  }
  .confirm-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary, #ccc);
    margin-bottom: 8px;
  }
  .confirm-body {
    font-size: 11px;
    color: var(--text-secondary, #888);
    margin-bottom: 12px;
    line-height: 1.4;
  }
  .confirm-id {
    font-family: 'Consolas', 'Courier New', monospace;
    color: var(--text-primary, #ccc);
  }
  .confirm-actions {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
  }
  .confirm-actions .footer-btn { flex: 0 0 auto; min-width: 80px; }

  /* Toast */
  .toast {
    position: absolute;
    bottom: 70px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--bg-input, #1e1e1e);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-primary, #ccc);
    padding: 6px 12px;
    border-radius: 3px;
    font-size: 11px;
    z-index: 11;
    pointer-events: none;
  }
</style>
