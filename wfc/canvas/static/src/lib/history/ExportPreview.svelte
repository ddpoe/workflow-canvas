<script lang="ts">
  import { selectedRunIds } from './historyStore.js';
  import { previewArtifacts, exportArtifacts } from './historyApi.js';
  import type { ExportPreviewResponse } from './historyApi.js';
  import { get } from 'svelte/store';
  import { isDraftRef } from './historyDrafts.js';

  interface Props {
    onClose: () => void;
  }

  let { onClose }: Props = $props();

  let preview = $state<ExportPreviewResponse | null>(null);
  let loadError = $state<string | null>(null);
  let loading = $state(true);
  let downloading = $state(false);

  /**
   * The recorded runs the artifact export asks for. A draft step is kept
   * but not recorded, so the recorded-run export cannot read it: its id is
   * left out and the dialog says so (its outputs download from its run
   * panel's Download zip).
   */
  function recordedIds(): string[] {
    return [...get(selectedRunIds)].filter(id => !isDraftRef(id));
  }
  const draftCount = [...get(selectedRunIds)].filter(isDraftRef).length;
  const draftNote = draftCount === 0 ? null
    : `${draftCount} draft step${draftCount === 1 ? '' : 's'} left out: a draft step is not recorded. Download its outputs with Download zip in its run panel.`;

  $effect(() => {
    const ids = recordedIds();
    if (ids.length === 0) {
      loadError = draftNote ?? 'No runs selected.';
      loading = false;
      return;
    }
    loading = true;
    loadError = null;
    previewArtifacts(ids)
      .then(p => {
        preview = p;
        loading = false;
      })
      .catch(err => {
        loadError = err instanceof Error ? err.message : String(err);
        loading = false;
      });
  });

  function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function doExport() {
    const ids = recordedIds();
    downloading = true;
    try {
      const { blob, filename } = await exportArtifacts(ids);
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(blobUrl);
      onClose();
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err);
    } finally {
      downloading = false;
    }
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="modal-overlay" onclick={onClose} onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="modal-content" onclick={(e: MouseEvent) => e.stopPropagation()}>
    <div class="modal-header">
      <span class="modal-title">Export Artifacts</span>
      <button class="modal-close" onclick={onClose}>&times;</button>
    </div>

    {#if loading}
      <div class="modal-body center">Loading preview...</div>
    {:else if loadError}
      <div class="modal-body center error">{loadError}</div>
    {:else if preview}
      <div class="modal-body">
        {#if draftNote}
          <div class="draft-note" data-testid="export-draft-note">{draftNote}</div>
        {/if}
        <div class="summary-grid">
          <div class="summary-item">
            <span class="summary-value">{preview.run_count}</span>
            <span class="summary-label">Runs</span>
          </div>
          <div class="summary-item">
            <span class="summary-value">{preview.total_count}</span>
            <span class="summary-label">Files</span>
          </div>
          <div class="summary-item">
            <span class="summary-value">{formatBytes(preview.total_size_bytes)}</span>
            <span class="summary-label">Total Size</span>
          </div>
          <div class="summary-item">
            <span class="summary-value">{preview.method_count}</span>
            <span class="summary-label">Methods</span>
          </div>
        </div>

        {#if preview.by_type.length > 0}
          <div class="type-breakdown">
            <div class="breakdown-title">By file type</div>
            <table class="breakdown-table">
              <thead>
                <tr><th>Type</th><th>Count</th><th>Size</th></tr>
              </thead>
              <tbody>
                {#each preview.by_type as entry}
                  <tr>
                    <td class="type-ext">.{entry.ext}</td>
                    <td class="type-count">{entry.count}</td>
                    <td class="type-size">{formatBytes(entry.size_bytes)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}

        {#if preview.total_count === 0}
          <div class="no-artifacts">No artifacts found for the selected runs.</div>
        {/if}
      </div>

      <div class="modal-footer">
        <button class="cancel-btn" onclick={onClose}>Cancel</button>
        <button
          class="download-btn"
          onclick={doExport}
          disabled={downloading || preview.total_count === 0}
        >
          {downloading ? 'Downloading...' : `Download Zip (${formatBytes(preview.total_size_bytes)})`}
        </button>
      </div>
    {/if}
  </div>
</div>

<style>
  .draft-note {
    margin-bottom: 12px;
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-secondary);
    font-size: 11.5px;
  }
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .modal-content {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    width: 400px;
    max-width: 90vw;
    max-height: 80vh;
    display: flex;
    flex-direction: column;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  }
  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
  }
  .modal-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
  }
  .modal-close {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 20px;
    cursor: pointer;
    line-height: 1;
  }
  .modal-close:hover { color: var(--text-primary); }
  .modal-body {
    padding: 16px;
    overflow-y: auto;
    flex: 1;
  }
  .modal-body.center {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 80px;
    font-size: 12px;
    color: var(--text-secondary);
  }
  .modal-body.error { color: var(--status-failed-ink); }

  /* Summary grid */
  .summary-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 16px;
  }
  .summary-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 10px;
    background: var(--bg-input);
    border-radius: 6px;
  }
  .summary-value {
    font-size: 18px;
    font-weight: 600;
    color: var(--text-primary);
  }
  .summary-label {
    font-size: 10px;
    color: var(--text-muted);
    text-transform: uppercase;
    margin-top: 2px;
  }

  /* Type breakdown */
  .type-breakdown {
    margin-top: 4px;
  }
  .breakdown-title {
    font-size: 11px;
    color: var(--text-muted);
    text-transform: uppercase;
    margin-bottom: 6px;
  }
  .breakdown-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
  }
  .breakdown-table th {
    text-align: left;
    color: var(--text-muted);
    font-weight: 500;
    padding: 3px 6px;
    border-bottom: 1px solid var(--border);
  }
  .breakdown-table td {
    padding: 3px 6px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.3);
    color: var(--text-primary);
  }
  .type-ext { font-family: 'Consolas', monospace; }
  .type-count { text-align: center; }
  .type-size { text-align: right; color: var(--text-secondary); }

  .no-artifacts {
    text-align: center;
    padding: 16px;
    color: var(--text-muted);
    font-size: 12px;
  }

  /* Footer */
  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--border);
  }
  .cancel-btn {
    background: var(--bg-header);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 6px 14px;
    color: var(--text-primary);
    font-size: 12px;
    cursor: pointer;
  }
  .cancel-btn:hover { border-color: var(--accent); }
  .download-btn {
    background: var(--accent);
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    color: white;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }
  .download-btn:hover { filter: brightness(1.1); }
  .download-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
