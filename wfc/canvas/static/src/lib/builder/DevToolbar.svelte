<script lang="ts">
  import { get } from 'svelte/store';
  import { nodes, showFlash } from './stores.js';
  import { awaitAllCommitted, startInspector } from '../machines/root.js';
  import { openLockSummary } from './lockFlow.js';
  import { loadPipeline, runPipeline, canvasPipelineId } from './pipeline.js';
  import type { PipelineJSON } from '../shared/types.js';
  import { fetchDemoPipeline as fetchDevDemoPipeline, postResetDb, postRefresh } from './devApi.js';

  let { fitView }: { fitView: (() => Promise<boolean>) | null } = $props();

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
    | 'fault_only'
    | 'streaming_fail'
    | 'imaging_single'
    | 'imaging_double'
    | 'selector_root'
    | 'collapsed_root'
    | 'collapsed_beside_per_sample'
    | 'fan_in_one_slot'
    | 'fan_in_several_slots'
    | 'reference_root'
    | 'selector_plus_reference'
    | 'reference_fan_in'
    | 'diamond_failure'
    | 'mixed_axis_failure'
    | 'missing_output'
    | 'required_slot_unfed';

  let resetLabel = $state('Reset DB');
  let refreshLabel = $state('Refresh');
  let inspectorOpened = $state(false);
  let topology = $state<Topology>('fan_in');

  function handleOpenInspector() {
    const ok = startInspector();
    if (ok) {
      inspectorOpened = true;
    } else {
      showFlash('Stately Inspector not available', 'error', 2500);
    }
  }

  /** Closes over the toolbar's topology select so its callers stay argument-free. */
  function fetchDemoPipeline(): Promise<PipelineJSON> {
    const option = TOPOLOGY_OPTIONS.find(o => o.value === topology);
    if (option?.seeds) {
      // A reference topology may first run a seed pipeline so the reference
      // has a completed run to name; that is one real run's time.
      showFlash('Seeding the referenced run first — this takes one real run', 'success', 6000);
    }
    return fetchDevDemoPipeline(topology);
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
      // tester doesn't have to click Lock on each node. The run starts only
      // after every row has committed.
      await awaitAllCommitted();
      await runPipeline();
    } catch (err) {
      showFlash(`Run demo failed: ${String(err)}`, 'error', 4000);
    }
  }

  async function handleResetDb() {
    try {
      await postResetDb();
      await postRefresh();
      resetLabel = 'Done!';
      setTimeout(() => { resetLabel = 'Reset DB'; }, 1000);
    } catch (err) {
      showFlash(`Reset failed: ${String(err)}`, 'error', 4000);
    }
  }

  async function handleRefresh() {
    try {
      await postRefresh();
      refreshLabel = 'Done!';
      setTimeout(() => { refreshLabel = 'Refresh'; }, 1000);
    } catch (err) {
      showFlash(`Refresh failed: ${String(err)}`, 'error', 4000);
    }
  }

  type TopologyOption = {
    value: Topology;
    label: string;
    hint: string;
    /** Seeds a completed run through the submission path before it can load. */
    seeds?: boolean;
  };
  type TopologyGroup = { label: string; options: TopologyOption[] };

  const TOPOLOGY_GROUPS: TopologyGroup[] = [
    {
      label: 'Sweeps and variants',
      options: [
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
      ],
    },
    {
      label: 'Streaming and faults',
      options: [
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
          value: 'fault_only',
          label: 'Fault only',
          hint: 'Single sample → faulty(crash). The stream ends on the node\'s own terminal failure with its message and traceback.',
        },
        {
          value: 'streaming_fail',
          label: 'Streaming fail (crash mid-stream)',
          hint: 'Single sample → faulty(streaming_fail). Three paced stdout ticks, then a crash on the third: progressive stdout, a stderr traceback, terminal:failed.',
        },
      ],
    },
    {
      label: 'Imaging',
      options: [
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
      ],
    },
    {
      label: 'Catalog: wiring shapes',
      options: [
        {
          value: 'selector_root',
          label: 'Selector root chain',
          hint: 'Fan-out over 4 samples → transform → scale. The sample fills transform\'s slot; scale is chained through transform\'s cache key. Run it twice: the second run is a cache hit on every node.',
        },
        {
          value: 'collapsed_root',
          label: 'Collapsed root (bundle)',
          hint: 'input_selector(fan_mode=in, 4 samples) → merge. Drop a sample from the selector and the key moves; reorder the samples and the re-run is still a hit.',
        },
        {
          value: 'fan_in_several_slots',
          label: 'Fan-in into several slots',
          hint: 'Fan-out → transform and scale → qc, transform on data, scale on metadata. Two upstream runs into two distinct slots of one node.',
        },
        {
          value: 'reference_root',
          label: 'Reference root (no selector)',
          hint: 'run_reference(latest transform run) → scale. No selector: the sample fallback is suppressed and the referenced run is recorded as scale\'s parent. Graft a different transform run in its place and scale\'s key moves.',
          seeds: true,
        },
        {
          value: 'selector_plus_reference',
          label: 'Selector plus reference',
          hint: 'input_selector(ctrl_01) → qc.data and run_reference(latest transform run) → qc.metadata. qc keys on its sample AND its reference parent.',
          seeds: true,
        },
        {
          value: 'reference_fan_in',
          label: 'Reference fan-in (two refs, one slot)',
          hint: 'Two run_references (transform, scale) → merge.sources. Both artifacts arrive under the one declared slot, in link order.',
          seeds: true,
        },
        {
          value: 'collapsed_beside_per_sample',
          label: 'REFUSED: collapsed beside per-sample',
          hint: 'qc fed by a per-sample transform on data and a fan-in selector on metadata. Press Run to see the sole-upstream refusal.',
        },
        {
          value: 'fan_in_one_slot',
          label: 'REFUSED: two method edges, one slot',
          hint: 'transform and scale both → merge.sources. Press Run to see the one-edge-per-slot refusal; only references may fan into one slot.',
        },
      ],
    },
    {
      label: 'Catalog: failures',
      options: [
        {
          value: 'diamond_failure',
          label: 'Diamond failure',
          hint: 'transform → {faulty(crash), scale} → qc. Keep-going on: faulty fails, scale completes, qc is cancelled with one row pointing at faulty\'s failed run.',
        },
        {
          value: 'mixed_axis_failure',
          label: 'Mixed sample-axis failure',
          hint: 'Per-sample branch (faulty crashes on ctrl_01 only → scale) beside a collapsed branch (merge → filter) over the same 4 samples. Keep-going on: scale is cancelled for ctrl_01 alone; everything else completes.',
        },
        {
          value: 'missing_output',
          label: 'Missing declared output',
          hint: 'faulty(missing_output): exits 0 without writing its declared slot. Collect fails the run naming the method and the slot.',
        },
        {
          value: 'required_slot_unfed',
          label: 'Required slot unfed',
          hint: 'build_config → stitch.config only; stitch.corrected is required and nothing feeds it. Validate warns, the claim refuses naming the slot.',
        },
      ],
    },
  ];

  const TOPOLOGY_OPTIONS: TopologyOption[] = TOPOLOGY_GROUPS.flatMap(g => g.options);
</script>

<div class="dev-toolbar">
  <span class="dev-label">DEV</span>

  <span class="seg-label">Topology:</span>
  <select
    class="topology-select"
    aria-label="Demo topology"
    title={TOPOLOGY_OPTIONS.find(o => o.value === topology)?.hint ?? ''}
    value={topology}
    onchange={(e) => { topology = (e.currentTarget as HTMLSelectElement).value as Topology; }}
  >
    {#each TOPOLOGY_GROUPS as group}
      <optgroup label={group.label}>
        {#each group.options as opt}
          <option value={opt.value} title={opt.hint}>{opt.label}</option>
        {/each}
      </optgroup>
    {/each}
  </select>

  <button class="dev-btn" onclick={handleLoadDemo}>Load Demo</button>
  <button class="dev-btn dev-btn-run" onclick={handleRunDemo}>Run Demo</button>
  <button class="dev-btn" onclick={() => openLockSummary('lock')} title="Commit every dirty row across all nodes">Lock All</button>
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
    border-top: 1px solid var(--border);
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
  .topology-select {
    background: #1e1e1e;
    border: 1px solid var(--border);
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
  .dev-btn {
    padding: 3px 8px;
    background: var(--border);
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
