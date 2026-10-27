/**
 * History's words for why a cancelled run was cancelled. The two
 * engine-side reasons say wfc could not run the target: the server stopped
 * before it started (`interrupted`), or the engine failed (`engine_failed`).
 * Neither names a cause run.
 */
import { describe, it, expect } from 'vitest';
import { cancelReasonText } from '../historyUtils';
import type { CancelReason } from '../historyApi';

const cancelled = (cancelReason: CancelReason) => ({
  cancelReason,
  cancelledDueToRunId: null,
  cancelledDueToMethod: null,
  cancelledDueToSample: null,
});

describe('cancelReasonText for the engine-side reasons', () => {
  it('says the server stopped before an interrupted run started', () => {
    expect(cancelReasonText(cancelled('interrupted'))).toEqual({
      text: 'Interrupted: the server stopped before it started',
      cause: null,
    });
  });

  it('says the engine failed for an engine_failed run', () => {
    expect(cancelReasonText(cancelled('engine_failed'))).toEqual({
      text: 'Not run: the engine failed',
      cause: null,
    });
  });
});
