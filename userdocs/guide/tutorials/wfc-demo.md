<!-- generated from pm_mvp::docs/consumer/tutorials/wfc-demo @ 7f780cc8bc11; do not edit -->

# A Tour of the Demo

## Start the Demo

`wfc demo` adds a complete, runnable pipeline to a project and opens it in the Canvas, so you can watch a run end to end before writing any methods of your own. It needs wfc installed, with Docker running and git: see [Install Workflow Canvas](../install.md).

```bash
wfc init --dir demo_project   # makes the demo_project folder and sets up a wfc project in it
cd demo_project               # moves into that folder
wfc doctor                    # checks that git, Docker and the archive are ready
wfc demo                      # adds the demo pipeline and opens it in the Canvas
```

`wfc demo` works in an existing project, so run `wfc init` first; when it asks where to keep the archive, press Enter to use the suggested folder. `wfc doctor` checks that git, Docker and the archive are ready: every line it prints should say `OK`, and any line that doesn't says how to fix it. `wfc demo` then:

- builds a container image with the software the demo's methods need (the first build takes a few minutes) and registers it as the environment `wfc-demo-env`;
- registers five methods in three modules, `__demo__cleanup`, `__demo__labeling` and `__demo__plotting`, and three samples. Each module has its own colour in the Canvas;
- writes the pipeline to `demo-pipeline.json`;
- starts the Canvas at `http://127.0.0.1:8500/?pipeline=demo` and opens it in your browser with the pipeline loaded.

Press **Run** to run all five steps for all three samples.

:::{figure} ../_images/builder-demo-pipeline.png
:alt: The Canvas Builder with the demo pipeline pre-wired: an Input Selector with the three demo samples feeding five method nodes in three module colours

The demo pipeline in the Builder: three samples feeding five methods, coloured by module.
:::

The Canvas keeps running until you press Ctrl+C in the terminal. To come back to it later, run `wfc canvas` and open `http://127.0.0.1:8500/?pipeline=demo`.

To rebuild the demo from scratch, for example after upgrading wfc, run `wfc demo --force`. It removes the existing demo and its runs first.


## The Five Steps

The pipeline runs five methods on each sample. Every name the demo registers starts with `__demo__`; this page leaves the prefix off.

```{mermaid}
flowchart LR
    preprocess --> filter_cells --> label
    label --> summarize
    label --> plot
```

| Step | Module | What it does |
|---|---|---|
| **preprocess** | `cleanup` | drops rows with no `intensity` value |
| **filter_cells** | `cleanup` | keeps rows with `quality` at or above `min_quality` (0.5) |
| **label** | `labeling` | adds a `label` column: `above` or `below`, depending on whether `intensity` reaches `threshold` (150) |
| **summarize** | `labeling` | reports the row count and mean intensity for each label |
| **plot** | `plotting` | draws the sample's intensity distribution: a smooth density curve over a faint histogram, shaded blue below the threshold and green above it |

`plot`'s `bins` param sets the number of bars and `smoothing` how smooth the curve is (1.0 by default, higher is smoother).

The method scripts are copied into your project under `.wfc/registry/methods/<module>/<name>/`, for example `.wfc/registry/methods/__demo__plotting/__demo__plot/`. Each one has comments that match its code to its `method.yaml` (each input, parameter and output), so you can use them as a starting point for your own methods. See [Write a Method in Python](authoring-a-method-script.md).


## Inspecting a Run

When the run finishes, open the **History** tab and select a `plot` run. Its **Artifacts** tab lists the figure under its image heading, with its preview closed: click the arrow beside it to show the preview in the panel. Click the file's name to open it in the viewer, where you can zoom in and move around the plot. The download icon beside each file saves it. If the panel feels narrow, drag its left edge to widen it. See [Look at a Run's Results](../how-to/run-details.md) for everything the run detail panel shows.

::::{div} wfc-gallery

:::{figure} ../_images/run-detail-panel.png
:alt: A completed plot run's detail panel on the Artifacts tab, with the figure's preview open

A `plot` run's Artifacts tab, with the figure's preview open in the panel.
:::

:::{figure} ../_images/run-detail-viewer.png
:alt: treat_02's figure open in the viewer: a density curve over a faint histogram, blue below the threshold and green above it

The same figure opened in the viewer, where you can zoom in and move around.
:::

::::



On the run's **Overview** tab, open the **Cache-key inputs** card at the bottom. It lists what wfc compared to decide whether it could reuse the run: the method's code, its environment, its inputs and its params. Change one of the `plot` step's params, run the pipeline again and open the new `plot` run: in the cache key, only its `params` hash differs from the earlier run's, which is why that step ran again while the steps above it were reused.

:::{figure} ../_images/run-detail-cache-key-inputs.png
:alt: The Cache-key inputs card of a demo plot run, with its method, env, slots table and params table

The Cache-key inputs card: what wfc compared to decide whether to reuse the run.
:::

To copy any step's output out of wfc, use the run ID shown in the detail panel:

```bash
wfc export --run <run-id>                                  # list the run's outputs
wfc export --run <run-id> --slot <output-name> --dest <dest>   # copy one output to <dest>
```


## The Sample Data

Each sample is a CSV of 400 cells with the columns `id`, `intensity`, `area` and `quality`. Two cells per file have no `intensity`, which is what `preprocess` removes.

| Sample | intensity range | quality range |
|---|---|---|
| `ctrl_01` | 36.5 – 174.6 | 0.19 – 0.99 |
| `treat_01` | 46.5 – 269.9 | 0.21 – 0.98 |
| `treat_02` | 56.5 – 319.7 | 0.06 – 0.99 |

The control's cells form one group around 105. In `treat_01` about two thirds of the cells have moved up to around 190, and in `treat_02` most have moved to around 240. With the default parameters, almost every control cell is labelled `below` and most treated cells `above`. Try changing the parameters on the Canvas and running again:

- Raise `threshold` to 200 and every `ctrl_01` cell falls `below`.
- Change `min_quality` and the row count of `treat_02`, which has the most low-quality cells, changes the most.
- Raise `plot`'s `smoothing` to 2 for a smoother curve; only the `plot` steps run again.

Steps whose inputs and parameters did not change are reused from the earlier run instead of running again.


## Export a run and rerun it

Any run can leave wfc as one zip that holds everything that made it: the samples it read, each step's code, parameters and container image, and how each step was launched. Whoever receives it reruns it with Python and Docker, without installing wfc, and sees for each output whether it came out the same.

1. **Export a `plot` run.** In the History tab, open the `plot` run for `ctrl_01` and click **Export run** in the detail panel's header. Turn on **Include outputs** and **Include images**, then click **Export**. The dialog shows that the bundle holds four steps: `plot` and the three steps above it. From the command line, take the run ID from the detail panel, or find it with `wfc runs --method __demo__plotting.__demo__plot`:

   ```bash
   wfc export-run --run <run-id> --out demo-plot.zip --with-outputs --with-image
   ```

   The demo's image is only on this machine, so **Include images** saves it into the zip (about 100 MB), and the zip needs nothing else.

2. **Rerun it.** Unzip it into a new folder, on this computer or on another one with Python 3.11 or newer and Docker, and run:

   ```bash
   python rerun.py
   ```

   After a line about the image, it prints one line per output:

   ```text
   Step 1  run 2   __demo__cleanup.__demo__preprocess v1    __demo__ctrl_01   clean.csv     same
   Step 2  run 4   __demo__cleanup.__demo__filter_cells v1  __demo__ctrl_01   filtered.csv  same
   Step 3  run 7   __demo__labeling.__demo__label v1        __demo__ctrl_01   labeled.csv   same
   Step 4  run 12  __demo__plotting.__demo__plot v1         __demo__ctrl_01   figure.png    same
   All 4 outputs are the same.
   ```

3. **Look inside.** The bundle's `README.md` lists each step with its method version, sample, image, parameters and output hashes. Each step's folder under `steps/` holds its code, the exact `docker run` it ran, and its logs.

[Share Runs as a Bundle Anyone Can Rerun](../how-to/share-bundle.md) covers the options, what the receiver needs, and what to do when an output comes out `DIFFERENT`.

## Share the demo

Exporting a run sends a result. Sharing the project gives a collaborator its methods and environments, so they can build and run pipelines of their own. It does not carry samples: a collaborator registers them again from the data ([What sharing through git does not carry](../how-to/sharing-a-project.md#what-sharing-through-git-does-not-carry)). An exported run is the other way round: its zip always holds the samples it read. The full procedure is in [Share a Project](../how-to/sharing-a-project.md).

`wfc demo` keeps the demo's image on this machine. To share the project, push the image to an image registry, a server that stores container images so collaborators can download them, then check what a collaborator would still miss.

1. **Push the image.** The first `wfc push-env` in a project sets its registry. With a GitHub remote it proposes `ghcr.io/<owner>/<repo>` and shows what it will push before you confirm. Without a GitHub remote, or to use another registry, pass `--registry <base>`. Docker must be logged in to the registry first; `wfc share` prints the `docker login` line.

   ```bash
   wfc push-env wfc-demo-env      # or: wfc push-env --all
   ```

2. **Check the result.** `wfc share` prints one row per check and changes nothing:

   ```bash
   wfc share
   ```

   Each row that warns names the command that fixes it. Fix the rows in order and run `wfc share` again.

3. **See the image on GitHub.** Open your GitHub profile's **Packages** tab: the demo's environment is the package `<repo>/wfc-demo-env`. The repository's main page lists it under **Packages**. [See Your Images on GitHub](../how-to/see-your-images-on-github.md) explains each page.

## Removing the Demo

```bash
wfc demo --remove
```

This lists what it will delete and asks you to confirm. It removes everything the demo added: its modules, methods, samples, environment, runs and `demo-pipeline.json`. Add `--purge-image` to also delete the demo's Docker image. Cached output files stay in the cache until you run `wfc cache prune`.

Nothing you register yourself is removed, because names starting with `__demo__` are kept for the demo.

## Next Steps

To build a pipeline of your own, follow [Your First Pipeline](getting-started.md), then [Write a Method in Python](authoring-a-method-script.md). The demo's scripts under `.wfc/registry/methods/<module>/<name>/` are a second set of worked examples; copy them before you remove the demo.

To bring your own scripts in with an AI agent instead, see [Work with an AI Agent](work-with-an-ai-agent.md).
