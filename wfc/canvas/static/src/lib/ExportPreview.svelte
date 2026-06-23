<script lang="ts">
  import { selectedRunIds } from './historyStore.js';
  import { previewArtifacts, exportArtifactsUrl } from './historyApi.js';
  import type { ExportPreviewResponse } from './historyApi.js';
  import { get } from 'svelte/store';

  interface Props {
    onClose: () => void;
  }

  let { onClose }: Props = $props();

  let preview = $state<ExportPreviewResponse | null>(null);
  let loadError = $state<string | null>(null);
  let loading = $state(true);
  let downloading = $state(false);

  $effect(() => {
    const ids = [...get(selectedRunIds)];
    if (ids.length === 0) {
      loadError = 'No runs selected.';
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
    const ids = [...get(selectedRunIds)];
    const { url, body } = exportArtifactsUrl(ids);
    downloading = true;
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => resp.statusText);
        throw new Error(`Export failed: ${text}`);
      }
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      const disposition = resp.headers.get('Content-Disposition');
      const filename = disposition?.match(/filename="?([^"]+)"?/)?.[1] || 'wfc_export.zip';
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
    background: var(--bg-panel, #252526);
    border: 1px solid var(--border, #3e3e42);
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
    border-bottom: 1px solid var(--border, #3e3e42);
  }
  .modal-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary, #ccc);
  }
  .modal-close {
    background: none;
    border: none;
    color: var(--text-muted, #666);
    font-size: 20px;
    cursor: pointer;
    line-height: 1;
  }
  .modal-close:hover { color: var(--text-primary, #ccc); }
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
    color: var(--text-secondary, #888);
  }
  .modal-body.error { color: var(--color-failed, #E74C3C); }

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
    background: var(--bg-input, #1e1e1e);
    border-radius: 6px;
  }
  .summary-value {
    font-size: 18px;
    font-weight: 600;
    color: var(--text-primary, #ccc);
  }
  .summary-label {
    font-size: 10px;
    color: var(--text-muted, #666);
    text-transform: uppercase;
    margin-top: 2px;
  }

  /* Type breakdown */
  .type-breakdown {
    margin-top: 4px;
  }
  .breakdown-title {
    font-size: 11px;
    color: var(--text-muted, #666);
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
    color: var(--text-muted, #666);
    font-weight: 500;
    padding: 3px 6px;
    border-bottom: 1px solid var(--border, #3e3e42);
  }
  .breakdown-table td {
    padding: 3px 6px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.3);
    color: var(--text-primary, #ccc);
  }
  .type-ext { font-family: 'Consolas', monospace; }
  .type-count { text-align: center; }
  .type-size { text-align: right; color: var(--text-secondary, #888); }

  .no-artifacts {
    text-align: center;
    padding: 16px;
    color: var(--text-muted, #666);
    font-size: 12px;
  }

  /* Footer */
  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--border, #3e3e42);
  }
  .cancel-btn {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 4px;
    padding: 6px 14px;
    color: var(--text-primary, #ccc);
    font-size: 12px;
    cursor: pointer;
  }
  .cancel-btn:hover { border-color: var(--accent, #4A90D9); }
  .download-btn {
    background: var(--accent, #4A90D9);
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
