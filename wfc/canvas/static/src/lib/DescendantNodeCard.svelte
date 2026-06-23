<script lang="ts">
  import type { WfcRun } from './historyApi.js';
  import { selectRun } from './historyStore.js';
  import { getModuleColor, hslToRgba, statusColor, formatParams } from './historyUtils.js';

  interface Props {
    run: WfcRun;
    isRoot?: boolean;
  }

  let { run, isRoot = false }: Props = $props();

  /**
   * Compute border color based on run status.
   */
  let borderColor = $derived.by(() => {
    switch (run.status) {
      case 'running': return '#E9A847';
      case 'failed': return '#E74C3C';
      case 'cancelled': return '#7f8ea3';
      case 'success': return getModuleColor(run.module);
      default: return '#666';
    }
  });

  /**
   * Compute background tint based on run status and root variant.
   */
  let bgColor = $derived.by(() => {
    const tint = isRoot ? 0.14 : 0.10;
    switch (run.status) {
      case 'running': return isRoot ? 'rgba(233, 168, 71, 0.14)' : 'rgba(233, 168, 71, 0.12)';
      case 'failed': return 'rgba(231, 76, 60, 0.10)';
      case 'cancelled': return 'rgba(127, 142, 163, 0.10)';
      case 'success': return hslToRgba(getModuleColor(run.module), tint);
      default: return 'transparent';
    }
  });

  let boxShadow = $derived(run.status === 'running' ? '0 0 0 1px #E9A847' : 'none');

  let params = $derived(formatParams(run.inputs, 2));

  let errorMessage = $derived(run.error_message ?? undefined);
  let errorFirstLine = $derived(errorMessage ? errorMessage.split('\n')[0] : '');

  function handleClick() {
    selectRun(run.id);
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleClick();
    }
  }
</script>

<div
  class="desc-node"
  class:root={isRoot}
  class:running={run.status === 'running'}
  class:failed={run.status === 'failed'}
  style="border-left-color: {borderColor}; background: {bgColor}; box-shadow: {boxShadow};"
  role="button"
  tabindex="0"
  onclick={handleClick}
  onkeydown={handleKeydown}
>
  <div class="hdr">
    <span class="run-name">{run.runName || run.id.slice(0, 8)}</span>
    <span class="status-pill" style="color: {statusColor(run.status)}">
      {'\u25CF'} {run.status === 'success' ? 'COMPLETED' : run.status.toUpperCase()}
    </span>
  </div>
  <div class="method">{run.method} v{run.version}</div>
  {#if params}
    <div class="params">{params}</div>
  {/if}
  <div class="run-id">{run.id.length > 8 ? run.id.slice(0, 8) : run.id}</div>
  {#if run.status === 'failed' && errorFirstLine}
    <div class="err-inline">{errorFirstLine}</div>
  {/if}
</div>

<style>
  .desc-node {
    padding: 13px 16px;
    border-radius: 6px;
    border-left: 4px solid var(--accent, #4A90D9);
    position: relative;
    cursor: pointer;
    transition: transform 0.08s;
  }
  .desc-node:hover {
    transform: translateY(-1px);
  }
  .desc-node.root {
    border-left-width: 6px;
    padding: 14px 16px;
    display: inline-block;
    margin-bottom: 14px;
    min-width: 360px;
  }
  .hdr {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 3px;
  }
  .run-name {
    color: #fff;
    font-weight: 600;
    font-size: 14px;
  }
  .status-pill {
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }
  .method {
    color: var(--text-secondary, #888);
    font-size: 13px;
  }
  .params {
    color: var(--text-secondary, #888);
    font-size: 13px;
    margin-top: 2px;
  }
  .run-id {
    color: var(--accent, #4A90D9);
    font-size: 12px;
    margin-top: 4px;
  }
  .err-inline {
    margin-top: 8px;
    padding: 6px 10px;
    background: rgba(231, 76, 60, 0.10);
    border-radius: 3px;
    color: var(--color-failed, #E74C3C);
    font-size: 12px;
  }
</style>
