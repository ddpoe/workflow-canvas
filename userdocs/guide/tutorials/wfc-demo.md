<!-- generated from pm_mvp::docs.consumer.tutorials.wfc-demo @ e14d95af87ba; do not edit -->

# Tutorial: Exploring the Demo

## What `wfc demo` does

`wfc demo` adds a complete, runnable pipeline to a project and opens it in the Canvas, so you can watch a run end to end before writing any methods of your own. It needs the same prerequisites as [Getting Started](getting-started.md#prerequisites): Python, Docker running, and git.

```bash
pip install workflow-canvas
wfc init --dir demo_project --yes
cd demo_project
wfc demo
```

`wfc demo` works in an existing project, so run `wfc init` first. It then:

- builds a small container image for the demo, a packaged copy of the software and dependencies its methods need ([The Tools Behind Workflow Canvas](../explanation/how-the-pieces-fit-together.md)) (the first build takes a few minutes) and registers it as the environment `__demo__env`;
- registers the module `__demo__` with five methods, and three samples;
- writes the pipeline to `demo-pipeline.json`;
- starts the Canvas at `http://127.0.0.1:8500/?pipeline=demo` and opens it in your browser with the pipeline loaded.

Press **Run** to run all five steps for all three samples.

![The Canvas Builder with the demo pipeline pre-wired: five method nodes fed by an Input Selector with the three demo samples](../_images/builder-demo-pipeline.png)

The Canvas keeps running until you press Ctrl+C in the terminal. To come back to it later, run `wfc canvas` and open `http://127.0.0.1:8500/?pipeline=demo`. Use `--port` to serve on another port and `--no-open` to skip opening the browser.

If the demo is already in the project, `wfc demo` stops and tells you to pass `--force` (set it up again from scratch) or to remove it with `wfc demo --remove`.


## The Pipeline

Every name the demo registers starts with `__demo__`: the methods are `__demo__preprocess`, `__demo__filter_cells` and so on, and the samples are `__demo__ctrl_01`, `__demo__treat_01` and `__demo__treat_02`. This page uses the short names.

The pipeline runs five methods on each sample:

```
preprocess -> filter_cells -> label -> summarize
                                    -> plot
```

- **preprocess** drops rows with no `intensity` value.
- **filter_cells** keeps rows with `quality` at or above `min_quality` (0.5).
- **label** adds a `label` column: `above` or `below`, depending on whether `intensity` reaches `threshold` (150).
- **summarize** reports the row count and mean intensity for each label.
- **plot** draws an intensity histogram for the sample, coloured by label.

The method scripts are copied into your project under `methods/__demo__<name>/`. Each one has comments that match its code to its `method.yaml` (each input, parameter and output), so you can use them as a starting point for your own methods. See [Authoring a Method Script](authoring-a-method-script.md).


## Inspecting a Run

When the run finishes, open the **History** tab and select a `plot` run. Its **Artifacts** tab shows the histogram as a thumbnail; click it to see it full size. See [Build, Run and Inspect in the Canvas](../how-to/canvas.md) for everything the run detail panel shows.

![A completed plot run's detail panel on the Artifacts tab, with the histogram PNG as an inline thumbnail](../_images/run-detail-panel.png)

To copy any step's output out of wfc, use the run ID shown in the detail panel:

```bash
wfc export <run-id>                          # list the run's outputs
wfc export <run-id> <output-name> <dest>     # copy one output to <dest>
```


## The Sample Data

Each sample is a CSV of 50 cells with the columns `id`, `intensity`, `area` and `quality`. One or two cells per file have no `intensity`, which is what `preprocess` removes.

| Sample | intensity range | quality range |
|---|---|---|
| `ctrl_01` | 49.2 – 193.5 | 0.51 – 0.98 |
| `treat_01` | 70.3 – 272.2 | 0.31 – 0.95 |
| `treat_02` | 80.0 – 320.7 | 0.21 – 0.80 |

With the default parameters, most control cells are labelled `below` and most treated cells `above`. Try changing the parameters on the Canvas and running again:

- Raise `threshold` to 200 and every `ctrl_01` cell falls `below`.
- Change `min_quality` and the row count of `treat_02`, which has the most low-quality cells, changes the most.

Steps whose inputs and parameters did not change are reused from the earlier run instead of running again.


## Removing the Demo

```bash
wfc demo --remove
```

This lists what it will delete and asks you to confirm; pass `--yes` to skip the question. It removes the demo's module, methods, samples, environment and runs, the method directories under `methods/`, and `demo-pipeline.json`. Add `--purge-image` to also delete the demo's Docker image. Cached output files stay in the cache until you run `wfc cache prune`.

Nothing you register yourself is removed: wfc refuses any module, method, sample or environment name that starts with `__demo__`, so only the demo carries that prefix.


## Next Steps

To build a pipeline of your own, follow [Getting Started](getting-started.md), then [Authoring a Method Script](authoring-a-method-script.md). The demo's scripts under `methods/__demo__<name>/` are a second set of worked examples; copy them before you remove the demo.

