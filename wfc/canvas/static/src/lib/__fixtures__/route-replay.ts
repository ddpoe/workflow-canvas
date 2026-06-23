/**
 * Playwright route-replay helper.
 *
 * Wires `page.route()` interceptors so the canvas can run end-to-end
 * without a live FastAPI process.  The status endpoint replays a
 * fixed `Timeline`: the Nth call to `/api/workflow/status/:jobId`
 * returns the Nth frame's payload after that frame's `delayMs` has
 * elapsed since the previous call.  The final frame is repeated
 * (terminal sticks) for any further polling.
 *
 * ADR-015 Phase D Pass 2: an optional `sseStream` fixture lets the
 * SSE log-streaming endpoint be replayed event-by-event with delayMs
 * pacing.  Playwright's `route.fulfill()` cannot stream a body, so
 * paced delivery is implemented in the page context: an init-script
 * patches `window.EventSource` for `**\/api/wfc/run/**\/stream-logs**`
 * URLs and dispatches each event after `setTimeout(cumulativeDelay)`.
 * The streaming machine consumes this identically to a real backend
 * stream — see `services.ts::subscribeSSE`.
 */
import type { Page, Route } from '@playwright/test';
import type { Timeline } from './timelines';

export interface SSEStreamEvent {
  /** Quantised delay (ms) before this event is emitted. */
  delayMs: number;
  /** SSE event type — `stdout` | `stderr` | `terminal`. */
  eventType: 'stdout' | 'stderr' | 'terminal';
  /** Raw event-data object (will be JSON-stringified). */
  data: Record<string, unknown>;
}

export interface SSEStreamFixture {
  /** Ordered SSE events to emit; final event should be a `terminal`. */
  events: SSEStreamEvent[];
}

export interface RouteReplayOptions {
  /** Fixed job_id returned by `/api/workflow/run`. */
  jobId?: string;
  /** Validation always succeeds in fixtures; override if a spec needs it to fail. */
  validateValid?: boolean;
  /**
   * Full override of the `/api/workflow/validate` response.  When set,
   * the route is fulfilled verbatim with `{status, contentType: json, body}`
   * and `validateValid` is ignored.
   */
  validateResponse?: { status: number; body: object };
  /**
   * Optional recorded SSE stream replayed against any
   * `**\/api/wfc/run/**\/stream-logs**` URL constructed via
   * `new EventSource(url)` in the page.  Per-event `delayMs` values
   * are honoured: each event arrives at the patched EventSource after
   * its cumulative delay from connection time.  When omitted, the
   * route handler serves a single-frame terminal stub for any fetch
   * consumer (no current code path uses fetch for stream-logs).
   */
  sseStream?: SSEStreamFixture;
}

export async function setupRouteReplay(
  page: Page,
  timeline: Timeline,
  options: RouteReplayOptions = {},
): Promise<void> {
  const jobId = options.jobId ?? 'job-fixture';
  let frameIndex = 0;

  // Patched EventSource for SSE replay.  Installed before any page script
  // so the streaming machine's `new EventSource(url)` resolves to this
  // class.  Wrapped in a Proxy on the original constructor so any URL
  // not matching the stream-logs pattern still goes to the real
  // EventSource untouched (defensive — no current code path opens
  // non-stream-logs EventSources).
  if (options.sseStream) {
    const eventsLiteral = JSON.stringify(options.sseStream.events);
    const initScript = `
(() => {
  const events = ${eventsLiteral};
  const STREAM_URL_RE = /\\/api\\/wfc\\/run\\/[^\\/]+\\/stream-logs/;
  const Real = window.EventSource;

  class PatchedEventSource {
    constructor(url) {
      this.CONNECTING = 0;
      this.OPEN = 1;
      this.CLOSED = 2;
      this.url = url;
      this.withCredentials = false;
      this.readyState = 0;
      this.onopen = null;
      this.onmessage = null;
      this.onerror = null;
      this._timers = [];
      this._closed = false;

      const openTimer = setTimeout(() => {
        if (this._closed) return;
        this.readyState = 1;
        if (this.onopen) this.onopen.call(this, new Event('open'));
      }, 0);
      this._timers.push(openTimer);

      let cum = 0;
      for (const ev of events) {
        cum += ev.delayMs;
        const t = setTimeout(() => {
          if (this._closed) return;
          if (this.readyState === 0) this.readyState = 1;
          const payload = JSON.stringify(Object.assign({ type: ev.eventType }, ev.data));
          const me = new MessageEvent('message', { data: payload });
          if (this.onmessage) this.onmessage.call(this, me);
        }, cum);
        this._timers.push(t);
      }
    }
    close() {
      this._closed = true;
      this.readyState = 2;
      for (const t of this._timers) clearTimeout(t);
      this._timers = [];
    }
    addEventListener() {}
    removeEventListener() {}
    dispatchEvent() { return true; }
  }

  const Patched = new Proxy(Real, {
    construct(target, args) {
      const url = String(args[0]);
      if (STREAM_URL_RE.test(url)) {
        return new PatchedEventSource(url);
      }
      return Reflect.construct(target, args);
    },
  });
  window.EventSource = Patched;
})();
`;
    await page.addInitScript(initScript);
  }

  // Validation endpoint.  Default: always pass.
  await page.route('**/api/workflow/validate', async (route: Route) => {
    if (options.validateResponse) {
      await route.fulfill({
        status: options.validateResponse.status,
        contentType: 'application/json',
        body: JSON.stringify(options.validateResponse.body),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ valid: options.validateValid ?? true, errors: [] }),
    });
  });

  // Submit endpoint.
  await page.route('**/api/workflow/run', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'started', job_id: jobId, message: 'fixture' }),
    });
  });

  // Refresh endpoint.
  await page.route('**/api/wfc/refresh', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  // Cancel endpoint (Pass 2): default mock acknowledges the cancel.  Spec
  // assertions can intercept this route to count calls.
  await page.route('**/api/workflow/cancel/**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'cancelled', job_id: jobId, noop: false }),
    });
  });

  // Status endpoint — replay timeline.
  await page.route('**/api/workflow/status/**', async (route: Route) => {
    if (timeline.length === 0) {
      // No timeline (e.g. zeroJobDAG) — return a stub running frame.
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: jobId,
          overall_status: 'running',
          steps: {},
          node_states: {},
          thread_alive: true,
          log: '',
          error: null,
        }),
      });
      return;
    }
    const idx = Math.min(frameIndex, timeline.length - 1);
    const f = timeline[idx];
    if (frameIndex < timeline.length) frameIndex += 1;
    if (f.delayMs > 0) {
      await new Promise(r => setTimeout(r, f.delayMs));
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(f.payload),
    });
  });

  // SSE log stream — paced delivery happens in the page via the patched
  // EventSource installed above.  This route handler is only reached if
  // a non-EventSource consumer (e.g. fetch) hits stream-logs; today no
  // page code does, so a single-frame terminal stub is sufficient.
  await page.route('**/api/wfc/run/**/stream-logs**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body:
        'data: {"type":"terminal","status":"completed","error_message":null,"error_traceback":null}\n\n',
    });
  });

  // Modules endpoint — many features depend on it; serve an empty list.
  await page.route('**/api/modules', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
}
