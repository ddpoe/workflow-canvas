<script lang="ts">
  import { modules } from './stores.js';
  import type { ModuleDef, MethodDef } from './types.js';
  import PipelineVariablesPanel from './PipelineVariablesPanel.svelte';

  let searchQuery = $state('');
  let expandedMethods = $state<Record<string, boolean>>({});
  let detailMethod = $state<MethodDef | null>(null);

  let filteredModules = $derived(
    $modules.map(mod => ({
      ...mod,
      methods: mod.methods.filter(m =>
        m.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        mod.name.toLowerCase().includes(searchQuery.toLowerCase())
      ),
    })).filter(mod => mod.methods.length > 0)
  );

  function onDragStart(event: DragEvent, method: MethodDef) {
    if (!event.dataTransfer) return;
    event.dataTransfer.setData('application/json', JSON.stringify(method));
    event.dataTransfer.effectAllowed = 'move';
  }

  function toggleMethod(key: string) {
    expandedMethods[key] = !expandedMethods[key];
  }

  // Module accent colors by index
  const MODULE_COLORS = ['#1ABC9C', '#2ecc71', '#9b59b6', '#E9A847', '#4A90D9', '#E74C3C', '#3498db', '#e67e22'];

  async function fetchModules() {
    try {
      const resp = await fetch('/api/modules');
      if (resp.ok) {
        const raw = await resp.json();
        // API returns {module_name: {description, methods: {method_name: {...}}}}
        // Transform to ModuleDef[]
        const result: ModuleDef[] = Object.entries(raw).map(([modName, modData]: [string, any], i: number) => ({
          name: modName,
          description: modData.description,
          color: MODULE_COLORS[i % MODULE_COLORS.length],
          methods: Object.entries(modData.methods || {}).map(([methName, methData]: [string, any]) => ({
            name: methName,
            module: modName,
            version: methData.version,
            description: methData.description,
            inputs: Object.entries(methData.inputs || {}).map(([slotName, slotData]: [string, any]) => ({
              name: slotName,
              type: slotData.type || 'csv',
              multi: slotData.multiple || false,
              description: slotData.description ?? undefined,
            })),
            outputs: Object.entries(methData.outputs || {}).map(([slotName, slotData]: [string, any]) => ({
              name: slotName,
              type: slotData.type || 'csv',
              description: slotData.description ?? undefined,
            })),
            params: Object.entries(methData.params_schema || {}).map(([pName, pData]: [string, any]) => {
              const rawType = (pData.type ?? 'str') as string;
              const contractType = (
                ['str', 'int', 'float', 'bool', 'list', 'dict'].includes(rawType)
                  ? rawType
                  : 'unknown'
              );
              return {
                name: pName,
                type: rawType === 'bool' ? 'boolean' : (rawType === 'int' || rawType === 'float') ? 'number' : 'string',
                contractType,
                // Preserve native dict/list defaults; only coerce primitives to string
                // for legacy callers. Without this, `{}.toString()` becomes the literal
                // string "[object Object]" and every dict-defaulted param gets corrupted.
                default: pData.default !== undefined && pData.default !== null
                  ? (typeof pData.default === 'object' ? pData.default : pData.default.toString())
                  : undefined,
                description: pData.description ?? undefined,
                required: pData.required || false,
                constraints: pData.constraints || undefined,
              };
            }),
            color: MODULE_COLORS[i % MODULE_COLORS.length],
          })),
        }));
        modules.set(result);
      }
    } catch { /* dev mode — modules may not be available */ }
  }

  $effect(() => { fetchModules(); });
</script>

<div class="sidebar">
  <!-- System nodes section -->
  <div class="system-section">
    <div class="system-section-label">System Nodes</div>
    <div class="system-items">
      <div
        class="system-item"
        draggable="true"
        ondragstart={(e: DragEvent) => {
          if (!e.dataTransfer) return;
          e.dataTransfer.setData('application/json', JSON.stringify({ _systemNode: true, nodeType: 'input_selector', name: 'Sample Input' }));
          e.dataTransfer.effectAllowed = 'move';
        }}
      >
        <div class="system-icon-small">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#1ABC9C" stroke-width="2">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
        </div>
        <span>Input Selector</span>
      </div>
      <div
        class="system-item"
        draggable="true"
        ondragstart={(e: DragEvent) => {
          if (!e.dataTransfer) return;
          e.dataTransfer.setData('application/json', JSON.stringify({ _systemNode: true, nodeType: 'run_reference', name: 'Run Output' }));
          e.dataTransfer.effectAllowed = 'move';
        }}
      >
        <div class="system-icon-small">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#1ABC9C" stroke-width="2">
            <polyline points="16 3 21 3 21 8"/>
            <line x1="4" y1="20" x2="21" y2="3"/>
            <polyline points="21 16 21 21 16 21"/>
            <line x1="15" y1="15" x2="21" y2="21"/>
            <line x1="4" y1="4" x2="9" y2="9"/>
          </svg>
        </div>
        <span>Run Reference</span>
      </div>
    </div>
  </div>

  <div class="sidebar-divider"></div>

  <!-- Track 2 (ADR-017 / D-5, D-6): Pipeline Variables panel — sole
       creation surface for pipeline variables. Mounted inside the
       Builder tab as the architect-required "sibling of Samples"
       collapsible region (interpreted as a Sidebar section since the
       canvas has no separate Samples panel — see builder D-7). -->
  <PipelineVariablesPanel />

  <!-- Search -->
  <div class="search-bar">
    <input type="text" placeholder="Search methods..." bind:value={searchQuery} />
  </div>

  <!-- Module groups -->
  <div class="module-list">
    {#each filteredModules as mod}
      <div class="module-group">
        <div class="module-header">
          <div class="module-label">
            <div class="color-bar" style="background: {mod.color};"></div>
            <span class="module-name">{mod.name}</span>
          </div>
          <span class="module-count">{mod.methods.length}</span>
        </div>
        <div class="method-list">
          {#each mod.methods as method}
            <div
              class="method-item"
              class:expanded={expandedMethods[method.name]}
              draggable="true"
              ondragstart={(e: DragEvent) => onDragStart(e, method)}
            >
              <div class="method-header" onclick={() => toggleMethod(method.name)}>
                <div class="method-label">
                  <span class="expand-icon">{expandedMethods[method.name] ? '\u25BC' : '\u25B6'}</span>
                  <span class="method-name-text" title={method.name}>{method.name}</span>
                </div>
                <div class="method-meta">
                  {#if method.version}<span class="version">v{method.version}</span>{/if}
                  <button class="info-btn" onclick={(e: MouseEvent) => { e.stopPropagation(); detailMethod = method; }}>i</button>
                </div>
              </div>
              {#if expandedMethods[method.name]}
                <div class="method-slots">
                  {#if method.inputs.length > 0}
                    <div class="slot-section-label">Inputs</div>
                    <div class="slot-badges">
                      {#each method.inputs as inp}
                        <span class="slot-badge" style="color: #F39C12; border-color: rgba(243,156,18,0.2); background: rgba(243,156,18,0.12);">{inp.name}</span>
                        {#if inp.multi}<span class="multi-badge">MULTI</span>{/if}
                      {/each}
                    </div>
                  {/if}
                  {#if method.outputs.length > 0}
                    <div class="slot-section-label">Outputs</div>
                    <div class="slot-badges">
                      {#each method.outputs as out}
                        <span class="slot-badge" style="color: #F39C12; border-color: rgba(243,156,18,0.2); background: rgba(243,156,18,0.12);">{out.name}</span>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      </div>
    {/each}
  </div>

  <!-- Detail panel -->
  {#if detailMethod}
    <div class="detail-panel">
      <div class="detail-header">
        <button class="back-btn" onclick={() => { detailMethod = null; }}>Back to list</button>
        <span class="detail-name">{detailMethod.name}</span>
      </div>
      <div class="detail-desc">{detailMethod.description ?? 'No description available.'}</div>
      <div class="detail-params">
        <span class="detail-label">PARAMS: </span>
        {#each detailMethod.params as p}
          <span class="detail-param">{p.name}</span>{#if p.required}<span class="required">*</span>{/if}
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .sidebar {
    background: #252526;
    border-right: 1px solid #3e3e42;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    overflow: hidden;
    height: 100%;
  }
  .search-bar {
    padding: 10px 12px;
    border-bottom: 1px solid #3e3e42;
  }
  .search-bar input {
    width: 100%;
    padding: 6px 10px;
    background: #1e1e1e;
    border: 1px solid #3e3e42;
    border-radius: 4px;
    color: #ccc;
    font-size: 13px;
    outline: none;
  }
  .module-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 0;
  }
  .module-group { margin-bottom: 4px; }
  .module-header {
    padding: 8px 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .module-label {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .color-bar {
    width: 3px;
    height: 14px;
    border-radius: 2px;
  }
  .module-name {
    color: #ccc;
    font-size: 13px;
    font-weight: 500;
  }
  .module-count {
    color: #666;
    font-size: 11px;
  }
  .method-list { padding: 0 12px 0 20px; }
  .method-item {
    margin: 2px 0;
    background: #1e1e1e;
    border-radius: 4px;
    cursor: grab;
  }
  .method-item.expanded {
    border: 1px solid #3e3e42;
  }
  .method-header {
    padding: 6px 10px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  }
  .method-label {
    display: flex;
    align-items: center;
    gap: 6px;
    color: #ccc;
    font-size: 13px;
    /* flex: 1 + min-width: 0 let the label occupy remaining width and
       shrink below its intrinsic content size so long method names can
       truncate without pushing the info icon off-row. */
    flex: 1 1 auto;
    min-width: 0;
  }
  .method-name-text {
    /* Truncation target — requires min-width: 0 on the flex parent (above). */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .expand-icon {
    color: #555;
    font-size: 9px;
    /* Stays at natural size even when the name truncates. */
    flex-shrink: 0;
  }
  .method-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    /* Pin the version chip + info icon at the row's right edge — they
       never compress, so every row's icon stays in the same column. */
    flex-shrink: 0;
  }
  .version { color: #666; font-size: 10px; }
  .info-btn {
    color: #4A90D9;
    font-size: 11px;
    width: 16px;
    height: 16px;
    border: 1px solid #4A90D9;
    border-radius: 50%;
    background: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 600;
  }
  .method-slots {
    padding: 6px 10px;
    border-top: 1px solid #3e3e42;
    background: rgba(0,0,0,0.2);
  }
  .slot-section-label {
    font-size: 9px;
    color: #888;
    text-transform: uppercase;
    margin-bottom: 4px;
  }
  .slot-badges {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    margin-bottom: 5px;
  }
  .slot-badge {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 3px;
    border: 1px solid;
  }
  .multi-badge {
    font-size: 9px;
    color: #1e1e1e;
    background: #E9A847;
    padding: 2px 5px;
    border-radius: 3px;
    font-weight: 600;
  }
  .detail-panel {
    border-top: 1px solid #3e3e42;
    background: #2d2d30;
    flex-shrink: 0;
  }
  .detail-header {
    padding: 6px 10px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .back-btn {
    background: none; border: none;
    color: #888; font-size: 12px; cursor: pointer;
  }
  .detail-name { color: #666; font-size: 11px; }
  .detail-desc {
    padding: 6px 12px 10px;
    font-size: 12px;
    color: #aaa;
    line-height: 1.4;
  }
  .detail-params { padding: 4px 12px 8px; }
  .detail-label { font-size: 10px; color: #888; }
  .detail-param { font-size: 11px; color: #ccc; margin-right: 5px; }
  .required { font-size: 10px; color: #E74C3C; }
  /* System nodes section */
  .system-section {
    padding: 10px 12px;
  }
  .system-section-label {
    font-size: 10px;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
  }
  .system-items {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .system-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 10px;
    background: #1e1e1e;
    border-radius: 4px;
    border: 1px solid rgba(26, 188, 156, 0.2);
    cursor: grab;
    color: #ccc;
    font-size: 13px;
  }
  .system-item:hover {
    border-color: rgba(26, 188, 156, 0.5);
    background: rgba(26, 188, 156, 0.08);
  }
  .system-icon-small {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .sidebar-divider {
    height: 1px;
    background: #3e3e42;
    margin: 4px 10px;
  }
</style>
