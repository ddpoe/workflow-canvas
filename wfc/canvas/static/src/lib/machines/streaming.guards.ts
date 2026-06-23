/**
 * Named guards for the SSE log-streaming machine.
 *
 * Extracted out of `streaming.machine.ts` so the SSE_TERMINAL fan-out
 * arms reference guards by string name — this is what lets
 * `gen-machine-mermaid.ts` annotate the diagram edges with the guard
 * that picks each final state. Inline lambda guards would be opaque
 * to the generator.
 */
import type { StreamingContext, StreamingEvent } from './streaming.machine';

type GuardArgs = { context: StreamingContext; event: StreamingEvent };

export const isSuccessStatus = ({ event }: GuardArgs) =>
  event.type === 'SSE_TERMINAL' && event.status === 'success';

export const isCancelledStatus = ({ event }: GuardArgs) =>
  event.type === 'SSE_TERMINAL' && event.status === 'cancelled';
