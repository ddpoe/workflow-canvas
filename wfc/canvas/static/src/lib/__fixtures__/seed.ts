/**
 * The `?fixture=<key>` canvas bootstrap.
 *
 * When the query parameter is present the canvas seeds nodes and edges
 * synchronously before mount, so a Playwright spec can drive the actor tree
 * without dragging from the sidebar or calling `/api/modules`. `App.svelte`
 * imports it statically, so seeding stays synchronous for every seeded spec
 * and the fixture code ships in the production bundle.
 */
import type { Node } from '@xyflow/svelte';
import { nodes, edges, selectedNodeId } from '../builder/stores.js';
import { methodNode, inputSelectorNode, type MethodNodeOpts } from '../builder/nodeData.js';
import type { CanvasNodeData, MethodDef, ParamDef } from '../shared/types.js';

/** The tabs `App.svelte` switches between. */
export type CanvasTab = 'builder' | 'registry' | 'history';

function seedFixture(key: string): void {
  // The fixture's stand-in for a method contract. Only the params differ
  // between fixtures, so each branch that needs its own passes them here.
  // The node comes from `nodeData.ts` like every other method node, so the
  // seed cannot drift from what a loaded pipeline produces — which is what
  // MethodPanel's streaming-child $effect depends on (it bails out on
  // `data.nodeType !== 'method'`).
  const fixtureMethod = (params: ParamDef[] = []): MethodDef => ({
    name: 'fixture_method',
    module: 'fixture_module',
    color: '#2ecc71',
    inputs: [],
    outputs: [{ name: 'output', type: 'csv' }],
    params,
  });
  const mk = (
    id: string, label: string, x: number,
    method: MethodDef = fixtureMethod(), opts: MethodNodeOpts = {},
  ): Node<CanvasNodeData> => methodNode(id, { x, y: 100 }, method, { label, ...opts });
  let seeded: Node<CanvasNodeData>[] = [];
  if (key === 'single-method' || key === 'cache-hit-method' || key === 'single-method-streaming') {
    // `single-method-streaming` shares the
    // single-method canvas seed.  Streaming-specific behaviour comes
    // from the SSE fixture replayed by `route-replay.ts` and the
    // `subscribeSSE` invocation triggered when the row enters
    // `running` — no new node shape is needed here.
    seeded = [mk('method_a', 'Method A', 100)];
  } else if (key === 'two-methods' || key === 'two-methods-cancel') {
    // `two-methods-cancel` reuses the
    // `two-methods` seed shape (A + B side by side).  The cancel
    // semantics live in the timeline payload (B's per-node row
    // carries `upstream_node_id: 'method_a'`), not in the canvas.
    seeded = [mk('method_a', 'Method A', 100), mk('method_b', 'Method B', 400)];
  } else if (key === 'three-methods-chain') {
    // Seed for the errorMidGraph row.  Three method
    // nodes A, B, C laid out left-to-right.  The polling bridge
    // doesn't depend on graph edges — only on per-node status
    // rows — so no edges are seeded.
    seeded = [
      mk('method_a', 'Method A', 100),
      mk('method_b', 'Method B', 400),
      mk('method_c', 'Method C', 700),
    ];
  } else if (key === 'method-and-system') {
    seeded = [
      inputSelectorNode('system_in', { x: 100, y: 100 }),
      mk('method_only', 'Method Only', 400),
    ];
  } else if (key === 'bound-variable') {
    // Bound-variable smoke: seed one method node with a
    // dict-typed param `mapping` bound to pipeline variable
    // `column_map`, prime the pipelineVariables store, and stash a
    // `pendingBoundVariables` marker so the spawned paramEditorActor
    // for `mapping` lands in the `bound` state on first mount. The
    // fixture deliberately bypasses pipeline.ts::loadPipeline (which
    // requires the full editable JSON contract); the smoke proves the
    // rehydration-and-rendering
    // layer (variables + binding marker → chip) without exercising
    // the History UI or the JSON parser path.
    seeded = [mk('method_a', 'Method A', 100,
      fixtureMethod([{ name: 'mapping', type: 'dict', required: false }]),
      { paramValues: { mapping: { p27: 'X' } } })];
    // Prime the variables store + bound-variable marker. Lazy-import
    // to avoid pulling pipeline.ts into the App module-init path.
    Promise.all([
      import('../builder/stores.js'),
      import('../builder/pipeline.js'),
    ]).then(([{ pipelineVariables }, { pendingBoundVariables }]) => {
      pipelineVariables.set({ column_map: { type: 'dict', value: { p27: 'X' } } });
      pendingBoundVariables.set({ 'method_a::mapping': 'column_map' });
      selectedNodeId.set('method_a');
    }).catch(() => {});
  } else if (key === 'bound-variable-history') {
    // Full E2E roundtrip fixture. Unlike `bound-variable` (which seeds canvas
    // directly to prove the rehydration-and-rendering layer), this
    // fixture leaves the canvas blank and only switches to the
    // History tab. The Playwright test mocks /api/wfc/runs (so a
    // PipelineRow renders), /api/workflow/{id}/editable (so
    // fetchPipelineDocument returns variables + $var refs), and
    // /api/modules (so InspectorPanel knows the bound param's type).
    // Clicking "Open pipeline in Canvas" then exercises the full
    // path: PipelineRow → fetchPipelineDocument → /editable →
    // parsePipelineJSON → loadPipeline → spawn paramEditorActor.
    // No canvas seed; the History tab is asked for below.
    seeded = [];
  } else if (key === 'param-editor') {
    // Param-editor gallery: seed one method node carrying a single
    // required `string` param `note` plus a pre-existing `v1` variant.
    // Lets `param-editor-gallery.spec.ts` drive every paramEditor /
    // variant / aggregator state through Playwright clicks (no SSE
    // timeline) and capture a PNG per state. `string` keeps the
    // input text-shaped (ValueList's editing `input.text` takes
    // `type="text"` unless `isNumber`) so
    // Playwright `fill()` accepts arbitrary strings, and
    // `required: true` lets a blanked-input commit reach the
    // coerce required-check failure path. Only fires when
    // `?fixture=param-editor` is present, so this branch has no
    // production-runtime effect.
    seeded = [mk('method_a', 'Method A', 100,
      fixtureMethod([{ name: 'note', type: 'string', required: true }]),
      { paramValues: { note: 'hello' }, variants: { note: { v1: 'hello' } } })];
  }
  if (seeded.length > 0) {
    nodes.set(seeded);
    edges.set([]);
  }
}

/**
 * Seed the canvas from `?fixture=<key>` and report the tab the fixture wants
 * to open on.
 *
 * Returns `null` when there is no fixture key, or when the fixture leaves the
 * app on its default tab.
 */
export function seedFixtureFromQuery(): CanvasTab | null {
  const key = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search).get('fixture')
    : null;
  if (!key) return null;
  seedFixture(key);

  // History fixture: route-replay covers /api/wfc/runs etc., but the
  // History tab needs to be the initial active tab so smoke tests can
  // assert against PipelinesView without simulating the toolbar click.
  // Plays nicely with the existing canvas fixtures — `?fixture=history-*`
  // implies History tab; everything else falls through to Builder.
  if (key.startsWith('history-')) return 'history';

  // The `bound-variable-history` fixture also boots into the History
  // tab. It doesn't use the `history-` prefix because the fixture name
  // leads with the
  // feature being tested (bound-variable round-trip), not the tab.
  if (key === 'bound-variable-history') return 'history';

  return null;
}
