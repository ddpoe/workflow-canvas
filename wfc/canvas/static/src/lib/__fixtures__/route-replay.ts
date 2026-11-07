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
 * An optional `sseStream` fixture lets the
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

  // Cancel endpoint: default mock acknowledges the cancel.  Spec
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

// ---------- A run's artifacts: listing, file, preview and image routes ----------

/** The image route's sizes and the longer side each is bounded to (the server's `IMAGE_SIZES`). */
export const IMAGE_SIDE = { thumb: 260, view: 2048, large: 4096 } as const;
export type FixtureImageSize = keyof typeof IMAGE_SIDE;

/** One image a fixture run holds: its file's facts and what the image route answers. */
export interface ImageFixture {
  /** The file's own pixel size. */
  width: number;
  height: number;
  bitDepth?: number;
  pages?: number;
  /** Image-info says the image is too large to decode; the image route answers 422. */
  refused?: boolean;
  /** The bytes the image route answers at every size, and their content type. */
  body: Buffer | string;
  contentType: string;
  /**
   * How long the image route waits before answering the viewer's sizes
   * (`view`, `large`): the viewer's "making preview…" state. The list's
   * `thumb` is never delayed.
   */
  delayMs?: number;
}

/** A run's artifact routes, keyed by an artifact's displayed path (`name` or `folder/member`). */
export interface ArtifactRouteFixture {
  runId: string;
  /** The `/artifacts` listing, as the server gives it (with `viewer` and `extension`). */
  listing: unknown[];
  /** File route bodies; a path with none answers a few bytes of text. */
  files?: Record<string, { body: Buffer | string; contentType: string }>;
  /** Preview route answers (`ArtifactPreview`) for tables, JSON and text. */
  previews?: Record<string, unknown>;
  images?: Record<string, ImageFixture>;
}

function fitSide(width: number, height: number, longest: number): { width: number; height: number } {
  const side = Math.max(width, height);
  if (side <= longest) return { width, height };
  const scale = longest / side;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/**
 * The image-info answer for one image at one size, as the server computes it
 * from the file's header: the preview's size, bit depth reduction and pages.
 * The memory a conversion needs is simplified to the decoded image plus the
 * preview at 4 bytes a pixel; the memory_mb that admits it is twice that,
 * in whole MiB, as the server's rule (each conversion within half).
 *
 * Args:
 *     img: The image's facts.
 *     size: The preview size asked for.
 *
 * Returns:
 *     The `ImageInfo` body.
 */
export function imageInfoFor(img: ImageFixture, size: FixtureImageSize): Record<string, unknown> {
  const preview = fitSide(img.width, img.height, IMAGE_SIDE[size]);
  const bitDepth = img.bitDepth ?? 8;
  const memoryBytes = img.width * img.height * 4 + preview.width * preview.height * 4;
  return {
    memory_bytes: memoryBytes,
    memory_mb_needed: Math.ceil((2 * memoryBytes) / 2 ** 20),
    width: img.width,
    height: img.height,
    preview_width: preview.width,
    preview_height: preview.height,
    bit_depth: bitDepth,
    bit_depth_reduced: bitDepth > 8,
    pages: img.pages ?? 1,
    decoded_bytes: img.width * img.height * 4,
    refused: img.refused ? `decoding ${img.width} x ${img.height} would exceed the memory limit` : null,
  };
}

/**
 * Serve one run's artifacts without a server: the listing, the file route,
 * the preview route and the image and image-info routes, from a fixture.
 *
 * Registered routes win over earlier ones; any other `/api/wfc/run/...` URL
 * falls back to the routes registered before this call (the bare run fetch,
 * cancelled-descendants).
 *
 * Args:
 *     page: The Playwright page.
 *     fixture: The run's artifacts.
 *
 * Returns:
 *     The paths and sizes the image route was asked for, in order: a spec
 *     reads it to check which size the viewer loaded.
 */
export async function setupArtifactRoutes(
  page: Page,
  fixture: ArtifactRouteFixture,
): Promise<{ imageRequests: { path: string; size: string }[] }> {
  const imageRequests: { path: string; size: string }[] = [];
  const prefix = `/api/wfc/run/${encodeURIComponent(fixture.runId)}/`;
  const json = (body: unknown, status = 200) => ({
    status, contentType: 'application/json', body: JSON.stringify(body),
  });

  await page.route('**/api/wfc/run/**', async (route: Route) => {
    const url = new URL(route.request().url());
    if (!url.pathname.startsWith(prefix)) return route.fallback();
    const rest = url.pathname.slice(prefix.length);
    if (rest === 'artifacts') return route.fulfill(json(fixture.listing));
    const slash = rest.indexOf('/');
    if (slash < 0) return route.fallback();
    const kind = rest.slice(0, slash);
    const path = rest.slice(slash + 1).split('/').map(decodeURIComponent).join('/');
    const size = (url.searchParams.get('size') ?? 'view') as FixtureImageSize;

    if (kind === 'artifact') {
      const file = fixture.files?.[path];
      if (file) return route.fulfill({ status: 200, contentType: file.contentType, body: file.body });
      return route.fulfill({ status: 200, contentType: 'text/plain', body: `fixture bytes of ${path}\n` });
    }
    if (kind === 'preview') {
      const preview = fixture.previews?.[path];
      if (preview) return route.fulfill(json(preview));
      return route.fulfill(json({ detail: `no preview for ${path}` }, 415));
    }
    if (kind === 'image-info' || kind === 'image') {
      const img = fixture.images?.[path];
      if (!img) return route.fulfill(json({ detail: `${path} is not a readable image` }, 415));
      if (kind === 'image-info') return route.fulfill(json(imageInfoFor(img, size)));
      imageRequests.push({ path, size });
      if (img.refused) {
        const { memory_mb_needed } = imageInfoFor(img, size);
        return route.fulfill(json({ detail: 'too large', width: img.width, height: img.height, memory_mb_needed }, 422));
      }
      if (img.delayMs && size !== 'thumb') await new Promise(r => setTimeout(r, img.delayMs));
      return route.fulfill({ status: 200, contentType: img.contentType, body: img.body });
    }
    return route.fallback();
  });
  return { imageRequests };
}
