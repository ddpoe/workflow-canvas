<!-- generated from pm_mvp::docs.consumer.explanation.how-a-run-executes @ 0b9fcc8fcdcd; do not edit -->

# How a Run Executes

## How a run executes

This page follows a pipeline from the moment you click **Run** in the canvas (or call `wfc run-pipeline`) to the moment its outputs are in the cache. Three ideas cover it:

1. **Snakemake orders the work.** wfc turns your pipeline into a Snakefile and runs Snakemake on it, which decides the order of steps and runs independent ones in parallel.
2. **Each step is one `wfc run-step` call.** A step checks the cache, gathers its inputs, runs your method in its container (a packaged copy of the software and dependencies the method needs; see [The Tools Behind Workflow Canvas](how-the-pieces-fit-together.md)), and records what it produced.
3. **Your method talks to wfc through environment variables and files.** wfc tells the method where its inputs are and where to write; the method writes files and exits. That is why a method can be written in any language.

To write the method that runs inside a step, see [Authoring a Method Script](../tutorials/authoring-a-method-script.md).

## Snakemake is the orchestrator

When you launch a pipeline, wfc freezes a copy of it, writes a Snakefile from it to `.runs/pipelines/<pipeline id>/Snakefile`, and runs Snakemake on that file. The Snakefile has one rule per node, expanded over every sample and parameter variant, so a pipeline drawn once over five samples becomes five steps per node. A rule's only job is to call `wfc run-step` for its node, sample and variant. When a pipeline starts from registered samples, a step before the first node restores each sample into `data/samples/<name>/`.

The Snakefile is regenerated on every launch; to change what runs, change the pipeline. `wfc run-pipeline --cores N` (default 4) sets how many steps, and so how many containers, run at once.

If a step fails, Snakemake starts no new steps, lets running ones finish, and the pipeline fails. With `--keep-going`, steps that do not depend on the failed one still run. Either way, every step that never ran is recorded as **cancelled** and linked to the failed step that blocked it, so the run history shows what happened to every node. Cancelling a pipeline from the canvas stops the running steps and records them as cancelled.

## What each step does

Every step runs the same sequence inside `wfc run-step`:

1. **Check the cache.** wfc computes the step's cache key from the method's registered code, its parameters, its inputs (the upstream results or the sample's content), and its environment. If a completed run already has that key, wfc records a new run that points at the earlier one and skips the method; downstream steps read the earlier run's outputs. Otherwise it records a new run with status *running*. [Caching & Reproducibility](caching-and-reproducibility.md) covers this in detail.
2. **Gather inputs.** wfc resolves each input slot to files: the outputs of the upstream runs, or the restored sample.
3. **Run the method in its container.** The method's `method.yaml` names an environment, which wfc resolves to the image you built with `wfc register-env`. wfc starts a Docker container from that image with the project mounted at `/work`, and runs your script with the environment's interpreter. The method's output goes to `stdout.log` and `stderr.log` in the run's directory. Docker must be running.
4. **Collect outputs.** When the method exits 0, wfc finds each declared output in the run's directory. A non-zero exit, or a declared output that is missing, fails the step with the error from the method's stderr.
5. **Record.** wfc marks the run completed or failed, records its outputs and metrics, and links it to the runs it read.

After the whole pipeline succeeds, the archive pass hashes every new output into the DVC cache and wfc pushes it to the archive location. `wfc run-pipeline --no-archive` skips that pass; `wfc cache archive` runs it later. [Storage & Provenance](storage-and-provenance.md) explains where the bytes end up.

## What your method receives and returns

wfc passes a step's context to your method in environment variables:

| Variable | Meaning |
|---|---|
| `WFC_RUN_DIR` | Directory to write your declared outputs into. |
| `WFC_INPUT_PATHS` | JSON `{slot: [paths]}`: the input files for each input slot. |
| `WFC_PARAMS` | JSON `{name: value}`: the parameter values set on the pipeline node. |
| `WFC_RUN_ID`, `WFC_SAMPLE`, `WFC_NODE_ID`, `WFC_PIPELINE_ID`, `WFC_VARIANT` | Identifiers for this run. |

Paths in these variables are paths inside the container.

Your method reads them, writes each declared output into `WFC_RUN_DIR`, and exits 0 on success or non-zero on failure. wfc finds the outputs by the file names your `method.yaml` declares. A method can also write a `_wfc_results.json` file into `WFC_RUN_DIR` to name its outputs and report metrics.

For Python, the `wfc-client` package does this for you: decorate a function with `@wfc.method`, use `ctx.input(...)`, `ctx.params`, `ctx.save_artifact(...)` and `ctx.log_metric(...)`, and call `wfc.run()`; it writes `_wfc_results.json` on exit. In R, bash or any other language, read the variables and write the files directly. [Authoring a Method Script](../tutorials/authoring-a-method-script.md) covers both.

## Where to go next

- [Authoring a Method Script](../tutorials/authoring-a-method-script.md) — write the code that runs inside a step.
- [Caching & Reproducibility](caching-and-reproducibility.md) — why a step re-runs or reuses an earlier result.
- [Build, Run and Inspect in the Canvas](../how-to/canvas.md) — find a run's outputs, status, logs and history in the Canvas.
- [Run and Inspect from the Command Line](../how-to/run-and-inspect-results.md) — run a pipeline and export its outputs with `wfc`.
- [Project Anatomy](project-anatomy.md) — where each of these files lives in the project.
- [Getting Started](../tutorials/getting-started.md) — run a pipeline end to end.
