<!-- generated from pm_mvp::docs.consumer.how-to.sweep-parameters-and-fan-out @ 217a4477904e; do not edit -->

# How-to: Sweep Parameters & Fan Out

## Sweep parameters and fan out

This how-to covers three ways to run one pipeline across many inputs and many settings without hand-writing every combination:

- **Parameter sweeps, variants, and per-sample overrides** — try several values for a parameter, or give specific samples their own settings, and let the pipeline expand into one branch per combination.
- **Fan-out and fan-in** — run the same steps independently per sample (fan-out), or bundle several samples into a single collapsed run (fan-in).
- **Pipeline variables** — name a shared value once and bind it to many parameter rows, so you change it in one place.

All three are authored in the Canvas Builder and travel in the exported pipeline JSON, so a pipeline you build visually runs the same way from `wfc run-pipeline`. For the Builder and History UI mechanics referenced throughout, see [Canvas Visual Builder](../how-to/canvas.md). If you are new to building a pipeline at all, start with [Getting Started](../tutorials/getting-started.md).

## Parameter sweeps, variants & per-sample overrides

A **sweep** runs the same method several times with different parameter values and keeps each result separate. Instead of committing a single value for a parameter in the node inspector, you add **variants** — named alternative values for that parameter. Each variant becomes its own branch of the run, so a sweep over a parameter with three values produces three independent executions of that step (and of everything downstream of it).

**Authoring a sweep.** Open a method node's inspector and add variants to a parameter row. When one parameter carries variants, your variant names are used as-is so the pipeline round-trips cleanly; when several parameters on the same node carry variants, the branches are the cartesian product of all the picks (auto-named `v1`, `v2`, …). Sweeping two parameters with two values each yields four branches.

**Per-sample overrides.** Sometimes one sample needs different settings from the rest — a higher threshold, a different reference column. Instead of a global variant, you attach an override to a specific sample on that node. The override applies only to that sample; every other sample keeps the base parameters (or the swept variants).

**How this expands the DAG.** Sweeps and overrides are compiled into a `param_sets` block in the pipeline JSON. When you export or run, Canvas walks each method node and emits its variant rows. If *any* node anywhere in the pipeline has an override, the engine switches to selective mode, where the run list is exclusive: Canvas then emits the full sample × sweep-variant matrix *plus* the override rows, so no swept combination is silently dropped. If there are only sweeps and no overrides, the `param_sets` block carries the variants and the engine's normal cartesian expansion fills in the matrix. Either way, the result is one branch per (sample, variant) combination, and a per-sample override whose resolved parameters happen to match an existing sweep variant is de-duplicated rather than run twice.

The upshot: you describe the values you care about, and the pipeline fans into exactly the set of runs those values imply — each cached, tracked, and inspectable on its own.

## Fan-out and fan-in

**Fan-out (the default).** Pipeline inputs come from an **Input Selector** system node, where you pick one or more registered samples. By default the selector's `fan_mode` is `"out"`: every selected sample becomes an independent execution branch, and downstream steps receive the sample name as a wildcard. Selecting four samples runs the whole downstream chain four times, once per sample, in isolation. There is nothing extra to configure — fan-out is what you get from a normal multi-sample selector.

**Fan-in (`fan_mode = "in"`).** Sometimes you want the opposite: take several samples and feed them together into a single step — for example a merge or a cross-sample comparison. Switch the Input Selector to fan-in mode and all of its selected samples are bundled into one collapsed execution. The collapsed step (and every step downstream of it) uses a single bundled identity, `__all__`, in its output paths and run records rather than a per-sample wildcard.

**The single-selector rule.** A fan-in selector must feed exactly one direct consumer method node; the Builder rejects shapes where a fan-in selector fans into several steps at once. This keeps the collapse unambiguous: the bundle goes to one place, and collapse then propagates contagiously — every descendant of a collapsed step is itself collapsed, so once samples are bundled they stay bundled for the rest of the chain.

**Mixing fan-out and fan-in.** A pipeline can do both: fan out across samples for per-sample processing, then fan in to a collapsing step that pulls those per-sample outputs back together. This is the common shape for "process each replicate, then combine." For how the picker and `fan_mode` toggle appear in the Builder, see [Canvas Visual Builder](../how-to/canvas.md).

## Reusing values with pipeline variables

When the same value appears in many parameter rows — a shared label column, a condition column used by several methods — retyping it everywhere is error-prone. **Pipeline variables** let you name that value once and bind parameter rows to it.

**Creating a variable.** In the Builder, open the Pipeline Variables panel and add a variable with a short name, a type (`str`, `int`, `float`, `bool`, `list`, or `dict`), and a value. Variables are created only in this panel.

**Binding a row.** In a method node's inspector, click the bind (chain) icon on a parameter row and pick a variable from the dropdown. The row then shows a `→ varname` chip and displays the resolved value instead of an editable field. Only type-compatible variables are selectable. A bound row cannot also carry sweep variants or per-sample overrides — a value is either shared-by-variable or swept, not both. Clicking back into a bound row's input breaks the binding in one gesture and restores the last literal value as your starting draft.

**How binding travels.** In the exported pipeline JSON, a bound parameter is written as a `{"$var": "name"}` reference instead of a literal, and the named values live in a `variables` block. At submission time the server resolves these references to their literal values before the pipeline runs, so methods always receive concrete values — the variable indirection is purely an authoring convenience.

**History round-trip.** Because the pre-resolution form is saved alongside each run, you can reopen a past pipeline with the variables and chips intact. In the History tab, use **Open pipeline in Canvas** on a pipeline; the Builder rehydrates the Pipeline Variables panel and re-binds every bound row exactly as it was when you submitted — no retyping required. The shared `{$var}` mechanism was introduced in ADR-017; the full UI walkthrough lives in [Canvas Visual Builder](../how-to/canvas.md).

## Next steps

- **[Canvas Visual Builder](../how-to/canvas.md)** — the full Builder and History UI: the node palette, inspectors, the Pipeline Variables panel, and the **Open pipeline in Canvas** action used to rehydrate a past run.
- **[Getting Started](../tutorials/getting-started.md)** — build and run your first pipeline end to end if you have not yet.
- Run sweeps and fan-out pipelines from the command line with `wfc run-pipeline` on an exported pipeline JSON; see the CLI reference for the full command set.
- To trace which run produced which output across a fan-out or a collapsed fan-in, inspect lineage in the History tab.
