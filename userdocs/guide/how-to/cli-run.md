<!-- generated from pm_mvp::docs/consumer/how-to/cli-run @ 1b8304c54cd4; do not edit -->

# Run a Pipeline

## Run a pipeline

A pipeline is a JSON file. The usual way to get one is to build the pipeline in the Canvas Builder and click **Export**, which downloads it named after the pipeline. Run it with:

```bash
wfc run-pipeline --pipeline my-pipeline.json
```

`--pipeline` is the path to the pipeline JSON file. The command runs the pipeline exactly as the Canvas would. Each step runs in a container: a packaged copy of the software and dependencies your method needs (see [The Tools Behind Workflow Canvas](../explanation/how-the-pieces-fit-together.md)), so Docker must be running. Steps whose results already exist are reused instead of re-run (see [Caching & Reproducibility](../explanation/caching-and-reproducibility.md)).

A parameter a node leaves unset runs at the default its `method.yaml` declares. For each node that runs any declared default, `wfc run-pipeline` prints a line naming each one and its value. For example:

```text
[run_pipeline] node 'filter' (my_analysis/filter_data) runs declared defaults: threshold=0.5
```

Options:

- `--cores N`: how many steps can run in parallel (default 4).
- `--keep-going`: when one step fails, independent steps keep running, so the other samples of a fan-out still finish. Without it, the first failure stops the pipeline.
- `--no-archive`: skip archiving outputs when the pipeline finishes. Run `wfc cache archive` later to archive them.
- `--project-root <path>`: run against a project other than the one you are in.

The full list is in the [Pipeline Commands](../reference/cli-pipelines.md#wfc-run-pipeline).

To check a pipeline file without running it, use `wfc check-pipeline --pipeline my-pipeline.json`. It runs the checks the Canvas Validate button runs, needs no Docker, and exits 1 on an error.

To stop a running pipeline from another terminal, run `wfc cancel <pipeline id>`. It shows what it will stop and asks first; `--yes` skips the question.


## Run a method without writing a pipeline

To run a registered method once on a registered sample, without building a pipeline, name both:

```bash
wfc run-method my_analysis.filter_data --sample scores
```

The method is `<module>.<method>` and `--sample` is the sample's name. wfc writes a one-step pipeline for the method, saves it in the project's `pipelines/` folder and runs it like any pipeline: the run is recorded, its outputs are archived, and a result that already exists is reused. Docker must be running. Before it runs, the command prints where it saved the pipeline:

```text
Saved the pipeline to <project>/pipelines/my_analysis.filter_data-scores.json; running it.
```

The saved file is an ordinary pipeline: open it in the canvas, edit it, or run it with `wfc run-pipeline --pipeline`. Running the same command again reuses it.

To set the method's parameters, give them as a JSON object; the ones you leave out run at their defaults:

```bash
wfc run-method my_analysis.filter_data --sample scores --params '{"threshold": 0.8}'
```

A parameter the method does not declare, or a value it does not accept, is refused before anything is saved. To give the pipeline a name of your own, add `--name`, for example `--name filter-strict`. To try several values or samples in one pipeline, build a pipeline with variants and an input selector instead (see [Sweep Values and Override Samples](sweep-values.md)).

The sample feeds the method's one input. If several of its inputs could take it, the command refuses and names them; write a pipeline that feeds each one instead. This runs a registered method; to run a folder you have not registered, use `wfc try`.

When it ends, read the result with `wfc runs --method my_analysis.filter_data` and `wfc show-run --run <run id>`. Every option, where each file is saved and what is refused: [wfc run-method](../reference/cli-pipelines.md#wfc-run-method).
