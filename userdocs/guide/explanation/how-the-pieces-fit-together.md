<!-- generated from pm_mvp::docs/consumer/explanation/how-the-pieces-fit-together @ 288fbbfc78ab; do not edit -->

# The Tools Behind Workflow Canvas

## What a reproducible analysis needs

An analysis is reproducible when someone else, or you a year from now, can see exactly how a result was made and make it again. That takes five things: the steps and their order, the code each step ran, the data it read, the software and dependencies it ran with, and a record tying them together for every run.

Each of these has a well-established open-source tool. Workflow Canvas brings them together behind the Canvas and the `wfc` command, so you get all five without setting up or learning each tool yourself.

## Snakemake: the pipeline

[Snakemake](https://snakemake.readthedocs.io/) is a workflow engine: it works out which steps depend on which, and runs them in the right order, in parallel where it can. When you run a pipeline from the Canvas, Workflow Canvas writes the pipeline out for Snakemake and lets it drive the execution. You never write Snakemake rules yourself. See [How a Run Executes](how-a-run-executes.md).

## git: the code

[git](https://git-scm.com/) keeps the history of your project's files. Registering a method commits its code to your project's git repository, and every run records the commit it ran from. Runs start from committed code, so the code behind any result can always be looked up. See [Write a Method](../how-to/write-a-method.md).

## DVC: the data

[DVC](https://dvc.org/) (Data Version Control) stores data files by their content, so every version of a file has its own fingerprint and is kept exactly as it was. Workflow Canvas stores your registered samples and every output your methods produce this way, and can keep a copy in a separate archive location. A result can always be traced to the exact bytes it was made from, and fetched again. See [Storage & Provenance](storage-and-provenance.md).

## Docker: the software and dependencies

[Docker](https://www.docker.com/) runs each step in a container: a sealed copy of the software and dependencies it needs, independent of whatever is installed on your machine. You describe a method's dependencies with [pixi](https://pixi.prefix.dev/) or [conda](https://docs.conda.io/), or point to an existing image, and Workflow Canvas builds and pins the image. The same step then runs with the same software and dependencies on any machine. See [Register an Environment](../tutorials/registering-an-environment.md).

## The run database: the record

Workflow Canvas keeps a database of every sample, method and run in your project. Each run records what it ran: the method's code, its inputs, parameters and environment, how its step was started, and which outputs it produced. This is what the History tab in the Canvas shows, and it is what lets you follow a result back through every step that led to it.


## How they combine

Together, these pin down everything that goes into a result. Snakemake fixes the steps and their order. git fixes the code. DVC fixes the data, by content. The container image fixes the software and dependencies. The run database ties the four together for every run.

That gives you two things. You can trace any result: open it in the History tab and see the code, data, parameters and dependencies behind each step that produced it. And you can rerun it: because each piece is pinned, running the same pipeline again reproduces the same work. When nothing that a step depends on has changed, Workflow Canvas reuses the earlier result, and when one thing changes, only the steps it affects run again. See [Caching & Reproducibility](caching-and-reproducibility.md).

A run can also leave your machine: `wfc export-run` writes it, with every run that led to it and the samples they read, as one zip that anyone with Python and Docker can rerun to check each output. See [Share Runs as a Bundle Anyone Can Rerun](../how-to/share-bundle.md).
