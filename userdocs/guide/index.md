<!-- generated from pm_mvp::docs/consumer/overview/what-is-workflow-canvas @ e703527cd426; do not edit -->

# What is Workflow Canvas?

## Why Workflow Canvas

Workflow Canvas, `wfc` for short, is a visual dry-lab notebook for reproducible analysis. `wfc` is also the command you type to use it. Your AI agent can turn your scripts and notebooks into a pipeline you can see, or you can write it yourself; either way, every result records the exact code, data, settings and software behind it, so anyone can rerun it and check that it matches.

If you run your own multi-step analysis in Python, R or bash, you know the failure mode: scripts multiply, and months later you can't say for certain which code, which inputs, and which environment produced a given figure or dataset — and neither can whoever inherits the project. Re-running redoes work that never changed. And the usual fix is its own burden: making your analysis reproducible and orchestrated this way normally means adopting infrastructure tools — a workflow engine and its language, containers, a provenance system — and taking on the job of learning and running them. That's a second job on top of the science.

**Workflow Canvas hands you that stack, pre-built.** You describe each analysis step once, with a contract saying what it takes in and what it produces, and Workflow Canvas runs the steps in order, each in its own container, skips work that hasn't changed, and records every run. You don't learn a workflow language or build the infrastructure yourself.

Most analysis folders hold things that make a result hard to reproduce: hard-coded paths, a random step with no seed, a threshold picked by eye, a helper shared between notebooks. [Convert an Analysis into a Pipeline](tutorials/convert-an-analysis.md) takes an example folder with all of these and turns it into a pipeline, step by step.

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

`wfc doctor` checks that git, Docker and your output archive are ready. Every line should say `OK` before you go on. `wfc demo` then spins up the exact pipeline shown above on your machine — see [A Tour of the Demo](tutorials/wfc-demo.md) for the tour. You'll need [Docker](https://docs.docker.com/get-started/get-docker/) running — every step executes in a container — plus Python 3.12 and git; [Install Workflow Canvas](install.md) sets up all three.


## Build it with an AI agent

The wfc plugin teaches Claude Code and Codex how Workflow Canvas works. Ask in plain words, such as *Turn scripts/filter.py into a wfc method that runs on my scores sample*, and the agent writes the step, tries it on your data as a draft, and shows you every change it made. It registers nothing until you agree.

Everything it builds appears in the Canvas: each step, what it reads and writes, its settings, and the results of every run, traced back to the code that made them. You can follow what the agent built without reading its code, and anyone who reads code can check it.

Add the plugin to Claude Code or Codex from a terminal:

```bash
# Claude Code
claude plugin marketplace add ddpoe/workflow-canvas
claude plugin install wfc@workflow-canvas

# Codex
codex plugin marketplace add ddpoe/workflow-canvas
codex plugin add wfc@workflow-canvas
```

For a chat on claude.ai or ChatGPT, which answers questions but can't run wfc, download wfc-plugin.zip and upload it. [Work with an AI Agent](tutorials/work-with-an-ai-agent.md) has the download, the steps for the Claude desktop app and other agents, and a first session.

## A rerunnable record of every result

Any result can be exported as one file that reruns on any computer with Python and Docker, without installing Workflow Canvas, and compares each output with the original. The file holds the samples the steps read and every step's code, parameters and environment, so the analysis stays something you can come back to and analyse again when new insights come along.

Send it to a collaborator, attach it to a paper for reviewers, or keep it as the record of a finished project. To try it, export one of the demo's results and rerun it: [Export a run and rerun it](tutorials/wfc-demo.md#export-a-run-and-rerun-it).

## Who it's for

Workflow Canvas is built for **researchers, scientists and PhD students who want their analysis organized, reproducible, and easy to pass on to others**. It runs locally, on a laptop or a lab workstation. One person can build pipelines for the whole lab to run from the Canvas. That can be a computational labmate, or, with an AI agent writing the code, anyone who has thought their analysis through. Each step needs one small file saying what it takes in and what it produces. There is no workflow language to learn and no server to stand up.

It is probably **not the right fit if** you run community tools without code of your own (Galaxy serves that better), or you run production pipelines at cluster scale (Nextflow with Seqera Platform serves that better).

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
- **The AI agent plugin** — skills that teach Claude Code and Codex to set up a project, bring your scripts in as methods, run them and explain failures. See [Work with an AI Agent](tutorials/work-with-an-ai-agent.md).

Two kinds of people use it. **Method authors** write the steps, by hand or with an AI agent, and curate the project's library of methods. **Pipeline users** drag those methods onto the canvas, pick among them, set parameters, and run — no code required to compose or run a pipeline.

## What you provide, and what you get

The deal is deliberately lopsided. **What you provide** is the raw material: your methods, written by you or your agent (each analysis step, with a contract for what it takes and returns), the environment each one runs in, and your data samples. **What you get back** is everything around them: your steps run in the right order, each in its own container; unchanged work is skipped; every run is recorded in a queryable lineage database; any result exports, with its samples, as one file that reruns and checks itself on another computer; and a visual **Canvas** that turns those methods into a no-code way to build and run whole workflows.


## Where to go next

- **[A Tour of the Demo](tutorials/wfc-demo.md)** — run `wfc demo` to explore a complete working pipeline before writing any code, then export one of its results as a zip that reruns on any computer with Docker, without wfc.
- **[Work with an AI Agent](tutorials/work-with-an-ai-agent.md)** — add the plugin, and let Claude Code or Codex set up a project and bring your scripts in as methods.
- **[Install Workflow Canvas](install.md)** — get Docker, git and wfc onto your computer.
- **[Your First Pipeline](tutorials/getting-started.md)** — take a project from empty to a running pipeline.
- **[Write a Method in Python](tutorials/authoring-a-method-script.md)** and **[Write a Method](how-to/write-a-method.md)** — turn your own analysis code into registered methods with contracts.
- **[Use the Canvas](how-to/canvas-hub.md)** — build and inspect pipelines visually instead of hand-editing pipeline JSON.

```{toctree}
:caption: Overview
:hidden:

self
```

```{toctree}
:caption: Installation
:maxdepth: 2
:hidden:

install
```

```{toctree}
:caption: Tutorials
:maxdepth: 2
:hidden:

tutorials/getting-started
tutorials/wfc-demo
tutorials/work-with-an-ai-agent
tutorials/convert-an-analysis
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
reference/known-issues
```

```{toctree}
:caption: Media
:maxdepth: 2
:hidden:

videos
```
