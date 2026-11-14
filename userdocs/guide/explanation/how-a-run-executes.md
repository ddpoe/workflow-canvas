<!-- generated from pm_mvp::docs/consumer/explanation/how-a-run-executes @ a108203e80b4; do not edit -->

# How a Run Executes

## How a run executes

This page follows a pipeline from the moment you click **Run** in the canvas (or call `wfc run-pipeline`) to the moment its outputs are in the cache. Three ideas cover it:

1. **Snakemake orders the work.** wfc hands your pipeline to Snakemake, a workflow engine, which decides the order of steps and runs independent ones in parallel.
2. **Every step runs the same sequence.** A step checks the cache, gathers its inputs, runs your method in its container (a packaged copy of the software and dependencies the method needs; see [The Tools Behind Workflow Canvas](how-the-pieces-fit-together.md)), and records what it produced.
3. **Your method talks to wfc through environment variables and files.** wfc tells the method where its inputs are and where to write; the method writes files and exits. That is why a method can be written in any language.

To write the method that runs inside a step, see [Write a Method in Python](../tutorials/authoring-a-method-script.md).


## Snakemake is the orchestrator

When you launch a pipeline, wfc saves a copy of it and hands it to Snakemake. Each node runs once per sample, so a pipeline drawn once over five samples becomes five steps per node.

`wfc run-pipeline --cores N` (default 4) sets how many steps, and so how many containers, run at once.

If a step fails, Snakemake starts no new steps, lets running ones finish, and the pipeline fails. With `--keep-going`, steps that do not depend on the failed one still run. Either way, every step that never ran is recorded as **cancelled** and linked to the failed step that blocked it, so the run history shows what happened to every node. Cancelling a pipeline from the canvas stops the running steps and records them as cancelled.


## What each step does

Every step runs the same sequence:

1. **Check the cache.** wfc compares the method's code, parameters, inputs and environment with earlier runs. If a completed run matches, the step reuses its outputs and the method does not run. [Caching & Reproducibility](caching-and-reproducibility.md) covers this in detail.
2. **Gather inputs.** wfc resolves each input slot to files: the outputs of the upstream runs, or the restored sample.
3. **Run the method in its container.** wfc starts a container from the method's environment, with the project at `/work`, and runs the registered copy of your script. What the method prints is saved in the run's logs.
4. **Collect outputs.** When the method exits 0, wfc finds each declared output in the run's directory. A non-zero exit, or a declared output that is missing, fails the step with the error from the method's stderr.
5. **Record.** wfc marks the run completed or failed, records its outputs and metrics, and links it to the runs it read.

After the whole pipeline succeeds, wfc stores every new output in the project's cache and backs it up to the archive. `wfc run-pipeline --no-archive` skips that pass; `wfc cache archive` runs it later. [Storage & Provenance](storage-and-provenance.md) explains where the bytes end up.


## What your method receives and returns

wfc passes a step's context to your method in environment variables:

| Variable | Meaning |
|---|---|
| `WFC_RUN_DIR` | Directory to write your declared outputs into. |
| `WFC_INPUT_PATHS` | JSON `{slot: [paths]}`: the input files for each input slot. |
| `WFC_PARAMS` | JSON `{name: value}`: the node's parameter values. A parameter left unset arrives at the default `method.yaml` declares, and the run records it. |

Paths in these variables are paths inside the container.

Your method reads them, writes each declared output into `WFC_RUN_DIR`, and exits 0 on success or non-zero on failure. wfc finds the outputs by the file names your `method.yaml` declares. A method can also write a `_wfc_results.json` file into `WFC_RUN_DIR` to name its outputs and report metrics.

For Python, the optional `wfc-client` package, a separate helper library, does this for you: decorate a function with `@wfc.method`, use `ctx.input(...)`, `ctx.params`, `ctx.save_artifact(...)` and `ctx.log_metric(...)`, and call `wfc.run()`; it writes `_wfc_results.json` on exit. A script in any language, Python included, can instead read the variables and write the files directly, so a method does not need to import wfc. [Write a Method in Python](../tutorials/authoring-a-method-script.md) covers the decorator, the [Method Script Contract](../reference/method-script-contract.md) lists the variables and files, and [Write a Method in R](../how-to/write-an-r-method.md) writes a method in R.


## Where to go next

- [Write a Method in Python](../tutorials/authoring-a-method-script.md) — write the code that runs inside a step.
- [Caching & Reproducibility](caching-and-reproducibility.md) — why a step re-runs or reuses an earlier result.
- [Look at a Run's Results](../how-to/run-details.md) — find a run's outputs, status, logs and history in the Canvas.
- [Use the Command Line](../how-to/cli-hub.md) — run a pipeline and export its outputs with `wfc`.
- [Project Anatomy](project-anatomy.md) — where each of these files lives in the project.
- [Your First Pipeline](../tutorials/getting-started.md) — run a pipeline end to end.
