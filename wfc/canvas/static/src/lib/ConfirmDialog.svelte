<script lang="ts">
  /**
   * Generic confirm/block dialog used by the load-in-canvas reload
   * actions. Variant prop selects between:
   *   - 'dirty-confirm': Cancel + Discard-and-load (replace flow)
   *   - 'running-block': Cancel-only (running pipeline blocks reload)
   *
   * Copy strings match SPEC §"Confirm-on-dirty dialog" / §"Block dialog"
   * verbatim.
   */

  type Variant = 'dirty-confirm' | 'running-block';

  interface Props {
    variant: Variant;
    currentName?: string;
    targetName?: string;
    runningPipelineLabel?: string;
    onCancel: () => void;
    onConfirm?: () => void;
  }

  let {
    variant,
    currentName = 'your canvas',
    targetName = 'this pipeline',
    runningPipelineLabel = '',
    onCancel,
    onConfirm,
  }: Props = $props();
</script>

<div class="confirm-overlay" role="dialog" aria-modal="true">
  <div class="confirm-card">
    {#if variant === 'dirty-confirm'}
      <div class="confirm-title">Discard unsaved canvas changes?</div>
      <div class="confirm-body">
        You have unsaved edits to <span class="confirm-id">{currentName}</span>.<br/>
        Open pipeline in Canvas will replace the canvas with
        <span class="confirm-id">{targetName}</span>.
      </div>
      <div class="confirm-actions">
        <button class="footer-btn" onclick={onCancel}>Cancel</button>
        <button class="footer-btn primary" onclick={onConfirm}>Discard and load</button>
      </div>
    {:else}
      <div class="confirm-title">Wait for running pipeline to finish</div>
      <div class="confirm-body">
        Your current canvas is running <span class="confirm-id">{runningPipelineLabel}</span>.<br/>
        You can't open this pipeline until it finishes.<br/>
        <span class="hint">Stop-and-reload coming in a future cycle.</span>
      </div>
      <div class="confirm-actions">
        <button class="footer-btn primary" onclick={onCancel}>OK</button>
      </div>
    {/if}
  </div>
</div>

<style>
  .confirm-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 100;
  }
  .confirm-card {
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    border-radius: 4px;
    padding: 14px 16px;
    width: 100%;
    max-width: 440px;
    color: var(--text-primary, #ccc);
  }
  .confirm-title {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .confirm-body {
    font-size: 12px;
    color: var(--text-secondary, #888);
    margin-bottom: 14px;
    line-height: 1.5;
  }
  .confirm-id {
    font-family: 'Consolas', 'Courier New', monospace;
    color: var(--text-primary, #ccc);
  }
  .hint {
    color: var(--color-running, #E9A847);
    font-size: 11px;
  }
  .confirm-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
  .footer-btn {
    padding: 6px 14px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42);
    color: var(--text-primary, #ccc);
    border-radius: 3px;
    font-size: 12px;
    font-family: inherit;
    cursor: pointer;
  }
  .footer-btn:hover { border-color: var(--accent, #4A90D9); }
  .footer-btn.primary {
    background: var(--accent, #4A90D9);
    border-color: var(--accent, #4A90D9);
    color: #fff;
    font-weight: 600;
  }
</style>
