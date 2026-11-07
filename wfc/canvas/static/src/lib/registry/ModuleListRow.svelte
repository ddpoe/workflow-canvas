<script lang="ts">
  /**
   * One module in the Registry's Modules tab, laid out like a Methods row:
   * its MODULE tag in the module's colour, its description, a link to its
   * methods and the Colour button, in columns that line up row to row.
   * Clicking the row opens or closes the module; the link and Colour act
   * on their own (Colour opens the module with its picker).
   */
  import LabelTag from '../shared/LabelTag.svelte';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import type { ModuleRow } from './api.js';

  let { module, expanded, onToggle, onMethods, onColour }: {
    module: ModuleRow;
    expanded: boolean;
    onToggle: () => void;
    onMethods: () => void;
    onColour: () => void;
  } = $props();

  const color = $derived(moduleColor(module.name));
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="modrow" class:open={expanded} role="button" tabindex="0" aria-expanded={expanded}
     data-testid="module-row" data-module={module.name} style="--m: {color}"
     onclick={onToggle}
     onkeydown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onToggle(); } }}>
  <span class="chev">{expanded ? '▼' : '▶'}</span>
  <span class="cell"><LabelTag kind="module" label="MODULE" value={module.name} {color} title={module.name} /></span>
  <span class="desc" title={module.description}>{module.description}</span>
  <span class="cell">
    <button type="button" class="link" data-testid="module-methods-link" title="View methods in {module.name}"
            onclick={(e) => { e.stopPropagation(); onMethods(); }}>{module.methods} {module.methods === 1 ? 'method' : 'methods'} →</button>
  </span>
  <span class="cell">
    <button type="button" class="colbtn" data-testid="module-colour-button" aria-label="Colour of {module.name}"
            onclick={(e) => { e.stopPropagation(); onColour(); }}><i></i>Colour</button>
  </span>
</div>

<style>
  .modrow {
    display: grid; grid-template-columns: var(--mod-grid); column-gap: 14px; align-items: center;
    padding: 8px 14px 8px 11px; cursor: pointer; min-height: 38px;
    border-left: 3px solid var(--m); background: var(--bg-header);
  }
  .modrow:hover, .modrow.open { background: #333337; }
  .modrow:focus-visible { outline: 1px solid var(--accent); outline-offset: -1px; }
  .chev { color: var(--text-secondary); font-size: 9px; text-align: center; user-select: none; }
  .cell { min-width: 0; display: flex; align-items: center; }
  .desc { color: var(--text-secondary); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .link {
    background: none; border: none; color: var(--accent); font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 11.5px; padding: 2px 6px; cursor: pointer; border-radius: 3px;
  }
  .link:hover { background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .colbtn {
    display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--border); border-radius: 4px;
    padding: 3px 9px; font-size: 11.5px; color: #ccc; background: #1d1d1d; cursor: pointer;
  }
  .colbtn:hover { background: var(--bg-input); }
  .colbtn i { width: 13px; height: 13px; border-radius: 3px; background: var(--m); display: inline-block; }
</style>
