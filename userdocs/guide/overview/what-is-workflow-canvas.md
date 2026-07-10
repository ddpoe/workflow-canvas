<!-- generated from pm_mvp::docs.consumer.overview.what-is-workflow-canvas @ 5963dc02b568; do not edit -->

# What is Workflow Canvas?

## What Workflow Canvas is

If you run your own multi-step analysis in Python, you know the failure mode: scripts multiply, and months later you can't say for certain which code, which inputs, and which environment produced a given figure or dataset — and neither can whoever inherits the project. Re-running redoes work that never changed. And the usual fix is its own burden: making your analysis reproducible and orchestrated this way normally means adopting infrastructure tools — a workflow engine and its language, containers, a provenance system — and taking on the job of learning and running them. That's a second job on top of the science.

**Workflow Canvas hands you that stack, pre-built.** You describe each analysis step once — a contract saying what it takes in and what it produces — and Workflow Canvas takes it from there: it orchestrates the runs, isolates each step in its own container, skips work that hasn't changed, and records every run in a queryable database you can trace any result back through. You get workflow orchestration and a full provenance layer without learning a workflow language or building the infrastructure yourself — that engineering is part of what Workflow Canvas brings.

## Who it's for

Workflow Canvas is built for **small computational-biology and data-analysis labs (roughly 1–10 people) that write their own Python analysis code** — where there is no dedicated pipeline engineer, no cluster is assumed, and the whole thing needs to run on a laptop. The cost to adopt is deliberately low: a contract per method, and nothing heavier — no new workflow language to learn, no tools to wrap in XML, no server to stand up.

It is probably **not the right fit if** you run pre-wrapped community tools without writing any analysis code of your own (Galaxy serves that better), or you operate production, cluster-scale genomics pipelines (Nextflow with Seqera/Tower serves that better). Workflow Canvas is for the analyst who just wants their own Python analysis organized, reproducible, and easy to hand off.

## The pieces, and how they fit

You don't need the internals to get started — here is the whole mental model in one place, each piece pointing to the page that explains it in depth.

- **Methods and modules** — a *method* is one analysis step (a Python function with a small contract). A *module* is a named group of methods that share an output contract, so your project's analysis library stays organized. See [Authoring a Method Script](../tutorials/authoring-a-method-script.md) and [Writing Contracts](../tutorials/writing-contracts.md).
- **Contracts** — each method declares what it needs and what it produces, so wiring mistakes and missing outputs are caught *before* a long run starts, not after. See [Writing Contracts](../tutorials/writing-contracts.md) and the <a href="../reference/reference/method-yaml-schema.html">method.yaml Schema</a> reference.
- **Runs and lineage** — every run is recorded, so you can trace any output back through the full chain of steps, code, and parameters that produced it. See [How a Run Executes](../explanation/how-a-run-executes.md) and [Storage & Provenance](../explanation/storage-and-provenance.md).
- **Caching** — a step whose code, inputs, and parameters are unchanged is skipped automatically; change one thing and only what it affects re-runs. See [Caching & Reproducibility](../explanation/caching-and-reproducibility.md).
- **Environments** — each method runs in its own container, so results don't drift with whatever happens to be installed on your laptop. See [Registering an Environment](../tutorials/registering-an-environment.md).
- **The Canvas** — a browser interface to build and run pipelines with no code: drag and wire method nodes, browse run history, and inspect everything in the project. See [Canvas Visual Builder](../how-to/canvas.md).

Two kinds of people use it. **Method authors** write the Python and curate the project's library of methods. **Pipeline users** drag those methods onto the canvas, pick among them, set parameters, and run — no code required to compose or run a pipeline.

## What you provide, and what you get

The deal is deliberately lopsided. **What you provide** is the raw material: your methods (each analysis step, with a contract for what it takes and returns), the environment each one runs in, and your data samples. **What you get back** is everything around them: your steps run in the right order, each in its own container; unchanged work is skipped; every run is recorded in a queryable lineage database; and — the part most people feel first — a visual **Canvas** that turns those methods into a no-code way to build and run whole workflows. None of it asks you to learn a workflow language or manage the infrastructure by hand.

## Where to go next

- **[Getting Started](../tutorials/getting-started.md)** — install Workflow Canvas and take a project from empty to a running pipeline.
- **[Authoring a Method Script](../tutorials/authoring-a-method-script.md)** and **[Writing Contracts](../tutorials/writing-contracts.md)** — turn your own analysis code into registered methods with contracts.
- **[Canvas Visual Builder](../how-to/canvas.md)** — build and inspect pipelines visually instead of hand-editing pipeline JSON.
