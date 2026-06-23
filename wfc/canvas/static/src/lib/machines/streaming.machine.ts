/**
 * SSE log-streaming machine.
 *
 * One parent state owns the EventSource subscription so the
 * connecting → streaming flip preserves the connection (sibling-flat
 * states recreated the actor on every transition in xstate v5):
 *
 *   connection-alive  ── invokes subscribeSSE here, single instance ──
 *     ├─ connecting  (no SSE_LINE seen yet)
 *     └─ streaming   (lines flowing)
 *
 *   on SSE_TERMINAL — branch on status (backend `_log_map_terminal_status`
 *   yields `success` | `failed` | `cancelled`):
 *     status === 'success'   → succeeded   (final)
 *     status === 'failed'    → failed      (final, with error_message + traceback)
 *     status === 'cancelled' → cancelled   (final)
 *     anything else          → failed      (defensive: e.g., raw `running` from
 *                                           server.py's wall-time-guard path)
 *
 *   on SSE_ERROR (genuine wire failure that survived services.ts's grace
 *   window) → failed with synthesized error_message="Connection lost"
 *
 * Transient wire blips are absorbed inside `subscribeSSE` (services.ts):
 * the browser's built-in EventSource auto-reconnect handles the retry,
 * and a wallclock guard suppresses SSE_ERROR until the failure persists
 * past the grace window. The machine never sees transient errors.
 */
import { setup, assign, fromCallback, type ActorRefFrom } from 'xstate';
import { isSuccessStatus, isCancelledStatus } from './streaming.guards';

export interface StreamingContext {
  runId: string;
  fullMode: boolean;
  lines: Array<{ kind: 'stdout' | 'stderr'; line: string }>;
  terminalStatus: string | null;
  terminalError: string | null;
  terminalTraceback: string | null;
}

export interface StreamingInput {
  runId: string;
  fullMode?: boolean;
}

export type StreamingEvent =
  | { type: 'SSE_LINE'; kind: 'stdout' | 'stderr'; line: string }
  | {
      type: 'SSE_TERMINAL';
      status: string | null;
      error_message: string | null;
      error_traceback: string | null;
    }
  | { type: 'SSE_ERROR' };

// TODO: manual Retry button. When SSE_ERROR has flipped us to `failed`
// because the grace window in services.ts elapsed, the user has no
// in-Inspector way to re-open the stream short of clicking Run again.
// A Retry action that re-spawned the streaming child would fit here.

const subscribeSSESlot = fromCallback(() => {
  return () => {};
});

export function makeStreamingMachine() {
  return setup({
    types: {} as {
      context: StreamingContext;
      events: StreamingEvent;
      input: StreamingInput;
    },
    actors: {
      subscribeSSE: subscribeSSESlot,
    },
    guards: {
      isSuccessStatus,
      isCancelledStatus,
    },
  }).createMachine({
    id: 'streaming',
    initial: 'connection-alive',
    context: ({ input }) => ({
      runId: input.runId,
      fullMode: input.fullMode ?? false,
      lines: [],
      terminalStatus: null,
      terminalError: null,
      terminalTraceback: null,
    }),
    states: {
      'connection-alive': {
        // Single invoke at the parent — child transitions don't tear it down.
        invoke: {
          id: 'sse',
          src: 'subscribeSSE',
          input: ({ context }) => ({
            runId: context.runId,
            fullMode: context.fullMode,
          }),
        },
        initial: 'connecting',
        states: {
          connecting: {
            on: {
              SSE_LINE: {
                target: 'streaming',
                actions: assign({
                  lines: ({ context, event }) => [
                    ...context.lines,
                    { kind: event.kind, line: event.line },
                  ],
                }),
              },
            },
          },
          streaming: {
            on: {
              SSE_LINE: {
                actions: assign({
                  lines: ({ context, event }) => [
                    ...context.lines,
                    { kind: event.kind, line: event.line },
                  ],
                }),
              },
            },
          },
        },
        on: {
          SSE_TERMINAL: [
            {
              guard: 'isSuccessStatus',
              target: 'succeeded',
              actions: assign({
                terminalStatus: ({ event }) => event.status,
                terminalError: ({ event }) => event.error_message,
                terminalTraceback: ({ event }) => event.error_traceback,
              }),
            },
            {
              guard: 'isCancelledStatus',
              target: 'cancelled',
              actions: assign({
                terminalStatus: ({ event }) => event.status,
                terminalError: ({ event }) => event.error_message,
                terminalTraceback: ({ event }) => event.error_traceback,
              }),
            },
            {
              // Default: 'failed' plus anything unexpected (raw `running`
              // from server.py:2092's wall-time-guard, future enum values, etc.)
              target: 'failed',
              actions: assign({
                terminalStatus: ({ event }) => event.status,
                terminalError: ({ event }) => event.error_message,
                terminalTraceback: ({ event }) => event.error_traceback,
              }),
            },
          ],
          SSE_ERROR: {
            target: 'failed',
            actions: assign({
              terminalStatus: () => 'failed',
              terminalError: () => 'Connection lost',
              terminalTraceback: () => null,
            }),
          },
        },
      },
      succeeded: { type: 'final' },
      failed: { type: 'final' },
      cancelled: { type: 'final' },
    },
  });
}

export type StreamingActor = ActorRefFrom<ReturnType<typeof makeStreamingMachine>>;
