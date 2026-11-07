<script lang="ts">
  /**
   * An open module in the Registry's Modules tab: its contracts and its
   * methods (newest version and env) as two tables, its source, and, when
   * the row's Colour button opened it, the module's Colour picker.
   */
  import LabelTag from '../shared/LabelTag.svelte';
  import { moduleColor } from '../shared/moduleColor.svelte.js';
  import { kindColor } from '../shared/slotColor.js';
  import ModuleColorPicker from './ModuleColorPicker.svelte';
  import type { MethodRow, ModuleRow } from './api.js';

  let { module, methods, colourOpen, onColourClose }: {
    module: ModuleRow;
    /** This module's methods. */
    methods: MethodRow[];
    colourOpen: boolean;
    onColourClose: () => void;
  } = $props();

  function newestLabel(m: MethodRow): string {
    const versions = m.versions ?? [];
    const v = versions.find(x => x.newest) ?? versions.at(-1);
    return v ? `v${v.label}` : '—';
  }
</script>

<div class="mpanel" data-testid="module-panel">
  <div class="tables">
    <div class="tbl" data-testid="module-contracts">
      <div class="th"><span>CONTRACTS <b>{module.contracts.length}</b></span><span>TYPE</span><span></span></div>
      {#each module.contracts as c (`${c.type}:${c.name}`)}
        <div class="tr" data-testid="module-contract-row">
          <span class="mono">{c.name}</span>
          <span>{#if c.type === 'param'}<span class="tyc">{c.type}</span>{:else}<span class="tyc kind" style:--chip={kindColor(c.type)}>{c.type}</span>{/if}</span>
          <span></span>
        </div>
      {:else}
        <div class="tr none"><span class="dim">No contracts.</span></div>
      {/each}
    </div>
    <div class="tbl" data-testid="module-methods">
      <div class="th"><span>METHODS <b>{methods.length}</b></span><span>VERSION</span><span class="end">ENV</span></div>
      {#each methods as m (m.name)}
        <div class="tr" data-testid="module-method-row">
          <span class="mono name" title={m.name}>{m.name}</span>
          <span><LabelTag kind="version" label="version" value={newestLabel(m)} /></span>
          <span class="end dim mono" title={m.env}>{m.env}</span>
        </div>
      {:else}
        <div class="tr none"><span class="dim">No methods.</span></div>
      {/each}
    </div>
  </div>
  <div class="side">
    <div class="card">
      <div class="k">SOURCE</div>
      <div class="src mono" data-testid="module-source" title={module.source}>{module.source || '—'}</div>
    </div>
    {#if colourOpen}
      <div class="picker-wrap">
        <ModuleColorPicker module={module.name} current={moduleColor(module.name)}
                           sample={methods[0]?.name ?? module.name} onClose={onColourClose} />
      </div>
    {/if}
  </div>
</div>

<style>
  .mpanel {
    display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 18px;
    padding: 4px 14px 16px 36px; border-top: 1px solid rgba(62, 62, 66, 0.5); background: var(--bg-input);
  }
  .mono { font-family: "JetBrains Mono", Consolas, monospace; font-size: 11.5px; }
  .dim { color: var(--text-secondary); }
  .end { text-align: right; justify-self: end; }
  .tbl { border: 1px solid var(--border); border-radius: 5px; margin-top: 12px; background: #232323; font-size: 12.5px; }
  .th, .tr { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr); column-gap: 10px;
             padding: 7px 12px; align-items: center; }
  .th { font-size: 10.5px; letter-spacing: .08em; color: #bbb; font-weight: 600; border-bottom: 1px solid #333;
        background: #262626; }
  .th b { color: var(--text-secondary); font-weight: 400; margin-left: 4px; }
  .th span + span { color: var(--text-secondary); font-weight: 400; }
  .tr { border-bottom: 1px solid #2c2c2c; }
  .tr:last-child { border-bottom: 0; }
  .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tyc { display: inline-block; font-size: 10px; border: 1px solid var(--border); color: var(--text-secondary);
         border-radius: 3px; padding: 0 5px; font-family: "JetBrains Mono", Consolas, monospace; }
  /* Contract kinds carry no run state: a palette colour (kindColor). */
  .tyc.kind { color: color-mix(in srgb, var(--chip), #fff 45%); background: color-mix(in srgb, var(--chip) 16%, transparent);
              border-color: color-mix(in srgb, var(--chip) 50%, transparent); }
  .card { border: 1px solid var(--border); border-radius: 6px; background: #232323; padding: 11px 13px; margin-top: 12px; }
  .k { font-size: 10.5px; letter-spacing: .08em; color: #bbb; font-weight: 600; margin-bottom: 6px; }
  .src { background: #1b1b1b; border: 1px solid var(--border); border-radius: 4px; padding: 4px 8px; color: #bbb;
         white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .picker-wrap { margin-top: 12px; }
</style>
