<script lang="ts">
  import { get } from 'svelte/store';
  import { nodes, showFlash } from './stores.js';
  import { awaitAllCommitted, startInspector } from './machines/root.js';
  import { loadPipeline, runPipeline, canvasPipelineId } from './pipeline.js';
  import type { PipelineJSON } from './types.js';

  let { fitView }: { fitView: (() => Promise<boolean>) | null } = $props();

  type ModuleForm = 'decorated' | 'ctx' | 'mixed' | 'raw';
  type Topology =
    | 'fan_in'
    | 'keep_going'
    | 'node_sweep'
    | 'sample_override'
    | 'sample_sweep'
    | 'combined_sweep'
    | 'dedup_check'
    | 'sweep_with_failure'
    | 'streaming'
    | 'cancelled_stream'
    | 'imaging_single'
    | 'imaging_double';

  let resetLabel = $state('Reset DB');
  let refreshLabel = $state('Refresh');
  let inspectorOpened = $state(false);
  let moduleForm = $state<ModuleForm>('decorated');
  let topology = $state<Topology>('fan_in');

  function handleOpenInspector() {
    const ok = startInspector();
    if (ok) {
      inspectorOpened = true;
    } else {
      showFlash('Stately Inspector not available', 'error', 2500);
    }
  }

  async function fetchDemoPipeline(): Promise<PipelineJSON> {
    const resp = await fetch(
      `/api/dev/demo-pipeline?module=${moduleForm}&topology=${topology}`
    );
    if (!resp.ok) throw new Error('Failed to fetch demo pipeline');
    return resp.json();
  }

  async function handleLoadDemo() {
    try {
      const pipeline = await fetchDemoPipeline();
      loadPipeline(pipeline);
      canvasPipelineId.set(null);
      if (fitView) await fitView();
    } catch (err) {
      showFlash(`Load demo failed: ${String(err)}`, 'error', 4000);
    }
  }

  async function handleRunDemo() {
    try {
      // Load demo pipeline if canvas is empty
      if (get(nodes).length === 0) {
        const pipeline = await fetchDemoPipeline();
        loadPipeline(pipeline);
        canvasPipelineId.set(null);
        if (fitView) await fitView();
        // Small delay so SvelteFlow registers the nodes before run reads them
        await new Promise(r => setTimeout(r, 100));
      }
      // Dev shortcut: force-commit every dirty row before running so the
      // tester doesn't have to click Lock on each node. Typed transition
      // replaces the legacy `requestCommitAll() + setTimeout(0)`
      // microtask race (ADR-016 Phase 2 expand).
      await awaitAllCommitted();
      await runPipeline();
    } catch (err) {
      showFlash(`Run demo failed: ${String(err)}`, 'error', 4000);
    }
  }

  async function handleResetDb() {
    try {
      await fetch('/api/dev/reset-db', { method: 'POST' });
      await fetch('/api/wfc/refresh', { method: 'POST' });
      resetLabel = 'Done!';
      setTimeout(() => { resetLabel = 'Reset DB'; }, 1000);
    } catch (err) {
      showFlash(`Reset failed: ${String(err)}`, 'error', 4000);
    }
  }

  async function handleRefresh() {
    try {
      await fetch('/api/wfc/refresh', { method: 'POST' });
      refreshLabel = 'Done!';
      setTimeout(() => { refreshLabel = 'Refresh'; }, 1000);
    } catch (err) {
      showFlash(`Refresh failed: ${String(err)}`, 'error', 4000);
    }
  }

  const MODULE_OPTIONS: { value: ModuleForm; label: string; hint: string }[] = [
    { value: 'decorated', label: 'Decorated', hint: '@wfc_method fixtures' },
    { value: 'ctx',       label: 'RunContext', hint: 'RunContext-driven fixtures' },
    { value: 'mixed',     label: 'Mixed',     hint: 'Alternates decorated/ctx across the chain' },
    { value: 'raw',       label: 'Raw',       hint: 'Original bare-script fixtures' },
  ];

  const TOPOLOGY_OPTIONS: { value: Topology; label: string; hint: string }[] = [
    {
      value: 'fan_in',
      label: 'Fan-in chain',
      hint: 'input_selector(fan_mode=in, 4 samples) → merge → filter → scale → transform → qc',
    },
    {
      value: 'keep_going',
      label: 'Keep-going (1/4 fail)',
      hint: 'input_selector(fan_mode=out, 4 samples) → faulty; ctrl_01 overridden to crash. Toggle the toolbar keep-going checkbox to see mixed state instead of pipeline failure.',
    },
    {
      value: 'node_sweep',
      label: 'Node sweep (3× threshold)',
      hint: 'Fan-out, filter.threshold swept over {5, 10, 15}. Expect 12 runs (4 samples × 3 variants).',
    },
    {
      value: 'sample_override',
      label: 'Sample override',
      hint: 'Fan-out, ctrl_01 overrides threshold=1 (others use 10). Expect 4 runs (3 default + 1 override).',
    },
    {
      value: 'sample_sweep',
      label: 'Sample sweep (3× on ctrl_01)',
      hint: 'Fan-out, ctrl_01 sweeps threshold {1,2,5}; others use baseline 10. Expect 6 runs (3 default + 3 ctrl_01).',
    },
    {
      value: 'combined_sweep',
      label: 'Combined (node+sample)',
      hint: 'Fan-out, global sweep {5,10} AND ctrl_01 adds {1,20}. Expect 10 runs (4×2 + 2 ctrl_01 extras).',
    },
    {
      value: 'dedup_check',
      label: 'Dedup check',
      hint: 'Fan-out, global sweep {5,10} ships as-if ctrl_01 override=5 was deduped against v1. Expect 8 runs.',
    },
    {
      value: 'sweep_with_failure',
      label: 'Sweep + 1 failing sample',
      hint: 'Fan-out, filter sweeps {5,10} → faulty; ctrl_01 overrides failure_mode=crash. Enable keep-going; expect 6/8 faulty runs succeed.',
    },
    {
      value: 'streaming',
      label: 'Streaming (heartbeat)',
      hint: 'Single sample → heartbeat. 15 timed stdout ticks over ~5s for SSE log-stream dogfood. Click Run, open Output tab on the heartbeat node to see lines arrive.',
    },
    {
      value: 'cancelled_stream',
      label: 'Cancelled heartbeat (upstream crash)',
      hint: 'Single sample → faulty(crash) → heartbeat. Faulty fails; heartbeat never runs. Open heartbeat Output tab to see the "Cancelled because faulty failed" banner.',
    },
    {
      value: 'imaging_single',
      label: 'Imaging pipeline (single sample)',
      hint: '7-method imaging pipeline isomorphic to the real user DAG — skip-level fan-in at stitch, quantify, export_final. PathsView: exactly 1 path ending at export_final. Parent chips: stitch=2, quantify=2, export_final=3 (measurements, stitched 3-hop skip, masks 2-hop skip).',
    },
    {
      value: 'imaging_double',
      label: 'Imaging pipeline (two samples, fan-out)',
      hint: 'Same imaging DAG with input_selector fan_mode=out over two samples. Each sample spawns its own sub-DAG → PathsView renders exactly 2 terminal rows (one per sample, each ending at export_final). Per-slot parent chips identical to the single-sample case within each sub-DAG.',
    },
  ];
</script>

<div class="dev-toolbar">
  <span class="dev-label">DEV</span>

  <span class="seg-label">Fixtures:</span>
  <div class="seg" role="group" aria-label="Demo fixture module">
    {#each MODULE_OPTIONS as opt}
      <button
        type="button"
        class="seg-btn"
        class:active={moduleForm === opt.value}
        title={opt.hint}
        onclick={() => { moduleForm = opt.value; }}
      >
        {opt.label}
      </button>
    {/each}
  </div>

  <span class="seg-label">Topology:</span>
  <select
    class="topology-select"
    aria-label="Demo topology"
    title={TOPOLOGY_OPTIONS.find(o => o.value === topology)?.hint ?? ''}
    value={topology}
    onchange={(e) => { topology = (e.currentTarget as HTMLSelectElement).value as Topology; }}
  >
    {#each TOPOLOGY_OPTIONS as opt}
      <option value={opt.value} title={opt.hint}>{opt.label}</option>
    {/each}
  </select>

  <button class="dev-btn" onclick={handleLoadDemo}>Load Demo</button>
  <button class="dev-btn dev-btn-run" onclick={handleRunDemo}>Run Demo</button>
  <button class="dev-btn" onclick={() => { void awaitAllCommitted(); }} title="Commit every dirty row across all nodes">Lock All</button>
  <button class="dev-btn" onclick={handleResetDb}>{resetLabel}</button>
  <button class="dev-btn" onclick={handleRefresh}>{refreshLabel}</button>
  <button
    class="dev-btn"
    onclick={handleOpenInspector}
    title="Open the Stately Inspector popup at stately.ai/registry/inspect — click again to reopen if you closed it."
  >
    {inspectorOpened ? 'Reopen Inspector' : 'Open Inspector'}
  </button>
</div>

<style>
  .dev-toolbar {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 10;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    background: #2d2d30;
    border-top: 1px solid #3e3e42;
  }
  .dev-label {
    font-size: 10px;
    font-weight: 700;
    color: #E9A847;
    letter-spacing: 1px;
    padding: 2px 6px;
    background: rgba(233, 168, 71, 0.15);
    border-radius: 3px;
  }
  .seg-label {
    font-size: 10px;
    color: #999;
    letter-spacing: 0.5px;
    text-transform: uppercase;
  }
  .seg {
    display: inline-flex;
    border: 1px solid #3e3e42;
    border-radius: 3px;
    overflow: hidden;
  }
  .topology-select {
    background: #1e1e1e;
    border: 1px solid #3e3e42;
    color: #ccc;
    font-size: 11px;
    padding: 3px 22px 3px 8px;
    border-radius: 3px;
    outline: none;
    cursor: pointer;
    font-family: inherit;
    appearance: none;
    -webkit-appearance: none;
    background-image:
      linear-gradient(45deg, transparent 50%, #888 50%),
      linear-gradient(135deg, #888 50%, transparent 50%);
    background-position: calc(100% - 12px) 50%, calc(100% - 7px) 50%;
    background-size: 5px 5px;
    background-repeat: no-repeat;
    min-width: 200px;
  }
  .topology-select:hover { border-color: #555; }
  .seg-btn {
    padding: 3px 8px;
    background: #2d2d30;
    border: none;
    border-right: 1px solid #3e3e42;
    font-size: 11px;
    color: #ccc;
    cursor: pointer;
  }
  .seg-btn:last-child { border-right: none; }
  .seg-btn:hover { background: #3e3e42; }
  .seg-btn.active {
    background: #1e7a3e;
    color: white;
  }
  .dev-btn {
    padding: 3px 8px;
    background: #3e3e42;
    border: none;
    border-radius: 3px;
    font-size: 11px;
    color: #ccc;
    cursor: pointer;
  }
  .dev-btn:hover {
    background: #505054;
  }
  .dev-btn-run {
    background: #1e7a3e;
    color: white;
  }
  .dev-btn-run:hover {
    background: #259a4e;
  }
</style>
