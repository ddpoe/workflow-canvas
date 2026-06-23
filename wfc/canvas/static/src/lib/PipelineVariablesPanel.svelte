<script lang="ts">
  /**
   * Pipeline Variables panel (Track 2, ADR-017 / D-5, D-6).
   *
   * Sole creation surface for pipeline variables (D-6). Mounted inside
   * the Builder tab as a collapsible region in the Sidebar (interpreted
   * pragmatically — see builder.decisions D-7). Reads/writes the
   * `pipelineVariables` store. Bind/unbind on a row is owned by the
   * per-row paramEditorActor (D-4) — not by this panel.
   *
   * UI:
   *   - Collapsible header with row count.
   *   - + Add variable button → inline-add row (name, type, value).
   *   - Existing rows: name | type | value | # bindings | × delete.
   *   - Delete confirms if any rows are currently bound to the variable.
   */
  import { pipelineVariables, createVariable, deleteVariable } from './stores.js';
  import { paramEditorAggregator } from './machines/root.js';

  let collapsed = $state(false);
  let showAdd = $state(false);
  let newName = $state('');
  let newType = $state<'str' | 'int' | 'float' | 'bool' | 'list' | 'dict'>('str');
  let newValueText = $state('');

  /**
   * Count actor rows currently bound to a given variable name.
   * Used to show "# bindings" per row and to decide whether deleting
   * needs a confirm prompt.
   */
  function bindingCount(varName: string): number {
    // The aggregator's children context is `Record<string, ChildActor>`
    // (see paramEditorAggregator.machine.ts), NOT a Map. The previous
    // cast to `Map<string, { actor: ... }>` was wrong twice over: it
    // crashed at runtime under jsdom (`children.entries is not a
    // function`) and shaped the entry as `{ actor }` though the
    // aggregator stores actors directly. Caught by incarnation 4's
    // PipelineVariablesPanel render test.
    const children = paramEditorAggregator.getSnapshot().context.children as
      | Record<string, { getSnapshot: () => { context: { boundVariable?: string | null } } }>
      | undefined;
    if (!children) return 0;
    let n = 0;
    for (const actor of Object.values(children)) {
      const ctx = actor.getSnapshot().context;
      if (ctx.boundVariable === varName) n += 1;
    }
    return n;
  }

  function parseValue(type: string, raw: string): unknown {
    if (type === 'int') return parseInt(raw, 10);
    if (type === 'float') return parseFloat(raw);
    if (type === 'bool') return raw === 'true' || raw === '1';
    if (type === 'list' || type === 'dict') {
      try { return JSON.parse(raw); } catch { return raw; }
    }
    return raw;
  }

  function displayValue(v: unknown): string {
    if (v === undefined || v === null) return '';
    if (typeof v === 'object') {
      try { return JSON.stringify(v); } catch { return String(v); }
    }
    return String(v);
  }

  function startAdd(): void {
    showAdd = true;
    newName = '';
    newType = 'str';
    newValueText = '';
  }

  function confirmAdd(): void {
    const name = newName.trim();
    if (!name) return;
    createVariable(name, newType, parseValue(newType, newValueText));
    showAdd = false;
  }

  function cancelAdd(): void {
    showAdd = false;
  }

  function handleDelete(name: string): void {
    const n = bindingCount(name);
    if (n > 0) {
      const ok = confirm(`Delete variable "${name}"? ${n} row(s) are currently bound to it.`);
      if (!ok) return;
    }
    deleteVariable(name);
  }

  let varRows = $derived(Object.entries($pipelineVariables));
</script>

<div class="pv-panel" data-testid="pipeline-variables-panel">
  <button type="button" class="pv-header" onclick={() => { collapsed = !collapsed; }}
    data-testid="pv-toggle-collapse">
    <span class="pv-caret">{collapsed ? '▸' : '▾'}</span>
    <span class="pv-title">Pipeline Variables</span>
    <span class="pv-count">{varRows.length}</span>
  </button>

  {#if !collapsed}
    <div class="pv-body">
      <button type="button" class="pv-add-btn" onclick={startAdd}
        data-testid="pv-add-variable">+ Add variable</button>

      {#if showAdd}
        <div class="pv-add-row" data-testid="pv-add-row">
          <input class="pv-input" placeholder="name" bind:value={newName}
            data-testid="pv-new-name" />
          <select class="pv-input" bind:value={newType} data-testid="pv-new-type">
            <option value="str">str</option>
            <option value="int">int</option>
            <option value="float">float</option>
            <option value="bool">bool</option>
            <option value="list">list</option>
            <option value="dict">dict</option>
          </select>
          {#if newType === 'list' || newType === 'dict'}
            <textarea class="pv-input pv-json" placeholder={newType === 'list' ? '[]' : '{}'}
              bind:value={newValueText} data-testid="pv-new-value"></textarea>
          {:else}
            <input class="pv-input" placeholder="value"
              type={newType === 'int' || newType === 'float' ? 'number' : 'text'}
              bind:value={newValueText} data-testid="pv-new-value" />
          {/if}
          <div class="pv-add-actions">
            <button type="button" class="pv-confirm" onclick={confirmAdd}
              data-testid="pv-confirm-add">✓</button>
            <button type="button" class="pv-cancel" onclick={cancelAdd}
              data-testid="pv-cancel-add">×</button>
          </div>
        </div>
      {/if}

      {#if varRows.length === 0 && !showAdd}
        <div class="pv-empty">No pipeline variables yet.</div>
      {/if}

      {#each varRows as [name, v] (name)}
        <div class="pv-row" data-testid="pv-row" data-variable-name={name}>
          <span class="pv-name">{name}</span>
          <span class="pv-type">{v.type}</span>
          <span class="pv-value" title={displayValue(v.value)}>{displayValue(v.value)}</span>
          <span class="pv-bindings" title="rows bound">{bindingCount(name)}</span>
          <button type="button" class="pv-delete"
            onclick={() => handleDelete(name)}
            data-testid="pv-delete">×</button>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .pv-panel {
    border-bottom: 1px solid #3e3e42;
    background: #252526;
  }
  .pv-header {
    width: 100%;
    background: none;
    border: none;
    color: #ccc;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    font-family: inherit;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    text-align: left;
  }
  .pv-header:hover { background: #2a2a2c; }
  .pv-caret { font-size: 10px; color: #666; }
  .pv-title { flex: 1; font-size: 11px; color: #888; }
  .pv-count { color: #4A90D9; font-size: 11px; }
  .pv-body { padding: 8px 12px 10px; display: flex; flex-direction: column; gap: 4px; }
  .pv-add-btn {
    background: transparent;
    border: 1px dashed #3e3e42;
    color: #888;
    padding: 4px 8px;
    border-radius: 3px;
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
  }
  .pv-add-btn:hover { color: #ccc; border-color: #555; }
  .pv-add-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 6px;
    background: #1e1e1e;
    border: 1px solid #4A90D9;
    border-radius: 3px;
  }
  .pv-input {
    background: #1b1b1d;
    border: 1px solid #2a2a2d;
    color: #ddd;
    padding: 4px 6px;
    border-radius: 2px;
    font-size: 11px;
    font-family: Consolas, monospace;
    outline: none;
  }
  .pv-json { resize: vertical; min-height: 40px; }
  .pv-add-actions { display: flex; gap: 4px; justify-content: flex-end; }
  .pv-confirm, .pv-cancel {
    background: transparent;
    border: none;
    color: #888;
    padding: 2px 6px;
    cursor: pointer;
    font-size: 12px;
  }
  .pv-confirm:hover { color: #6aa84f; }
  .pv-cancel:hover { color: #E74C3C; }
  .pv-empty { color: #555; font-size: 11px; font-style: italic; padding: 4px 0; }
  .pv-row {
    display: grid;
    grid-template-columns: 1fr auto auto auto auto;
    gap: 6px;
    align-items: center;
    padding: 4px 6px;
    background: #1e1e1e;
    border-radius: 2px;
    font-size: 11px;
    font-family: Consolas, monospace;
  }
  .pv-name { color: #ddd; overflow: hidden; text-overflow: ellipsis; }
  .pv-type { color: #888; font-size: 10px; }
  .pv-value { color: #6aa84f; max-width: 80px; overflow: hidden; text-overflow: ellipsis; }
  .pv-bindings { color: #4A90D9; font-size: 10px; padding: 0 4px; }
  .pv-delete {
    background: transparent;
    border: none;
    color: #555;
    cursor: pointer;
    font-size: 13px;
    padding: 0 4px;
  }
  .pv-delete:hover { color: #E74C3C; }
</style>
