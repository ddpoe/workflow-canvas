<script lang="ts">
  import { modules } from './stores.js';
  import { refreshPalette } from './api.js';
  import type { MethodDef } from '../shared/types.js';
  import type { DraftNodePayload } from './nodeData.js';
  import PipelineVariablesPanel from './PipelineVariablesPanel.svelte';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import { displaySlotType, slotColor } from '../shared/slotColor.js';

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

  function matches(name: string, mod: string): boolean {
    const q = searchQuery.toLowerCase();
    return name.toLowerCase().includes(q) || mod.toLowerCase().includes(q);
  }

  // The Drafts group: every module's drafts, grouped by module, after the
  // module groups. The search filters them like methods.
  let draftGroups = $derived(
    $modules.map(mod => ({
      name: mod.name,
      drafts: (mod.drafts ?? []).filter(m => m.draft && matches(m.name, mod.name)),
    })).filter(g => g.drafts.length > 0)
  );
  let draftCount = $derived(draftGroups.reduce((n, g) => n + g.drafts.length, 0));
  let draftsOpen = $state(true);

  function onDraftDragStart(event: DragEvent, method: MethodDef) {
    if (!event.dataTransfer) return;
    const payload: DraftNodePayload = { _draft: true, method };
    event.dataTransfer.setData('application/json', JSON.stringify(payload));
    event.dataTransfer.effectAllowed = 'move';
  }

  /** The reasons an invalid draft can't run, for the ⚠ INVALID hover. */
  function invalidTitle(method: MethodDef): string {
    return (method.draft?.reasons ?? []).join('\n') || 'This draft’s Registration check fails.';
  }

  function onDragStart(event: DragEvent, method: MethodDef) {
    if (!event.dataTransfer) return;
    event.dataTransfer.setData('application/json', JSON.stringify(method));
    event.dataTransfer.effectAllowed = 'move';
  }

  function toggleMethod(key: string) {
    expandedMethods[key] = !expandedMethods[key];
  }

  /** The newest version's label, for the palette chip. */
  function newestLabel(method: MethodDef): number | undefined {
    return method.versions?.find(v => v.newest)?.label;
  }

  // Dev mode may have no modules route: refreshPalette keeps what it had.
  $effect(() => { refreshPalette(); });
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
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
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
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
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

  <!-- Pipeline Variables panel — sole creation surface for pipeline
       variables, mounted as a collapsible Sidebar section of the
       Builder tab. -->
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
            <div class="color-bar" style="background: {moduleColor(mod.name)};"></div>
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
                  {#if newestLabel(method) !== undefined}<span class="version" title="newest version">v{newestLabel(method)}</span>{/if}
                  <button class="info-btn" onclick={(e: MouseEvent) => { e.stopPropagation(); detailMethod = method; }}>i</button>
                </div>
              </div>
              {#if expandedMethods[method.name]}
                <div class="method-slots">
                  {#if method.inputs.length > 0}
                    <div class="slot-section-label">Inputs</div>
                    <div class="slot-badges">
                      {#each method.inputs as inp}
                        <span class="slot-badge" data-testid="palette-slot" style="--chip: {slotColor(inp.type)};" title={displaySlotType(inp.type)}>{inp.name}</span>
                        {#if inp.multi}<span class="multi-badge">MULTI</span>{/if}
                      {/each}
                    </div>
                  {/if}
                  {#if method.outputs.length > 0}
                    <div class="slot-section-label">Outputs</div>
                    <div class="slot-badges">
                      {#each method.outputs as out}
                        <span class="slot-badge" data-testid="palette-slot" style="--chip: {slotColor(out.type)};" title={displaySlotType(out.type)}>{out.name}</span>
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
    {#if draftCount > 0}
      <div class="module-group drafts-group" data-testid="palette-drafts">
        <button type="button" class="module-header drafts-header" onclick={() => { draftsOpen = !draftsOpen; }}>
          <div class="module-label">
            <div class="color-bar" style="background: var(--dash-neutral);"></div>
            <span class="module-name">{draftsOpen ? '▾' : '▸'} Drafts</span>
          </div>
          <span class="module-count">{draftCount}</span>
        </button>
        {#if draftsOpen}
          {#each draftGroups as group}
            <div class="draft-sub"><i style="background: {moduleColor(group.name)};"></i>{group.name}</div>
            <div class="method-list">
              {#each group.drafts as method}
                <div
                  class="method-item draft-item"
                  draggable="true"
                  data-testid="palette-draft"
                  data-method={method.name}
                  ondragstart={(e: DragEvent) => onDraftDragStart(e, method)}
                >
                  <div class="method-header">
                    <div class="method-label">
                      <span class="expand-icon">{'▶'}</span>
                      <span class="method-name-text" title={method.name}>{method.name}</span>
                      {#if method.draft && !method.draft.valid}
                        <span class="inv" title={invalidTitle(method)}>{'⚠'} INVALID</span>
                      {/if}
                    </div>
                    <div class="method-meta">
                      <button class="info-btn" onclick={(e: MouseEvent) => { e.stopPropagation(); detailMethod = method; }}>i</button>
                    </div>
                  </div>
                  <div class="draft-chip-row">
                    <span class="vch"><span class="v">DRAFT</span><span class="l">{method.draft?.kind ?? 'version'}</span></span>
                  </div>
                </div>
              {/each}
            </div>
          {/each}
        {/if}
      </div>
    {/if}
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
    background: var(--bg-panel);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    overflow: hidden;
    height: 100%;
  }
  .search-bar {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }
  .search-bar input {
    width: 100%;
    padding: 6px 10px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-primary);
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
    color: var(--text-primary);
    font-size: 13px;
    font-weight: 500;
  }
  .module-count {
    color: var(--text-muted);
    font-size: 11px;
  }
  .method-list { padding: 0 12px 0 20px; }
  .method-item {
    margin: 2px 0;
    background: var(--bg-input);
    border-radius: 4px;
    cursor: grab;
  }
  .method-item.expanded {
    border: 1px solid var(--border);
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
    color: var(--text-primary);
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
  .version { color: var(--text-muted); font-size: 10px; }
  .info-btn {
    color: var(--accent);
    font-size: 11px;
    width: 16px;
    height: 16px;
    border: 1px solid var(--accent);
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
    border-top: 1px solid var(--border);
    background: rgba(0,0,0,0.2);
  }
  .slot-section-label {
    font-size: 9px;
    color: var(--text-secondary);
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
    /* A slot type's palette colour (shared/slotColor.ts), as the node chips use it. */
    color: color-mix(in srgb, var(--chip, #808080), #fff 45%);
    background: color-mix(in srgb, var(--chip, #808080) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--chip, #808080) 40%, transparent);
  }
  .multi-badge {
    font-size: 9px;
    color: var(--bg-input);
    background: var(--marker-light);
    padding: 2px 5px;
    border-radius: 3px;
    font-weight: 600;
  }
  .detail-panel {
    border-top: 1px solid var(--border);
    background: var(--bg-header);
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
    color: var(--text-secondary); font-size: 12px; cursor: pointer;
  }
  .detail-name { color: var(--text-muted); font-size: 11px; }
  .detail-desc {
    padding: 6px 12px 10px;
    font-size: 12px;
    color: #aaa;
    line-height: 1.4;
  }
  .detail-params { padding: 4px 12px 8px; }
  .detail-label { font-size: 10px; color: var(--text-secondary); }
  .detail-param { font-size: 11px; color: var(--text-primary); margin-right: 5px; }
  .required { font-size: 10px; color: var(--status-failed-ink); }
  /* System nodes section */
  .system-section {
    padding: 10px 12px;
  }
  .system-section-label {
    font-size: 10px;
    color: var(--text-secondary);
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
    background: var(--bg-input);
    border-radius: 4px;
    border: 1px solid color-mix(in srgb, var(--system-ink) 20%, transparent);
    cursor: grab;
    color: var(--text-primary);
    font-size: 13px;
  }
  .system-item:hover {
    border-color: color-mix(in srgb, var(--system-ink) 50%, transparent);
    background: color-mix(in srgb, var(--system-ink) 8%, transparent);
  }
  .system-icon-small {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    /* System nodes draw in the system ink (SOP Canvas Colours). */
    color: var(--system-ink);
  }
  .drafts-header {
    width: 100%;
    background: none;
    border: none;
    cursor: pointer;
    font: inherit;
    text-align: left;
  }
  .draft-sub {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 2px 0 4px 18px;
    font-size: 11.5px;
    color: var(--text-secondary);
  }
  .draft-sub i {
    width: 8px;
    height: 8px;
    border-radius: 2px;
    display: inline-block;
  }
  .draft-chip-row { padding: 0 10px 6px 25px; }
  .vch {
    display: inline-flex;
    border-radius: 3px;
    border: 1px solid var(--marker-dark);
    font-family: Consolas, monospace;
    font-size: 9.5px;
    line-height: 14px;
    white-space: nowrap;
  }
  .vch .v {
    padding: 0 7px;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: var(--marker-light);
    background: var(--bg-canvas);
    border-radius: 2px 0 0 2px;
  }
  .vch .l {
    padding: 0 6px;
    color: var(--text-secondary);
    background: var(--bg-header);
    border-radius: 0 2px 2px 0;
  }
  .inv {
    color: var(--status-failed-ink);
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.05em;
    cursor: help;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .sidebar-divider {
    height: 1px;
    background: var(--border);
    margin: 4px 10px;
  }
</style>
