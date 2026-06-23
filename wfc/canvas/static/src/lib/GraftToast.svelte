<script lang="ts">
  /**
   * Bottom-right toast for Action 3 (Reference in Canvas) success.
   * Auto-dismiss at ~4s. Optional [Jump to node] action.
   */

  interface Props {
    message: string;
    detail?: string;
    onJump?: () => void;
    onDismiss: () => void;
  }

  let { message, detail, onJump, onDismiss }: Props = $props();

  let visible = $state(true);

  $effect(() => {
    const t = setTimeout(() => {
      visible = false;
      onDismiss();
    }, 4000);
    return () => clearTimeout(t);
  });
</script>

{#if visible}
  <div class="graft-toast" role="status" aria-live="polite">
    <span class="check">&#10003;</span>
    <div class="msg">
      <div class="head">{message}</div>
      {#if detail}<div class="detail">{detail}</div>{/if}
    </div>
    {#if onJump}
      <button class="jump" onclick={onJump}>Jump to node</button>
    {/if}
  </div>
{/if}

<style>
  .graft-toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--bg-header, #2d2d30);
    border: 1px solid var(--color-completed, #50C878);
    border-radius: 4px;
    padding: 10px 14px;
    color: var(--text-primary, #ccc);
    font-size: 12px;
    z-index: 100;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  }
  .check { color: var(--color-completed, #50C878); font-weight: 700; }
  .msg { display: flex; flex-direction: column; gap: 2px; }
  .head { font-weight: 600; }
  .detail { font-size: 10.5px; color: var(--text-secondary, #888); font-family: 'Consolas', monospace; }
  .jump {
    margin-left: 8px;
    background: none;
    border: 1px solid var(--accent, #4A90D9);
    color: var(--accent, #4A90D9);
    border-radius: 3px;
    font-size: 11px;
    padding: 4px 10px;
    cursor: pointer;
    font-family: inherit;
  }
  .jump:hover { background: rgba(74, 144, 217, 0.10); }
</style>
