/**
 * History's "Watch in canvas": put a running pipeline on the canvas as if
 * the user had never left the builder.
 *
 * Loads the pipeline's editable document through the same route "Open in
 * canvas" uses, then attaches the pipeline machine to it (`dispatchAttach`):
 * node statuses come from the status route on the poller's first tick, a
 * running node streams its log, and the Toolbar shows Stop. The status
 * route answers for any live pipeline, so this works for canvas and CLI
 * pipelines alike, and after a server restart.
 *
 * When the canvas is already watching (or ran) another pipeline that is
 * still running, the user is asked first; on yes the canvas switches and
 * the other pipeline keeps running, reachable again from History. Either
 * way, a canvas with nodes on it is replaced only after the usual
 * replace-the-canvas question.
 */
import { get } from 'svelte/store';
import { fetchPipelineDocument } from './historyApi.js';
import { confirmReplaceIfDirty, loadPipeline } from '../builder/pipeline.js';
import { pipelineName, runState, showFlash } from '../builder/stores.js';
import { confirmDialogState } from '../shared/uiState.js';
import { dispatchAttach, pipelineRunActor } from '../machines/root.js';

/** Ask whether to stop watching `current` and watch `target` instead. */
function confirmSwitchWatch(current: string, target: string): Promise<boolean> {
  return new Promise<boolean>(resolve => {
    confirmDialogState.set({
      variant: 'switch-watch',
      currentName: current,
      targetName: target,
      resolve: (proceed: boolean) => {
        confirmDialogState.set(null);
        resolve(proceed);
      },
    });
  });
}

/**
 * Watch the running pipeline `pipelineId` in the canvas.
 *
 * Args:
 *   pipelineId: The pipeline's id (also its job id on the status route).
 *   label: The pipeline's name, for the dialog and the flash.
 *
 * Returns:
 *   True when the canvas now watches the pipeline; false when the user
 *   declined, the canvas was mid-submit, or the document could not load.
 */
export async function watchPipelineInCanvas(
  pipelineId: string,
  label: string,
): Promise<boolean> {
  const current = pipelineRunActor.getSnapshot().context.jobId;
  if (get(runState).running) {
    if (current === pipelineId) {
      showFlash(`Already watching ${label}`, 'info');
      return true;
    }
    if (!current) {
      // Preflight or submit: the canvas's own run has no id to come back to yet.
      showFlash('The canvas is starting a run; try again once it has started.', 'error');
      return false;
    }
    const currentLabel = get(pipelineName) || current;
    if (!(await confirmSwitchWatch(currentLabel, label))) return false;
  }
  // Switching or not, the load replaces the canvas: unsaved edits are asked about.
  if (!(await confirmReplaceIfDirty(label))) return false;
  let json;
  try {
    json = await fetchPipelineDocument(pipelineId);
  } catch (err) {
    const code = err instanceof Error ? err.message : String(err);
    showFlash(
      code === 'PIPELINE_DOCUMENT_NOT_FOUND'
        ? 'Pipeline document not available — this pipeline never reached the run-generation stage.'
        : `Failed to load pipeline: ${code}`,
      'error',
    );
    return false;
  }
  // The load resets the pipeline machine; attach once that reset has landed.
  await loadPipeline(json);
  dispatchAttach(pipelineId, json);
  showFlash(`Watching ${label} in canvas`, 'success');
  return true;
}
