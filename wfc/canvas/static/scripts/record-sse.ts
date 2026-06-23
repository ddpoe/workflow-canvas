#!/usr/bin/env -S npx tsx
/**
 * SSE record-and-replay tool (ADR-015 Phase D Pass 2).
 *
 * Connects to a live dev server, runs a topology end-to-end, captures
 * every SSE event from `/api/wfc/run/<runId>/stream-logs` with quantised
 * (50ms-bucket) inter-event delays, and writes the recording to disk
 * as JSON.  The fixture is then consumed by Playwright via
 * `route-replay.ts::setupRouteReplay({ sseStream })`.
 *
 * Why record rather than hand-author:
 * - The SSE wire format (per-event JSON shape, line ordering, terminal
 *   payload) is defined by `wfc.canvas.server::stream_run_logs`.  Recording
 *   is byte-faithful; hand-authored fixtures drift on every backend
 *   protocol change.
 * - Re-running this script after a backend protocol change makes the
 *   diff visible in the PR.
 *
 * Usage (from the dev workspace, with the canvas dev server running):
 *
 *     npx tsx wfc/canvas/static/scripts/record-sse.ts \
 *       --topology streaming --node node_heartbeat \
 *       --output wfc/canvas/static/tests/e2e/fixtures/sse-streams/streaming.json
 *
 *     npx tsx wfc/canvas/static/scripts/record-sse.ts \
 *       --topology fault_only --node node_faulty \
 *       --output wfc/canvas/static/tests/e2e/fixtures/sse-streams/fault-on-stream.json
 *
 *     npx tsx wfc/canvas/static/scripts/record-sse.ts \
 *       --topology streaming --node node_heartbeat --cancel-after-events 5 \
 *       --output wfc/canvas/static/tests/e2e/fixtures/sse-streams/streaming-cancelled.json
 *
 * Flags:
 *   --topology <name>                Demo topology (registered in scripts/dev_routes.py)
 *   --node <node_id>                 Node whose run_ids[0] is opened against stream-logs
 *   --cancel-after-events <N>        Optional: POST cancel after N events
 *   --output <path>                  Output JSON path (relative to cwd)
 *   --base <url>                     Backend base URL (default: http://localhost:8500)
 *
 * Output JSON shape: { events: [{ delayMs, eventType, data }, ...] }
 * (matches `SSEStreamFixture` in `route-replay.ts`).
 */

interface RecordedEvent {
  delayMs: number;
  eventType: 'stdout' | 'stderr' | 'terminal';
  data: Record<string, unknown>;
}

interface RecordingFile {
  topology: string;
  node: string;
  cancelAfterEvents?: number;
  recordedAt: string;
  events: RecordedEvent[];
}

const QUANTUM_MS = 50;

function quantise(deltaMs: number): number {
  return Math.round(deltaMs / QUANTUM_MS) * QUANTUM_MS;
}

function parseArgs(argv: string[]) {
  const opts: Record<string, string> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      opts[a.slice(2)] = argv[i + 1];
      i++;
    }
  }
  return opts;
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const topology = opts.topology;
  const targetNode = opts.node;
  const output = opts.output;
  const cancelAfter = opts['cancel-after-events']
    ? parseInt(opts['cancel-after-events'], 10)
    : undefined;
  const base = opts.base ?? 'http://localhost:8500';

  if (!topology || !targetNode || !output) {
    console.error('Usage: --topology <name> --node <node_id> --output <path> [--cancel-after-events N]');
    process.exit(2);
  }

  // 1. Fetch the demo pipeline.
  const demoRes = await fetch(`${base}/api/dev/demo-pipeline?topology=${topology}`);
  if (!demoRes.ok) throw new Error(`demo-pipeline ${demoRes.status}`);
  const pipeline = await demoRes.json();

  // 1b. Cache-bust: inject a per-recording nonce into method-node params so
  // identical-input runs never hit wfc's prior-run cache. The methods accept
  // params via `dict.get(...)`, so an unknown key is harmless.
  const nonce = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  for (const node of (pipeline as { nodes?: Array<{ type?: string; params?: Record<string, unknown> }> }).nodes ?? []) {
    if (node.type !== 'input_selector') {
      node.params = { ...(node.params ?? {}), _recording_nonce: nonce };
    }
  }

  // 2. Submit it.
  const runRes = await fetch(`${base}/api/workflow/run`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(pipeline),
  });
  if (!runRes.ok) throw new Error(`run ${runRes.status}`);
  const { job_id: jobId } = await runRes.json();
  console.error(`[record-sse] submitted ${jobId}`);

  // 3. Poll status until target node is running and run_ids[0] available.
  let runId: string | null = null;
  for (let i = 0; i < 200; i++) {
    const statusRes = await fetch(`${base}/api/workflow/status/${jobId}`);
    if (statusRes.ok) {
      const s = await statusRes.json();
      const ns = s.node_states?.[targetNode];
      if (ns?.run_ids?.[0]) {
        runId = ns.run_ids[0];
        break;
      }
    }
    await new Promise(r => setTimeout(r, 100));
  }
  if (!runId) throw new Error(`target node ${targetNode} never produced a run_id`);
  console.error(`[record-sse] streaming run ${runId}`);

  // 4. Open EventSource and capture events.
  // tsx in node — use undici's EventSource via 'node:events'+fetch streaming.
  // Simpler: poll the SSE endpoint with fetch + manual line parser.
  const streamRes = await fetch(`${base}/api/wfc/run/${runId}/stream-logs`);
  if (!streamRes.ok || !streamRes.body) throw new Error(`stream ${streamRes.status}`);

  const decoder = new TextDecoder('utf-8');
  const reader = streamRes.body.getReader();
  let buf = '';
  const events: RecordedEvent[] = [];
  let lastTs = Date.now();
  let cancelled = false;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep = buf.indexOf('\n\n');
    while (sep !== -1) {
      const block = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      sep = buf.indexOf('\n\n');
      const dataLine = block.split('\n').find(l => l.startsWith('data: '));
      if (!dataLine) continue;
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(dataLine.slice(6));
      } catch { continue; }
      const now = Date.now();
      const delayMs = quantise(now - lastTs);
      lastTs = now;
      const eventType = (payload.type as RecordedEvent['eventType']) ?? 'stdout';
      const { type: _t, ...rest } = payload;
      events.push({ delayMs, eventType, data: rest });
      if (cancelAfter && !cancelled && events.length >= cancelAfter) {
        cancelled = true;
        console.error(`[record-sse] firing cancel after ${events.length} events`);
        fetch(`${base}/api/workflow/cancel/${jobId}`, { method: 'POST' })
          .catch(err => console.error('[record-sse] cancel error', err));
      }
      if (eventType === 'terminal') {
        // record the terminal then stop reading.
        await reader.cancel();
        break;
      }
    }
  }

  const out: RecordingFile = {
    topology,
    node: targetNode,
    cancelAfterEvents: cancelAfter,
    recordedAt: new Date().toISOString(),
    events,
  };
  const fs = await import('node:fs/promises');
  const path = await import('node:path');
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(output, JSON.stringify(out, null, 2), 'utf-8');
  console.error(`[record-sse] wrote ${events.length} events to ${output}`);
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
