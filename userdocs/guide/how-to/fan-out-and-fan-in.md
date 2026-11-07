<!-- generated from pm_mvp::docs/consumer/how-to/fan-out-and-fan-in @ 8f024cd4f4ff; do not edit -->

# Fan Out and Fan In

## Fan-out and Fan-in

The **Settings** tab of an Input Selector decides how its samples flow through the pipeline.

**Fan-out (the default).** Each selected sample runs as its own branch: with four samples selected, every downstream step runs four times, once per sample. **Keep going on failure** is on by default, so one sample failing doesn't cancel the others; the node then shows a count of how many samples succeeded, such as `3/4 ✓`. Turn it off to stop the whole pipeline at the first failure.

**Fan-in.** Switch to **Fan-in** to bundle all the selected samples into a single run, for example to merge or compare them. The step the selector feeds receives every sample on one input slot and runs once, and every step downstream of it also runs once. In History, the run shows the samples it bundled.

Wire a fan-in selector straight into the step that combines the samples, and make it that step's only input. The pipeline is refused before it starts if a fan-in step also takes another input, or if a step below a fan-in step also takes per-sample outputs.

Variants and fan-out multiply: two samples fanned out into a step with three variants give six runs of that step.

In a pipeline file, an Input Selector's fan-out or fan-in is its `fan_mode`, `"out"` or `"in"`; see [Pipeline Document](../reference/pipeline-document.md).
