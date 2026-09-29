<script lang="ts">
  /**
   * The lock summary: one confirmation view before Lock All, and before a
   * Run while any row is still unlocked. Lists every row still unlocked and
   * every parameter that runs its method's declared default (node,
   * parameter, value). After a confirm, a row that could not be committed
   * is named here and nothing proceeds. State and actions live in
   * `lockFlow.ts`; App.svelte hosts this component.
   */
  import { lockSummary, confirmLockSummary, cancelLockSummary } from './lockFlow.js';
</script>

{#if $lockSummary}
  {@const s = $lockSummary}
  <div class="confirm-overlay" role="dialog" aria-modal="true" data-testid="lock-summary">
    <div class="confirm-card">
      <div class="confirm-title">
        {s.purpose === 'run' ? 'Lock the unlocked rows, then run' : 'Lock All'}
      </div>

      {#if s.failed.length > 0}
        <div class="section failed" data-testid="lock-summary-failed">
          <div class="section-title">Could not be locked ({s.failed.length})</div>
          <ul>{#each s.failed as r}<li>{r}</li>{/each}</ul>
          <div class="hint">Select the node in the Inspector panel: each row shows why its value was refused.</div>
        </div>
      {/if}

      <div class="section" data-testid="lock-summary-unlocked">
        <div class="section-title">Still unlocked ({s.unlocked.length})</div>
        {#if s.unlocked.length === 0}
          <div class="empty">Every row is locked.</div>
        {:else}
          <ul>{#each s.unlocked as r}<li>{r}</li>{/each}</ul>
        {/if}
      </div>

      <div class="section" data-testid="lock-summary-defaults">
        <div class="section-title">Runs the method's declared default ({s.defaults.length})</div>
        {#if s.defaults.length === 0}
          <div class="empty">Every parameter has a value set.</div>
        {:else}
          <table>
            <thead><tr><th>Node</th><th>Parameter</th><th>Value</th></tr></thead>
            <tbody>
              {#each s.defaults as d}
                <tr data-testid="lock-summary-default"><td>{d.node}</td><td>{d.param}</td><td class="val">{d.value}</td></tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <div class="confirm-actions">
        <button class="footer-btn" data-testid="lock-summary-cancel" onclick={cancelLockSummary}>Cancel</button>
        <button class="footer-btn primary" data-testid="lock-summary-confirm" disabled={s.busy}
          onclick={() => { void confirmLockSummary(); }}>
          {s.busy ? 'Locking…' : s.purpose === 'run' ? 'Lock and run' : 'Lock'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .confirm-overlay {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55);
    display: flex; align-items: center; justify-content: center;
    padding: 16px; z-index: 100;
  }
  .confirm-card {
    background: var(--bg-header, #2d2d30); border: 1px solid var(--border, #3e3e42);
    border-radius: 4px; padding: 14px 16px; width: 100%; max-width: 520px;
    max-height: 80vh; overflow: auto; color: var(--text-primary, #ccc);
  }
  .confirm-title { font-size: 13px; font-weight: 600; margin-bottom: 10px; }
  .section { margin-bottom: 12px; font-size: 12px; }
  .section-title { color: #9aa0a6; font-weight: 600; margin-bottom: 4px; }
  .section ul { margin: 0; padding-left: 18px; }
  .failed { color: #E74C3C; }
  .hint { color: #c07070; font-size: 11px; margin-top: 4px; }
  .empty { color: #666; font-style: italic; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 2px 8px 2px 0; border-bottom: 1px solid #333; }
  th { color: #888; font-weight: 600; font-size: 11px; }
  .val { font-family: Consolas, monospace; color: #F39C12; }
  .confirm-actions { display: flex; gap: 8px; justify-content: flex-end; }
  .footer-btn {
    padding: 6px 14px; background: var(--bg-header, #2d2d30);
    border: 1px solid var(--border, #3e3e42); color: var(--text-primary, #ccc);
    border-radius: 3px; font-size: 12px; font-family: inherit; cursor: pointer;
  }
  .footer-btn:hover { border-color: var(--accent, #4A90D9); }
  .footer-btn.primary {
    background: var(--accent, #4A90D9); border-color: var(--accent, #4A90D9);
    color: #fff; font-weight: 600;
  }
  .footer-btn:disabled { opacity: 0.6; cursor: wait; }
</style>
