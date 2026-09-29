<!-- generated from pm_mvp::docs.consumer.explanation.caching-and-reproducibility @ 5a6cf3167911; do not edit -->

# Caching & Reproducibility

## Caching & Reproducibility

Workflow Canvas records enough about every step to know when it has already computed a result, and to trace any result back to what produced it. Two ideas cover this:

- The **cache key** decides whether a step runs or reuses an earlier result.
- **Lineage** links each run to the runs and samples it consumed.

To find and read a run's outputs, use the History tab ([Build, Run and Inspect in the Canvas](../how-to/canvas.md)) or `wfc export` ([Run and Inspect from the Command Line](../how-to/run-and-inspect-results.md)).

## When a step is reused (the cache key)

Before a step runs, wfc computes its **cache key**, a fingerprint of everything that determines the result:

- **Code**: the method's scripts (including any `helpers:` listed in `method.yaml`) and the input and output slots it declares there. Reordering or commenting `method.yaml` does not change it.
- **Parameters**: the step's parameter values.
- **Inputs**: what feeds each input slot, either the cache key of the upstream run or the content of the sample file.
- **Environment**: the exact container image the method's `env` resolves to, that is, the packaged copy of the software and dependencies the method runs with (see [The Tools Behind Workflow Canvas](how-the-pieces-fit-together.md)).
- **Method**: which module and method this is.

If an earlier completed run of the same method on the same sample has the same key, and its outputs can still be read (from this machine or from the archive), the step is **reused**: no code runs, and the step's outputs are the earlier run's. Otherwise the step runs.

Because each step's input fingerprint includes its upstream's cache key, a change reaches only its own step and the steps downstream of it. Edit one method and register it again, change one parameter, or re-register one environment with a new image, and that step and the steps after it re-run; everything else is reused. Some things never cause a re-run: a git commit that does not touch the method's files, re-registering an unchanged sample file, or wiring the same samples into a fan-in selector in a different order.

In the Canvas, **Lock All** shows each step's cache status in the Runs Preview before you run: reused from the local cache, reused from the archive, re-run because the step changed, or re-run because an upstream re-runs. A reused step shows a `cached` badge after the run.

For where reused outputs are stored, see [Storage & Provenance](storage-and-provenance.md).

## Lineage: tracing how a result was produced

Every run records what it consumed: each upstream run that fed one of its input slots, and each sample it read. Following those links backwards from any run gives its **lineage**, the chain of runs back to the registered samples it started from. A step that merges several inputs has several parents.

A reused step is recorded too. When a step is served from the cache, wfc adds a run for it that points at the earlier run whose outputs it reused, so a re-run where every step was cached still has its own complete lineage, and you can see which original run supplied each result.

Together with the cache key, this answers "which inputs, code, parameters and environment produced this file?" for any output.

To see it, open a run in the Canvas History tab. The Lineages view lays out each run's path from its samples, and **Open lineage in Canvas** in the run's detail panel rebuilds its ancestors as an editable pipeline, wired to the same slots, that reuses the existing results when you run it.

## Next steps

- [Build, Run and Inspect in the Canvas](../how-to/canvas.md): find a run in the History tab and read its outputs.
- [Run and Inspect from the Command Line](../how-to/run-and-inspect-results.md): run a pipeline and export its outputs with `wfc`.
- [Storage & Provenance](storage-and-provenance.md): where output files live, the archive, and what to back up.
- [How a Run Executes](how-a-run-executes.md): how a single run is scheduled and executed.
- [Getting Started](../tutorials/getting-started.md): build your first pipeline.
