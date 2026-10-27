<!-- generated from pm_mvp::docs/consumer/how-to/canvas @ e8e6609da6b9; do not edit -->

# How-to: Build, Run and Inspect in the Canvas

## Overview

Canvas is the browser interface for building, running and inspecting pipelines. Start it from your project root:

```bash
wfc canvas
```

It serves at `http://127.0.0.1:8500`. Use `--port` for another port, `--host` to change the bind address, and `--project-root <path>` to serve a different project. The top bar has three tabs:

- **Builder** -- wire methods into a pipeline, set their parameters, check what will run, and run it.
- **History** -- browse past runs, inspect their parameters, metrics, logs and output files, and bring a run back into the Builder. A pipeline that is still running can be watched in the Builder or stopped from here, even after the canvas server restarts.
- **Registry** -- browse and register the project's modules, methods, environments and samples.

This page follows that order: building a pipeline, running it over many samples and parameter values, running it, then inspecting the results in History. To run and export from a terminal instead, see [Run and Inspect from the Command Line](run-and-inspect-results.md).

## Building a Pipeline

The Builder has a palette on the left, the canvas in the middle, the Inspector on the right and the **Runs Preview** table along the bottom.

![The Builder view with the demo pipeline loaded: node palette on the left, pipeline graph on the canvas, and the Runs Preview table below](../_images/builder-demo-pipeline.png)

**The palette.** At the top are the two system nodes, **Input Selector** and **Run Reference** (see System Nodes below), then the Pipeline Variables panel, then every registered method grouped by module. Type in **Search methods...** to filter by method or module name. Click a method to expand its input and output slots (fan-in inputs are marked `MULTI`); the **i** button shows its description and parameters.

**Add a node.** Drag a system node or a method from the palette onto the canvas. A method node shows its input slots on the left and its output slots on the right, each labelled with its file type (`csv`, `h5ad`, `dir`, ...) and coloured by that type, so slots of the same type share a colour.

**Choose a version.** A method node follows the newest registered version of its method, and its header shows the version number it runs on, with a green dot while that is the newest and a yellow dot when the node is pinned to an older one; hover the number to see which. To run an older version, select the node and pick it from the version dropdown in the Inspector (`v1`, `v2`, ...); the node pins that version, draws that version's inputs, outputs and parameters, and keeps its cached results when a newer version is registered. Picking the newest again makes the node follow it. If the version you switch to does not declare a parameter value you set, the row says `<param> is not in v<N>, remove it` and Run stays blocked until you remove the value.

**Connect nodes.** Drag from an output slot to an input slot. An input slot takes one connection; if you drop a second one onto it, Canvas refuses it and says which slot is already connected. The one exception is Run Reference nodes: several of them can feed the same slot.

**Delete and undo.** Select a node and press Backspace, right-click it and choose **Delete Node**, or use the delete button on the node or in the Inspector. Hover over a connection to delete it. **Ctrl+Z** undoes and **Ctrl+Y** (or **Ctrl+Shift+Z**) redoes; the toolbar has matching buttons. **Clear** empties the canvas.

## System Nodes

A pipeline's data comes in through **system nodes**. Every method node needs something connected upstream of it, so each chain starts at a system node; Validate reports a method node with nothing feeding it.

**Input Selector** feeds registered samples into the pipeline. Select the node, pick samples on the **Select Inputs** tab and click **Accept Selection**. The **Settings** tab sets the node's label and whether the samples fan out (one run per sample, the default) or fan in (one run for all of them); see [Fan-out and Fan-in](#fan-out-and-fan-in) below.

**Run Reference** feeds an output of a finished run into the pipeline, so you can build on earlier results without re-running them. Select the node, choose a completed run on the **Select Run** tab (expand a run to see its parameters and outputs) and click **Accept Selected Run**. The **Output** tab sets the node's label and shows the chosen run. The quickest way to add one is **Reference in Canvas** on a run in History (see [From History Back to the Builder](#from-history-back-to-the-builder)).

## Setting Parameters

Click a method node to open it in the Inspector. The header shows the node's module, its environment and the version it runs, with a delete button. The **Config** tab lists the node's parameters. Required parameters are marked `*`; hover the `?` for a parameter's description.

![A method node selected on the canvas, with the inspector panel showing its header and the parameter form controls](../_images/builder-inspector.png)

**Edit a value.** Each parameter row is locked (🔒) until you edit it. Click ✎ to unlock the row, change the value, and press Enter or click ✓ to lock it in. The control matches the parameter's type: a toggle for booleans, a dropdown for a fixed set of choices, a number box for `int` and `float`, a text box for strings (⤢ expands it to several lines), and JSON for `list` and `dict`. If the type does not accept a value, the row says why and stays unlocked. A text parameter that names a column of an upstream table offers the declared column names as suggestions.

A parameter you never set runs with the method's declared default.

**Lock All** at the top of the parameter list is enabled while the node has unlocked rows; it works like **Lock All** in the Runs Preview (see [Checking and Running a Pipeline](#checking-and-running-a-pipeline)).

To try several values for a parameter, or give one sample its own values, see [Variants and Per-Sample Values](#variants-and-per-sample-values). To share one value across many rows, see [Pipeline Variables](#pipeline-variables).

The **Output** tab shows the log of this node's most recent run. When a step fails, the Inspector shows a **This step failed** banner with a **View log** button.

## Variants and Per-Sample Values

A **sweep** runs a step several times with different values for a parameter. Each value is a **variant**, and each variant runs as its own branch, so the step and everything downstream of it run once per variant.

**Add variants.** In the node's Inspector, click **+ variant** under a parameter to add another value. If only one parameter on the node has variants, each variant keeps its own name. If several parameters have variants, the node runs every combination of them, named `v1`, `v2`, and so on; two parameters with two values each give four runs per sample.

**Override one sample.** Open the **Per Sample** sub-tab next to **All Samples** and pick a sample. Any value you set there, including variants, applies to that sample only; every other sample keeps the values from **All Samples**. A ● marks samples and parameters that have an override, and **Clear override** removes it. The sub-tab lists the samples of the Input Selector wired into the pipeline.

The Runs Preview shows one row per sample and variant, so you can check the combinations before you run (see [Checking and Running a Pipeline](#checking-and-running-a-pipeline)).

## Fan-out and Fan-in

The **Settings** tab of an Input Selector decides how its samples flow through the pipeline.

**Fan-out (the default).** Each selected sample runs as its own branch: with four samples selected, every downstream step runs four times, once per sample. **Keep going on failure** is on by default, so one sample failing doesn't cancel the others; the node then shows a count of how many samples succeeded, such as `3/4 ✓`. Turn it off to stop the whole pipeline at the first failure.

**Fan-in.** Switch to **Fan-in** to bundle all the selected samples into a single run, for example to merge or compare them. The step the selector feeds receives every sample on one input slot and runs once, and every step downstream of it also runs once. In History, the run shows the samples it bundled.

Wire a fan-in selector straight into the step that combines the samples, and make it that step's only input. The pipeline is refused before it starts if a fan-in step also takes another input, or if a step below a fan-in step also takes per-sample outputs.

Variants and fan-out multiply: two samples fanned out into a step with three variants give six runs of that step.

In a pipeline file, an Input Selector's fan-out or fan-in is its `fan_mode`, `"out"` or `"in"`; see [Pipeline Document](../reference/pipeline-document.md).

## Pipeline Variables

A pipeline variable is a named value you set once and bind to parameters on several nodes, for example a label column that several methods use.

**Create a variable.** Open the **Pipeline Variables** panel in the palette (below the system nodes) and click **+ Add variable**. Enter a name, pick a type (`str`, `int`, `float`, `bool`, `list` or `dict`), enter a value and click ✓. A value the type does not accept is reported in the panel and the variable is not added. Each variable's row shows its type, its value and how many parameter rows are bound to it. Click × to delete a variable; if rows are bound to it, Canvas asks you to confirm first.

![The Pipeline Variables panel with one variable defined](../_images/builder-variables-panel.png)

**Bind a parameter.** In the Inspector, click 🔗 on a parameter row and choose a variable. Variables of a type the parameter cannot take are greyed out. The row then shows `→ name` and the variable's value. A bound row can't also have variants or per-sample values.

![A bound param row in the inspector: the row shows the variable-name chip and the greyed resolved value](../_images/builder-bound-param.png)

**Unbind.** Click × on a bound row to unbind it, or ✎ to unbind it and start editing a literal value straight away.

When the pipeline runs, every bound parameter receives its variable's value. The exported pipeline JSON keeps the variables and bindings (a bound parameter is written as `{"$var": "name"}`), so it runs the same way with `wfc run-pipeline`, and **Open in Canvas ↗** in History's Pipelines view brings them back with their bindings.

## Checking and Running a Pipeline

**Name the pipeline** in the text field in the toolbar. The name titles the pipeline's row in History's Pipelines view.

**Validate** checks the graph against the registered methods and lists any problems, such as an unknown method or a method node with nothing feeding it. A valid graph shows "Workflow is valid!".

**The Runs Preview** at the bottom of the Builder lists every run the pipeline will schedule. The **All runs** view has one row per method node with its run count; pick a node to see its runs, one per sample and variant, with the parameters each will use. Those rows can be grouped by sample, variant or status. A row that would produce exactly the same result as an earlier row is counted but hidden behind a **Show N reused** toggle. Close the table with × and reopen it from the **Runs Preview** tab at the bottom edge.

Until you lock, the preview shows run counts only. Click **🔒 Lock All** to lock every parameter row and ask the project which runs are already done. Each row then shows one status:

| Status | What happens on Run |
|---|---|
| `cached · local` | skipped; the earlier result is reused |
| `cached · remote` | skipped; the earlier result is pulled from the remote |
| `outputs missing` | re-run, because the earlier run's outputs are gone |
| `new · this step changed` | runs |
| `new · upstream re-runs` | runs, because a step feeding it runs |
| `blocked` | cannot run; the row gives the reason |

A cached row links to the run it reuses. Any edit clears the statuses until you lock again. While a row is blocked, **Run** is disabled and its tooltip lists the reasons. For why a step is reused or re-run, see [Caching and Reproducibility](../explanation/caching-and-reproducibility.md).

**Run** starts the pipeline. If any parameter row is still unlocked, Run first opens a summary listing those rows and every parameter that will run with its declared default; click **Lock and run** to go ahead. If a row cannot be locked, the summary names it and the run does not start.

While the pipeline runs, **Run** becomes **Stop**, and each node shows its status: running, completed, failed, cancelled, cached when its result was reused, or a count such as `3/4 ✓` when some of its samples failed. If several nodes use the same method, each one shows only its own runs. Select a node and open the Inspector's **Output** tab to follow its log. A cached node names the run it reused, and a node cancelled by an upstream failure names the step whose failure stopped it.

If a pipeline is interrupted, a badge in the toolbar counts the runs whose outputs are not yet archived; click it and choose **Archive now**.

**Import and Export.** **Export** downloads the canvas as a pipeline JSON file named after the pipeline, which you can run with `wfc run-pipeline` (see [Run and Inspect from the Command Line](run-and-inspect-results.md)). **Import** loads a pipeline JSON file and replaces the canvas; Canvas asks first if the canvas has unsaved work, and does not import while a pipeline is running.

## The History Tab

The History tab shows the project's past runs, including runs started with `wfc run-pipeline`. A summary line counts the runs by status, with cache hits also counted under cached.

**Views.** Switch between three views at the top:

- **Descendants** (the default) -- per sample, a collapsible tree of the runs that actually executed. Runs served from the cache are left out, so after a re-run the tree shows only new work. A run with several parents appears under each parent with an "N parents" marker, and selecting one copy highlights all of them. **Collapse all** and **Expand all** sit at the end of the filter bar.
- **Lineages** -- one row per chain of runs, left to right from sample to final step. Runs served from the cache carry a `⟳ CACHED` pill, so a re-run gets its own row even when every step in it was reused.
- **Pipelines** -- one row per submitted pipeline, under the column headers Status, Pipeline, Runs, Samples, Started and Actions. The status is one of RUNNING, SUCCEEDED, FAILED, CANCELLED, or MIXED when some runs succeeded and others failed or were cancelled; the canvas shows the same status for the same pipeline. The Runs column draws a small bar and counts the runs in words, for example `4 ran · 3 cached · 1 failed · 2 pending`: *ran* runs succeeded by running, *cached* reused an earlier result, and *pending* are runs the pipeline will still make. **Open in Canvas ↗** loads the pipeline onto the canvas; the **⋯** menu also downloads its pipeline JSON and copies its ID. While a pipeline is running, its row offers **Watch in Canvas ↗**, which shows it running on the canvas, and **Stop**. Click a row to list its runs by step, then sample, with each run's status, method version (a green dot marks the newest), sample, NID, duration and run ID. Hover over a CANCELLED run to see why: you stopped it, an upstream step failed (naming that step and sample, with a link to the failed run), or the pipeline ended before it started.

![The Descendants view: per-sample collapsible trees of the runs that executed](../_images/history-descendants.png)

After a pipeline's first run, every step in the Lineages view has executed:

![The Lineages view after a first run: horizontal lineage chains, every step freshly executed](../_images/history-lineages-first-run.png)

Change one parameter on the last step and run again, and only that step executes. Its new row shows the reused upstream steps marked `⟳ CACHED`, followed by the new run:

![The Lineages view after re-running with a changed plot parameter: cached upstream steps carry the amber CACHED pill and feed the freshly-executed plot, and every path row renders in full](../_images/history-lineages-cached.png)

![The Pipelines view: one row per pipeline under column headers, with its status, runs bar and counts, and one pipeline expanded to list its runs by step](../_images/history-pipelines.png)

**Filters.** The filter bar applies to every view. Narrow the runs by time range (last 24 hours, 7 days or 30 days), module, methods, samples, status and free-text search. Choosing a module narrows the Methods list to its methods, and the Samples list offers only samples with matching runs. The buttons at the end of the bar show only favorites (★), show or hide archived runs (archived runs are hidden by default), turn on select mode for export, refresh, and reset the filters.

**Focus on one run.** Open a run and click **→ Descendants** in its detail panel to show only that run and the runs downstream of it; a banner names the run. **Show all runs** returns to the full view.

## Run Details and Downloads

Click a run in any view to open its detail panel. From its header you can copy the run ID (which `wfc export` takes) or a share link, download the run's record as JSON, star it as a favorite, rename it, or archive it to hide it from the default list (**Unarchive** brings it back). The panel has five tabs:

- **Overview** -- run ID, start time, the pipeline it belongs to, its parent runs by input slot, and each sample it read as a line "reads *sample* → *slot*". A cached run shows **Cached from** with a link to the run it reused. A cancelled run says why it was cancelled: you stopped it, an upstream step failed (naming that step and sample, with a link to the failed run), or the pipeline ended before it started. A failed run shows its error and lists the runs cancelled because of it. The tab ends with the **Cache-key inputs** card, which shows what decided whether wfc could reuse this run (see below).
- **Parameters** -- the values it ran with.
- **Metrics** -- the numbers the method logged.
- **Artifacts** -- the output files. Click a file to download it; images show a thumbnail that opens full size. A cached run lists the outputs of the run it reused. An output that changed after the run wrote it is listed first under **Unavailable**, with "changed after the run wrote it; run the step again" and no download link.
- **Output** -- the run's log. **Load full log** shows all of it.

![A run's detail panel open on the Artifacts tab, with the output PNG shown as an inline thumbnail](../_images/run-detail-panel.png)

![A run's detail panel Overview tab, showing a Sample read row of the form 'reads sample -> slot'](../_images/run-detail-overview.png)

**See why a run was or was not reused.** The last block of the Overview tab is the **Cache-key inputs** card. Click its title to open it; click **?** for a short explanation of the cache key. *In the cache key* lists what wfc compared:

- **Method**: the method, its version and its `code` hash.
- **Env**: the environment and its `env` hash.
- **Slots**: click *N input slots* for a table of each input slot and what filled it: an upstream run and its output slot with that run's `run key`, or a sample with its `content` hash.
- **Params**: click *N params* for a table of each parameter's name, value and declared type. Hover over a long value to preview it; click it to open its full JSON.

Two runs with the same key have the same rows. *Recorded, not in the key* lists where the run came from: the `commit`, the wfc version and the `pipeline` hash. Every hash is labelled with what it is; hover over one to see what it is computed from, and click the copy icon beside it to copy the full value. Hover over a name that is cut short to see it in full. A value wfc did not record reads "not recorded". A run whose key was computed under an earlier rule says so in one line.

The card links to what it names. Click a run to open it, or its output slot to open it on **Artifacts**. Click the method to open it in the Registry tab on the version this run ran, or a sample to open the Registry's samples (its copy icon copies the sample's name for the filter). Click the `pipeline` hash to open the Pipelines view with this run highlighted.

![A fresh run's Cache-key inputs card opened on the Overview tab, with its method, env, slots table and params table under 'In the cache key' and its commit, wfc version and pipeline under 'Recorded, not in the key'](../_images/run-detail-cache-key-inputs.png)

![A cached run's Cache-key inputs card with its slots table open, showing the sample it read and its own commit](../_images/run-detail-cache-key-inputs-cached.png)

**Export a run to rerun elsewhere.** **Export run** in the detail panel's header packages the run and every run above it as a zip: a record of how the result was made that anyone with Python and Docker can rerun with `python rerun.py`, without wfc, checking each output against yours. **Export runs** does the same for the runs you selected (see below). The dialog shows how many steps the bundle holds, or every reason it cannot be made. **Include outputs** adds the original output files so the receiver can compare them with the rerun's; **Include images** adds each step's container image. Changing either shows the plan again, and nothing downloads until you click **Export**. If a step's image was never pushed to a registry and **Include images** is off, the dialog warns about it and the button reads **Export anyway**. The command-line equivalent is `wfc export-run`; see [Share runs as a bundle anyone can rerun](run-and-inspect-results.md#share-runs-as-a-bundle-anyone-can-rerun).

**Download several runs at once.** Click the select-mode button in the filter bar and tick the runs you want, then click **Export Artifacts** in the bar that appears. A preview shows the number of runs, files and methods, the total size and a breakdown by file type. Click **Download Zip** to download them as one zip file.

## From History Back to the Builder

Three actions load History into the Builder:

- **Open in Canvas ↗** on a row of the Pipelines view loads the pipeline as it was submitted, including its pipeline variables and bindings.
- **Open lineage in Canvas** in the detail panel rebuilds the run and every run upstream of it as a pipeline, each connection using the output and input slot the run actually used. Each node is pinned to the version that run used. Run it as-is to reuse its results, or change a parameter and run it as a new pipeline. Canvas refuses to open a lineage for a run made before versions were recorded, or for a run that never ran, and the message names that run.
- **Reference in Canvas** in the detail panel adds a Run Reference node for the run to the current canvas and keeps everything already there, so a new pipeline can use its outputs as input. A message with **Jump to node** shows where it landed.

To keep the exact pipeline a run used, open the **⋯** menu on its row in the Pipelines view and choose **Download pipeline JSON**. The file has every method node pinned to the version it ran, so loading it later runs the same versions.

Both Open actions replace the canvas. Canvas asks first if the canvas has unsaved work, and does not replace it while a pipeline is running.

## The Registry Tab

The Registry tab lists everything registered in the project, on four sub-tabs. **↻ Refresh** reloads the lists and every open method.

- **Modules** -- each module with its output contracts and an `N methods →` link to its methods.
- **Methods** -- the methods grouped by module, each row showing the method, its newest version and its env. Click a module's name to fold it, or use **Collapse all** and **Expand all**. Click a method to open it: its inputs, outputs and parameters sit beside its files, the main script first, with syntax highlighting. Click an input, output or parameter to see its description and the other fields it declares, and click a value cut short with … to read all of it. Drag the divider between the contract and the code to change their widths.
  - An open method's row has a version picker in place of its version: pick an earlier version to see its files and contract. The newest is marked **latest**.
  - The **⋯** menu on a method's row has **Version details…** (when it was registered, its commit, its version identity, its runs, its env fingerprint and image, and its code tree), **Copy version identity**, **Export script and contract** and **Register a new version…**. The **⋯** beside a version in the picker has the same for that version, and **Copy commit**; a version registered on another machine reads **not recorded** for its commit.
  - **Export script and contract** downloads the version as `<method>_v<N>.zip`, holding every file of its registered copy. Unzip it, edit it, and register the folder as the method's next version.
- **Envs** -- each registered environment with its backend, the methods that use it, its run count, and a chip saying where its image is: **pushed**, **not pushed**, **no registry**, **cannot push** or **missing**, or **unknown** when Docker is not running. Hover over the chip for the reason and the command that fixes it: for **cannot push**, the env's name is one no registry accepts and the tooltip gives the name rule and the fixes; for a bring-your-own image already in another registry, it names that registry. See [Share a Project](sharing-a-project.md). Expand a pixi or conda environment to see its installed packages as `name==version`, tagged conda, pixi or pip.
- **Samples** -- each sample with its size, whether it has been pushed to the remote or is local-only, and how many runs used it.

![The Registry Methods list with one method open, its contract beside its main script](../_images/registry-methods.png)

On the Modules, Methods and Samples sub-tabs, **+ Register** opens a form to register a new one from the browser; see [Registering Modules, Methods, and Samples](registration.md). Environments are registered from the command line; see [Registering an Environment](../tutorials/registering-an-environment.md).

## Next Steps

- [Run and Inspect from the Command Line](run-and-inspect-results.md) -- run an exported pipeline with `wfc run-pipeline` and copy outputs out with `wfc export`.
- [Caching and Reproducibility](../explanation/caching-and-reproducibility.md) -- why a step is reused or re-run.
- [Storage and Provenance](../explanation/storage-and-provenance.md) -- where outputs are stored.
- [CLI Reference](../reference/cli-reference.md) -- every `wfc` command, including `wfc canvas`.
