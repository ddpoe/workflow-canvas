<!-- generated from pm_mvp::docs/consumer/how-to/sweep-values @ b1ea6ee0f1a9; do not edit -->

# Sweep Values and Override Samples

## Variants and Per-Sample Values

A **sweep** runs a step several times with different values for a parameter. Each value is a **variant**, and each variant runs as its own branch, so the step and everything downstream of it run once per variant.

**Add variants.** In the node's Inspector, click **+ variant** under a parameter to add another value. `list` and `dict` parameters can't be swept. If only one parameter on the node has variants, each variant keeps its own name. If several parameters have variants, the node runs every combination of them, named `v1`, `v2`, and so on.

**Override one sample.** Open the **Per Sample** sub-tab next to **All Samples** and pick a sample. Any value you set there, including variants, applies to that sample only; every other sample keeps the values from **All Samples**. A ● marks samples and parameters that have an override, and **Clear override** removes it. The sub-tab lists the samples of the Input Selector wired into the pipeline.

The Runs Preview shows one row per sample and variant, so you can check the combinations before you run (see [Check and Run a Pipeline](check-and-run.md)).
