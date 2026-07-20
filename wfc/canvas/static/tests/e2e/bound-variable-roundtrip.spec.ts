/**
 * ADR-017 Track 2 Phase D — load-roundtrip smoke (US-4 acceptance).
 *
 * Two tests covering the full US-4 flow:
 *
 *   - Test 1 (existing direct-rehydration smoke): Drives loadPipeline via
 *     the `?fixture=bound-variable` bootstrap. Proves the rehydration-and-
 *     rendering layer in isolation: variables panel, bind chip, resolved
 *     value display.
 *
 *   - Test 2 (full click→fetch→parse path, Reviewer iter 1 addition):
 *     Mocks /api/workflow/{pipeline_id}/editable + history runs, navigates
 *     to History tab via `?fixture=bound-variable-history`, clicks
 *     "Open pipeline in Canvas" on a PipelineRow, switches to Builder,
 *     and asserts variable + chip render. Proves the integration of:
 *       - PipelineRow click → fetchPipelineDocument → /editable fetch
 *       - parsePipelineJSON rehydrates pipelineVariables + binding markers
 *       - paramEditorActor spawns into `bound` from the markers
 *       - ValueList renders the chip from actor snapshot.
 */
import { expect, test, type Page } from '@playwright/test';

test.describe('Bound variable round-trip (US-4 smoke)', () => {
  test('loadPipeline with variables + $var ref renders bound chip in Inspector and variable in panel', async ({ page }) => {
    // Stub /api/wfc/* endpoints so the dev toolbar / status banners
    // don't block startup.
    await page.route('**/api/wfc/runs', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }));
    await page.route('**/api/wfc/modules', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }));
    await page.route('**/api/wfc/methods', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }));
    await page.route('**/api/wfc/status', async (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ loaded: true, path: '/x', modules: 0, runs: 0 }),
    }));
    await page.route('**/api/dev/status', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ dev: false }),
    }));
    await page.route('**/api/wfc/samples', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }));

    await page.goto('/?fixture=bound-variable');

    // Pipeline Variables panel: column_map is rendered with its value.
    // The panel mounts inside the Sidebar > Builder tab (default tab).
    const pvRow = page.locator('[data-testid="pv-row"][data-variable-name="column_map"]');
    await expect(pvRow).toBeVisible({ timeout: 10_000 });

    // Inspector: bound row chip must be visible. The fixture sets
    // selectedNodeId='method_a' so the InspectorPanel mounts the
    // method's params, including the bound `mapping` row.
    const chip = page.locator('[data-testid="bound-row"]').first();
    await expect(chip).toBeVisible({ timeout: 10_000 });
    await expect(chip).toContainText('→ column_map');
    // Resolved value displayed in the chip is the dict (stringified).
    await expect(chip).toContainText('"p27":"X"');
  });

  // Reviewer iter 1 issue 3: full click → fetch → parse path. Mocks the
  // /api/workflow/{id}/editable endpoint, /api/wfc/runs (so the History tab
  // shows a PipelineRow), and /api/modules (so InspectorPanel knows the
  // bound param's contract type). Drives a real PipelineRow click.
  test('Open pipeline in Canvas: full click → /editable fetch → parsePipelineJSON → chip render', async ({ page }) => {
    const PIPE_ID = 'pipeline-with-variables';

    // The pre-substitution editable form returned by
    // /api/workflow/{id}/editable: top-level `variables` block with
    // column_map, plus one method node whose `mapping` param is bound
    // via {$var: column_map}. Shape conforms to PipelineJSON (nodes,
    // links, samples) — loadPipeline → parsePipelineJSON consumes it
    // verbatim, populates pipelineVariables store, and writes the
    // per-row binding marker into pendingBoundVariables so the spawned
    // paramEditorActor lands in `bound` on first render.
    const editableBody = {
      name: 'pipeline-with-variables',
      version: 1,
      pipeline_id: PIPE_ID,
      samples: ['sample_a'],
      variables: {
        column_map: {
          type: 'dict',
          value: { p27: 'R1_p27', CycD1: 'R1_CycD1' },
        },
      },
      nodes: [
        {
          id: 'node_1',
          type: 'method',
          method: 'method_a',
          module: 'mod_a',
          position: { x: 100, y: 100 },
          params: { mapping: { $var: 'column_map' } },
        },
      ],
      links: [],
    };
    let editableHits = 0;
    await page.route(`**/api/workflow/${PIPE_ID}/editable`, async (r) => {
      editableHits += 1;
      await r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(editableBody),
      });
    });

    // Mock history runs so a PipelineRow renders for PIPE_ID.
    const baseRun = {
      id: 'r1',
      module: 'mod_a',
      method: 'method_a',
      version: '1',
      timestamp: 1_700_000_000_000,
      duration: 1.5,
      status: 'success',
      inputs: {},
      outputs: {},
      metrics: {},
      dataSource: 'sample_a',
      parentRunIds: [],
      parents: [],
      experimentId: 'exp1',
      runName: `${PIPE_ID}/foo`,
      nid: 'v1',
      user: 'tester',
      favorite: false,
      pipelineId: PIPE_ID,
      scriptPath: null,
    };
    await page.route('**/api/wfc/runs', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify([baseRun]),
    }));
    await page.route('**/api/wfc/run/*', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(baseRun),
    }));
    await page.route('**/api/wfc/modules', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(['mod_a']),
    }));
    await page.route('**/api/wfc/methods', async (r) => r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { name: 'method_a', module: 'mod_a', script_path: null, env: 'x' },
      ]),
    }));
    await page.route('**/api/wfc/status', async (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ loaded: true, path: '/x', modules: 1, runs: 1 }),
    }));
    await page.route('**/api/dev/status', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ dev: false }),
    }));
    await page.route('**/api/wfc/samples', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }));

    // /api/modules — Sidebar.svelte uses this (NOT /api/wfc/modules) to
    // populate the modules store, which loadPipeline → findMethodDef
    // reads to attach typed params to the loaded node. Without a typed
    // `mapping` param entry, InspectorPanel won't render any rows (and
    // no paramEditorActor spawns), so the bound chip never appears.
    // Shape: { module_name: { description, methods: { method_name: {
    // params_schema: { param_name: { type, required, ... } } } } } }.
    await page.route('**/api/modules', async (r) => r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        mod_a: {
          description: 'Test module',
          methods: {
            method_a: {
              version: '1',
              description: 'Test method',
              inputs: { data: { type: 'csv' } },
              outputs: { output: { type: 'csv' } },
              params_schema: {
                mapping: {
                  type: 'dict',
                  required: false,
                  default: {},
                  description: 'Column mapping',
                },
              },
            },
          },
        },
      }),
    }));
    // Samples endpoint loaded by stores.ts::loadSamples.
    await page.route('**/api/samples', async (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }));

    // Boot into the History tab with the canvas blank. The
    // bound-variable-history fixture sets activeTab='history' but
    // does NOT seed runs or canvas state — those come from the route
    // mocks above and from loadPipeline after the click.
    // Wait for /api/modules to resolve before clicking Open: Sidebar's
    // $effect fires fetchModules() on mount (even with main-content
    // display:none), and loadPipeline → findMethodDef reads the modules
    // store synchronously inside handleOpenPipeline.
    const modulesResponse = page.waitForResponse('**/api/modules');
    await page.goto('/?fixture=bound-variable-history');
    await modulesResponse;

    // Wait for the History tab + PipelineRow to render. Default History
    // view is Descendants — switch to Pipelines first.
    await page.waitForSelector('.view-switcher', { timeout: 10_000 });
    await page.locator('.view-switcher .seg', { hasText: 'Pipelines' }).click();
    const row = page.locator('.pipeline-row', { hasText: PIPE_ID });
    await expect(row).toBeVisible({ timeout: 10_000 });

    // The "Open pipeline in Canvas" button lives in .row-head (always
    // visible; no need to expand the row first).
    const openBtn = row.locator('button.open-btn');
    await expect(openBtn).toBeVisible({ timeout: 5_000 });
    await openBtn.click();

    // Verify /editable was hit — proves fetchPipelineDocument's sidecar
    // path ran, not just the fallback /document path.
    await expect.poll(() => editableHits, { timeout: 5_000 }).toBeGreaterThan(0);

    // Switch to the Builder tab so Sidebar (Pipeline Variables panel)
    // and InspectorPanel become visible. PipelineRow.handleOpenPipeline
    // doesn't auto-switch tabs; this is a real user gesture.
    await page.locator('button.tab', { hasText: 'Builder' }).click();

    // Pipeline Variables panel shows column_map after the fetch+parse
    // roundtrip. The panel reads from pipelineVariables store, which
    // loadPipeline populated from editableBody.variables.
    const pvRow = page.locator('[data-testid="pv-row"][data-variable-name="column_map"]');
    await expect(pvRow).toBeVisible({ timeout: 10_000 });
    await expect(pvRow).toContainText('column_map');

    // Click on the loaded node (id 'node_1') to open it in the Inspector.
    // SvelteFlow renders nodes with [data-id="..."]. The bound chip
    // appears once InspectorPanel mounts the method's params and the
    // per-row paramEditorActor consumes its pendingBoundVariables marker.
    const node = page.locator('[data-id="node_1"]');
    await expect(node).toBeVisible({ timeout: 5_000 });
    await node.click();

    // Bound row chip — `→ column_map` proves the per-row binding
    // marker was consumed and the actor is in `bound`.
    const chip = page.locator('[data-testid="bound-row"]').first();
    await expect(chip).toBeVisible({ timeout: 10_000 });
    await expect(chip).toContainText('→ column_map');
    // Resolved value (stringified dict) reflects the variable's value.
    await expect(chip).toContainText('"p27":"R1_p27"');

    // Updating column_map in the panel updates the chip's resolved-value
    // display. PipelineVariablesPanel exposes only Add and Delete; Add
    // with the same name replaces the existing entry (createVariable in
    // stores.ts is idempotent on name). Replace column_map with a new
    // dict and confirm the chip's resolved view updates.
    await page.locator('[data-testid="pv-add-variable"]').click();
    await page.locator('[data-testid="pv-new-name"]').fill('column_map');
    await page.locator('[data-testid="pv-new-type"]').selectOption('dict');
    await page.locator('[data-testid="pv-new-value"]').fill('{"p27":"NEW_p27"}');
    await page.locator('[data-testid="pv-confirm-add"]').click();

    // Chip's resolved value re-renders from the updated variable.
    await expect(chip).toContainText('"p27":"NEW_p27"');
    await expect(chip).not.toContainText('R1_p27');
  });
});
