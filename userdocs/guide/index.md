<!-- generated from pm_mvp::docs/consumer/overview/what-is-workflow-canvas @ 97227f58aac0; do not edit -->

# What is Workflow Canvas?

## Why Workflow Canvas

If you run your own multi-step analysis in Python, you know the failure mode: scripts multiply, and months later you can't say for certain which code, which inputs, and which environment produced a given figure or dataset — and neither can whoever inherits the project. Re-running redoes work that never changed. And the usual fix is its own burden: making your analysis reproducible and orchestrated this way normally means adopting infrastructure tools — a workflow engine and its language, containers, a provenance system — and taking on the job of learning and running them. That's a second job on top of the science.

:::{figure} _images/builder-demo-pipeline.png
:alt: The Canvas Builder with the demo pipeline wired: three samples fanned across five methods, coloured by their three modules

The **Builder** tab, with the demo pipeline wired. The other two tabs in the top bar: **Registry** — every registered method, its code, and its container envs — and **History** — past runs, their lineages, and artifacts.
:::

```bash
pip install workflow-canvas
wfc init
wfc doctor
wfc demo
```

`wfc doctor` checks that git, Docker and your output archive are ready. Every line should say `OK` before you go on. `wfc demo` then spins up the exact pipeline shown above on your machine — see [A Tour of the Demo](tutorials/wfc-demo.md) for the tour. You'll need [Docker](https://docs.docker.com/get-started/get-docker/) running — every step executes in a container — plus Python 3.12 and git; see [Installation](tutorials/getting-started.md#installation) for the details.

**Workflow Canvas hands you that stack, pre-built.** You describe each analysis step once — a contract saying what it takes in and what it produces — and Workflow Canvas takes it from there: it orchestrates the runs, isolates each step in its own container, skips work that hasn't changed, and records every run in a queryable database you can trace any result back through. You get workflow orchestration and a full provenance layer without learning a workflow language or building the infrastructure yourself — that engineering is part of what Workflow Canvas brings.
Methods can be written in Python, R or bash, with the same contract in each, and each runs in the container environment it names.


## A rerunnable record of every result

Any result can be exported as one file that reruns on any computer with Python and Docker, without installing Workflow Canvas, and compares each output with the original. The file holds the samples the steps read and every step's code, parameters and environment, so the analysis stays something you can come back to and analyse again when new insights come along.

Send it to a collaborator, attach it to a paper for reviewers, or keep it as the record of a finished project. To try it, export one of the demo's results and rerun it: [Export a run and rerun it](tutorials/wfc-demo.md#export-a-run-and-rerun-it).

## Who it's for

Workflow Canvas is built for **small computational-biology and data-analysis labs (roughly 1–10 people) that write their own Python analysis code** — where there is no dedicated pipeline engineer, no cluster is assumed, and the whole thing needs to run on a laptop. The cost to adopt is deliberately low: a contract per method, and nothing heavier — no new workflow language to learn, no tools to wrap in XML, no server to stand up.

It is probably **not the right fit if** you run pre-wrapped community tools without writing any analysis code of your own (Galaxy serves that better), or you operate production, cluster-scale genomics pipelines (Nextflow with Seqera/Tower serves that better). Workflow Canvas is for the analyst who just wants their own Python analysis organized, reproducible, and easy to hand off.

## The pieces, and how they fit

You don't need the internals to get started — here is the whole mental model in one place, each piece pointing to the page that explains it in depth.

- **Methods and modules** — a *method* is one analysis step (a Python, R or bash script with a small contract). A *module* is a named group of methods that share an output contract, so your project's analysis library stays organized. See [Write a Method in Python](tutorials/authoring-a-method-script.md) and [Write a Method](how-to/write-a-method.md).
- **Drafts** — before you register a method, run its folder as a draft, from the command line or in the Canvas. Nothing a draft does is recorded or cached, so you can change it and run it again until it works, then register it once. See [Draft a Method](tutorials/drafting-a-method.md).
- **Contracts** — each method declares what it needs and what it produces, so wiring mistakes and missing outputs are caught *before* a long run starts, not after. See [Write a Method](how-to/write-a-method.md) and the [method.yaml Schema](reference/method-yaml-schema.md) reference.
- **Runs and lineage** — every run is recorded, so you can trace any output back through the full chain of steps, code, and parameters that produced it. See [How a Run Executes](explanation/how-a-run-executes.md) and [Storage & Provenance](explanation/storage-and-provenance.md).
- **Caching** — a step whose code, inputs, and parameters are unchanged is skipped automatically; change one thing and only what it affects re-runs. See [Caching & Reproducibility](explanation/caching-and-reproducibility.md).
- **Environments** — each method runs in its own container, so results don't drift with whatever happens to be installed on your laptop. See [Register an Environment](tutorials/registering-an-environment.md).
- **Rerun bundles** — export any run, with every run that led to it and the samples they read, as one zip that reruns on another computer and checks each output against yours. See [Share Runs as a Bundle Anyone Can Rerun](how-to/share-bundle.md).
- **The Canvas** — a browser interface with three tabs: **Builder** to drag and wire method nodes into a pipeline, **Registry** to browse everything registered (methods and their code, envs, samples), and **History** to trace runs, lineages, and artifacts. See [Use the Canvas](how-to/canvas-hub.md).

Two kinds of people use it. **Method authors** write the Python and curate the project's library of methods. **Pipeline users** drag those methods onto the canvas, pick among them, set parameters, and run — no code required to compose or run a pipeline.

## What you provide, and what you get

The deal is deliberately lopsided. **What you provide** is the raw material: your methods (each analysis step, with a contract for what it takes and returns), the environment each one runs in, and your data samples. **What you get back** is everything around them: your steps run in the right order, each in its own container; unchanged work is skipped; every run is recorded in a queryable lineage database; any result exports, with its samples, as one file that reruns and checks itself on another computer; and a visual **Canvas** that turns those methods into a no-code way to build and run whole workflows.


## Where to go next

- **[A Tour of the Demo](tutorials/wfc-demo.md)** — run `wfc demo` to explore a complete working pipeline before writing any code, then export one of its results as a zip that reruns on any computer with Docker, without wfc.
- **[Your First Pipeline](tutorials/getting-started.md)** — install Workflow Canvas and take a project from empty to a running pipeline.
- **[Write a Method in Python](tutorials/authoring-a-method-script.md)** and **[Write a Method](how-to/write-a-method.md)** — turn your own analysis code into registered methods with contracts.
- **[Use the Canvas](how-to/canvas-hub.md)** — build and inspect pipelines visually instead of hand-editing pipeline JSON.

```{toctree}
:caption: Overview
:hidden:

self
```

```{toctree}
:caption: Tutorials
:maxdepth: 2
:hidden:

tutorials/getting-started
tutorials/wfc-demo
tutorials/work-with-an-ai-agent
```

```{toctree}
:caption: How-to Guides
:maxdepth: 2
:hidden:

how-to/build-your-own-pipeline
how-to/run-and-inspect
how-to/sharing-a-project
```

```{toctree}
:caption: Explanation
:maxdepth: 2
:hidden:

explanation/how-the-pieces-fit-together
explanation/project-anatomy
explanation/how-a-run-executes
explanation/storage-and-provenance
explanation/caching-and-reproducibility
```

```{toctree}
:caption: Reference
:maxdepth: 2
:hidden:

reference/command-line
reference/method-yaml-schema
reference/method-script-contract
reference/pipeline-document
reference/wf-canvas-toml
reference/wfc-client-api
```
