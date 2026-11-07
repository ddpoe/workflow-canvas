<script lang="ts">
  /**
   * The pipeline-error banner over the canvas: what `setPipelineError`
   * holds, until the user dismisses it. A header line with items renders
   * them as a bulleted list, styled like the Inspector's "✗ The server
   * refused this step" box; a single message renders alone. Readiness
   * refusals keep their own icon (dirty repo, Docker, git); every other
   * error takes the refused box's ✗.
   */
  import type { PipelineError } from '../shared/types.js';

  let { error, onDismiss }: { error: PipelineError; onDismiss: () => void } = $props();

  let items = $derived(error.items ?? []);
  let icon = $derived(
    error.kind === 'dirty_repo' ? '⚠' :
    error.kind === 'not_runnable_docker' ? '🐳' :
    error.kind === 'not_runnable_git' ? '🔧' :
    '✗'
  );
</script>

<div class="pipeline-error-banner" role="alert" data-testid="pipeline-error-banner">
  <div class="pe-icon" aria-hidden="true">{icon}</div>
  <div class="pe-body">
    <div class="pe-message" class:pe-head={items.length > 0}>{error.message}</div>
    {#if items.length > 0}
      <ul class="pe-items">
        {#each items as item, i (i)}<li>{item}</li>{/each}
      </ul>
    {/if}
    {#if error.hint}
      <div class="pe-hint">{error.hint}</div>
    {/if}
  </div>
  <button
    type="button"
    class="pe-close"
    aria-label="Dismiss error"
    title="Dismiss"
    onclick={onDismiss}
  >×</button>
</div>

<style>
  /* Persistent (user-dismissed) rather than auto-fading like a toast: these
     are failures the user fixes before the next Run. The frame, header,
     list and hint take the Inspector refusal box's look, on an opaque
     panel so the canvas does not show through. */
  .pipeline-error-banner {
    position: fixed;
    top: 60px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: flex-start;
    gap: 10px;
    background: color-mix(in srgb, var(--status-failed) 8%, var(--bg-panel));
    border: 1px solid color-mix(in srgb, var(--status-failed) 30%, var(--bg-panel));
    border-left: 3px solid var(--status-failed);
    color: var(--text-primary);
    padding: 10px 12px 10px 12px;
    border-radius: 4px;
    font-size: 13px;
    line-height: 1.4;
    max-width: 720px;
    min-width: 360px;
    z-index: 1001;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  }
  .pe-icon {
    color: var(--status-failed-ink);
    font-weight: 600;
    font-size: 15px;
    line-height: 1.25;
    flex-shrink: 0;
  }
  .pe-body {
    flex: 1;
    min-width: 0;
  }
  .pe-message {
    white-space: pre-wrap;
    word-break: break-word;
    font-weight: 500;
  }
  .pe-head {
    color: var(--status-failed-ink);
    font-weight: 600;
    margin-bottom: 4px;
  }
  .pe-items {
    margin: 0;
    padding-left: 16px;
  }
  .pe-items li {
    margin: 2px 0;
    overflow-wrap: anywhere;
  }
  .pe-hint {
    margin-top: 6px;
    color: var(--text-secondary);
    font-size: 12px;
  }
  .pe-close {
    background: transparent;
    border: none;
    color: var(--text-primary);
    font-size: 20px;
    line-height: 1;
    cursor: pointer;
    padding: 0 4px;
    margin-left: 4px;
    flex-shrink: 0;
    opacity: 0.75;
  }
  .pe-close:hover {
    opacity: 1;
  }
</style>
