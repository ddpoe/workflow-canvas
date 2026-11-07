<!-- generated from pm_mvp::docs/consumer/explanation/caching-and-reproducibility @ 97ecf9b12526; do not edit -->

# Caching & Reproducibility

## Caching & Reproducibility

Workflow Canvas records enough about every step to know when it has already computed a result, and to trace any result back to what produced it. Two ideas cover this:

- The **cache key** decides whether a step runs or reuses an earlier result.
- **Lineage** links each run to the runs and samples it consumed.

To find and read a run's outputs, use the History tab ([Browse Past Runs in History](../how-to/browse-history.md)) or `wfc export` ([Export a Run's Outputs](../how-to/cli-export.md)). To hand a result to someone who can check it, `wfc export-run` packages runs, with the samples they read, as a zip they can rerun with `python rerun.py` ([Share Runs as a Bundle Anyone Can Rerun](../how-to/share-bundle.md)).

## When a step is reused (the cache key)

Before a step runs, wfc computes its **cache key**, a fingerprint of everything that determines the result:

- **Code**: the method's scripts (including any `helpers:` listed in `method.yaml`) and its declarations in `method.yaml`: input and output slots and parameters. Descriptions, formatting, reordering, comments, the `env:` line and `helpers:` do not change it (the env is its own ingredient, below).
- **Parameters**: the step's parameter values, including the declared defaults of parameters left unset. Leaving a parameter unset and writing its default give the same key.
- **Inputs**: what feeds each input slot, either the cache key of the upstream run or the content of the sample file.
- **Environment**: the contents of the container image the method's `env` resolves to, that is, the packaged copy of the software and dependencies the method runs with, not its name or where it is stored (see [The Tools Behind Workflow Canvas](how-the-pieces-fit-together.md)).
- **Method**: which module and method this is.

If an earlier completed run of the same method on the same sample has the same key, and its outputs can still be read (from this machine or from the archive), the step is **reused**: no code runs, and the step's outputs are the earlier run's. Otherwise the step runs.

Because each step's inputs include its upstream step's key, a change re-runs only its own step and the steps after it: edit and re-register one method, or change one parameter, and everything else is reused. Rebuilding an environment changes nothing until you register the methods that use it again; `wfc register-env` lists them.

In the Canvas, **Lock All** shows in the Runs Preview which steps will be reused and which will run, before you run. A reused step shows a `cached` badge after the run.

For where reused outputs are stored, see [Storage & Provenance](storage-and-provenance.md).


## Lineage: tracing how a result was produced

Every run records what it consumed: each upstream run that fed one of its input slots, and each sample it read. Following those links backwards from any run gives its **lineage**, the chain of runs back to the registered samples it started from. A step that merges several inputs has several parents.

A reused step is recorded too, pointing at the earlier run whose outputs it reused, so you can see which original run supplied each result.

Together with the cache key, this answers "which inputs, code, parameters and environment produced this file?" for any output.

To see it, open a run in the Canvas History tab. The Lineages view lays out each run's path from its samples, and **Open lineage in Canvas** in the run's detail panel rebuilds its ancestors as an editable pipeline, wired to the same slots, that reuses the existing results when you run it.

A `DIFFERENT` is a difference in bytes, which is not always a different result; see [If a step says DIFFERENT](../how-to/share-bundle.md#if-a-step-says-different).


## Next steps

- [Browse Past Runs in History](../how-to/browse-history.md): find a run in the History tab and read its outputs.
- [Use the Command Line](../how-to/cli-hub.md): run a pipeline and export its outputs with `wfc`.
- [Storage & Provenance](storage-and-provenance.md): where output files live, the archive, and what to back up.
- [How a Run Executes](how-a-run-executes.md): how a single run is scheduled and executed.
- [Your First Pipeline](../tutorials/getting-started.md): build your first pipeline.
