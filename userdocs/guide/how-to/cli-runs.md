<!-- generated from pm_mvp::docs/consumer/how-to/cli-runs @ d2893a75520d; do not edit -->

# See What Ran

## See what ran

When the pipeline ends, successfully or not, `wfc run-pipeline` prints a summary:

```text
============================================================
PIPELINE SUMMARY
============================================================
Pipeline ID: <pipeline-id>
Status: succeeded
Total runs: 6  |  Passed: 4  |  Failed: 0  |  Cached: 2  |  Cancelled: 0
```

`Status` is `succeeded`, `failed`, `cancelled`, or `mixed` when some runs succeeded and others did not. Every step the pipeline schedules, for each sample and each variant, becomes one **run** with a numeric run ID. `Cached` counts runs that reused an earlier result instead of executing. If any run failed, the summary adds a `FAILED RUNS:` list with each failed run's ID, sample and error, and the command ends with a single `ERROR:` line and exit code 1. A run that never started, for example because a step it needed failed, counts as cancelled.

To list the runs later, use `wfc runs`. It prints one line per run with its run ID, pipeline, node, method and version, sample and status; `wfc runs --pipeline <pipeline-id>` lists only that pipeline's runs, and `wfc runs --method <module>.<method>` only one method's runs.

To read one run in full, use `wfc show-run --run <run id>`. It prints the run's status, method, sample, metrics, outputs, error and the last 40 lines of its stderr, so you can read why a past run failed without opening the canvas. `wfc show-run --run <run id> --log stderr` prints the whole log.

Each run records the values it ran with, what it read, and what it produced. The History tab in the Canvas shows all of this for every run, including runs started from the command line, and its detail panel has a button to copy a run's ID for `wfc export`; see [Browse Past Runs in History](browse-history.md).

When the pipeline finishes, wfc moves each output into the project's read-only cache. `wfc export` copies an output out; an output kept only in the archive is fetched back when you ask for it. See [Storage & Provenance](../explanation/storage-and-provenance.md) for where outputs live.
