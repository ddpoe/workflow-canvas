<!-- generated from pm_mvp::docs/consumer/tutorials/getting-started @ 4d10ba36ed64; do not edit -->

# Tutorial: Your First Pipeline

## Introduction

Workflow Canvas (wfc) runs your analysis scripts as pipeline steps, records what produced every result, and reuses a step's earlier result when its code, parameters, inputs and environment have not changed.

This tutorial takes you from installation to a one-step pipeline you built yourself: you create a project, an environment, a module, a method and a sample, run the pipeline, and look at the result. To see a complete pipeline before writing anything, install wfc and then follow [Exploring the Demo](wfc-demo.md). For the bigger picture, read [What is Workflow Canvas?](../index.md).

## Prerequisites

You need:

- **Python 3.12** to install and run wfc.
- **Docker**, installed and running. Each step runs in a container: a packaged copy of the software and dependencies your method needs (see [How the Pieces Fit Together](../explanation/how-the-pieces-fit-together.md)). On Windows and macOS this is Docker Desktop; on Linux, Docker Engine.
- **Git**. wfc records the commit of your method code with every run and will not run while tracked files have uncommitted changes. Everything stays local: no account or remote is needed.
- **conda** ([Miniconda](https://www.anaconda.com/docs/getting-started/miniconda/main), [Miniforge](https://conda-forge.org/download/) or Anaconda), used in this tutorial for the environment your method runs in.

DVC and Snakemake are installed with wfc, and `wfc init` configures them for you.

## Installation

```bash
pip install workflow-canvas
wfc --help
```

`wfc --help` lists the available commands, among them `init`, `doctor`, `demo`, `register-env`, `register-module`, `register-method`, `register-sample`, `run-pipeline`, `canvas` and `export`.


## Your First Pipeline

You will build a pipeline with one method, `filter_data`, that keeps the rows of a CSV whose `quality` is at least a threshold. Your code lives under `src/`, and every command runs from the project root, `wfc_project_root`.

### 1. Create a project

A project is a folder that holds your analysis code and everything wfc keeps track of about it. Create one and move into it:

```bash
wfc init --dir wfc_project_root
cd wfc_project_root
```

`wfc init` asks one question: where to keep the **archive**. This is a folder outside your project where wfc keeps a backup copy of your data and every result your pipelines produce. If you clean up your project to free disk space, wfc gets the files back from the archive when it needs them. Press Enter to use the suggested folder, `~/.wfc/archives/wfc_project_root`.

It then sets up three things in the project:

- **A `.wfc/` folder**, where wfc keeps its settings and the record of every run.
- **A git repository.** git keeps the history of your code, so wfc can record exactly which version of your code produced each result. It all stays on your computer, with no account needed. ([How wfc uses git](../explanation/how-the-pieces-fit-together.md#git-the-code))
- **The archive.** wfc manages it with DVC, a tool that saves every version of a data file exactly as it was, so each result can be traced to the exact data it was made from. You never run DVC yourself. ([How wfc uses DVC](../explanation/how-the-pieces-fit-together.md#dvc-the-data))

When you back up the project, keep the archive and the `.wfc/` folder together. The archive holds the files, and `.wfc/` records which run made each one.

### 2. Check that everything is ready

Before you register anything, check that the project is ready to run:

```bash
wfc doctor
```

`wfc doctor` checks that git, Docker and the archive are ready, and prints one line for each. `wfc init` printed the same checklist when it finished. Every line should say `OK`:

```text
Run-readiness:
  [  OK] git      git repository present with a clean, committed HEAD.
  [  OK] dvc      DVC archive configured and reachable (...).
  [  OK] docker   Docker daemon is running.
  [  OK] samples  No samples registered.
  [  OK] views    Restored samples and checkouts are links to the cache (...).
```

If a line doesn't say `OK`, the line under it says how to fix it. Fix it and run `wfc doctor` again before you go on. Later, if a run won't start, `wfc doctor` is the first thing to try.

### 3. Register an environment

An environment is the software your method runs with: Python and the packages your code imports. wfc packages it into a container, a sealed copy that runs the same on any computer.

Start from a conda environment. You can use one you already have, as long as it has pandas, which this tutorial's method uses. Or create one for this tutorial. Either way, install `wfc-client`, the small package your method script uses to talk to wfc:

```bash
conda create -n first-pipeline python=3.12 pandas
conda activate first-pipeline
pip install wfc-client
```

Then register it:

```bash
wfc register-env first-pipeline conda:first-pipeline
```

The first name is what your methods will call the environment. `conda:first-pipeline` tells wfc which conda environment to copy. wfc reads the packages you installed and the versions you have, and builds the container from them. Your conda environment isn't changed. Containers run Linux, so on Windows and macOS wfc picks the Linux builds of the same versions for you and prints a note saying so. The first build takes a few minutes.

If you use pixi or uv, have a lock file, or already have a container image, see [Registering an Environment](registering-an-environment.md).

### 4. Register a module

A module groups related methods. Create `src/my_analysis/module.yaml`:

```yaml
description: Table filtering utilities
```

Then register it, pointing `--module-dir` at that folder:

```bash
wfc register-module --name my_analysis --module-dir src/my_analysis
```

`register-module` reads `module.yaml` from the folder you pass. A module can also declare output contracts that every method in it must provide; see [Writing Contracts](writing-contracts.md).

### 5. Write and register a method

A method is one step of your analysis: a folder holding a `method.yaml` and a script.

Start with the `method.yaml`. It is the method's contract: what goes in, what comes out, which settings it takes and which environment it runs in. wfc uses it to check a pipeline's wiring and settings before anything runs, and to check after each step that it produced what it promised. Create `src/my_analysis/filter_data/method.yaml`:

```yaml
inputs:
  data:
    type: .csv
    required: true

outputs:
  filtered:
    type: .csv
    required: true

params:
  min_quality:
    type: float
    required: false
    default: 0.5

env: first-pipeline
```

This says the method takes one CSV called `data`, produces one CSV called `filtered`, has a setting `min_quality` that is 0.5 unless a pipeline sets it, and runs in the `first-pipeline` environment. The [method.yaml Schema](../reference/method-yaml-schema.md) lists everything else a contract can declare: more inputs and outputs, other file types, settings with allowed values, the columns a table must have, and GPUs.

Then write the script that does the work, `src/my_analysis/filter_data/filter_data.py`, using the names from the contract:

```python
import pandas as pd
import wfc_client as wfc


@wfc.method
def filter_data(ctx):
    # 1. Inputs and settings, by their names in method.yaml
    data_path = ctx.input("data")[0]         # the CSV wired into the "data" input
    min_quality = ctx.params["min_quality"]  # 0.5 unless the pipeline sets it

    # 2. The analysis: ordinary pandas
    table = pd.read_csv(data_path)
    kept = table[table["quality"] >= min_quality]

    # 3. Outputs: write the file, then hand it to wfc under its method.yaml name
    out_path = ctx.workdir / "filtered.csv"
    kept.to_csv(out_path, index=False)
    ctx.save_artifact("filtered", out_path)
    ctx.log_metric("rows_kept", len(kept))   # a number shown on the run in the Canvas


# Lets wfc start the method when it runs this script
if __name__ == "__main__":
    wfc.run()
```

The script has three parts. The first reads the inputs and settings, the last saves the outputs, and only those two talk to wfc. The middle is your analysis, written the way you would write it anyway. `ctx.input` returns a list because one input can collect files from several upstream steps; with one file wired in, take the first.

Register it into the `my_analysis` module:

```bash
wfc register-method src/my_analysis/filter_data --module my_analysis
```

wfc checks the contract and that the `first-pipeline` environment is registered, then saves a copy of the method in the project and commits it to git. Runs use that saved copy, so to change the method later, edit the script and run `wfc register-method` again. [Writing a Method in Python](authoring-a-method-script.md) covers scripts and contracts in full.

### 6. Register a sample

A sample is a data file your pipeline reads. In this tutorial it is a small table of four cells, each with a quality score:

| id | quality |
|---|---|
| cell_1 | 0.9 |
| cell_2 | 0.3 |
| cell_3 | 0.7 |
| cell_4 | 0.2 |

Save it as `raw/cells.csv`: a first line `id,quality`, then one line per cell, such as `cell_1,0.9`.

Optionally, give the sample a description for the Canvas to show next to it, in `raw/sample.yaml`:

```yaml
description: Four cells with a quality score
```

Register it:

```bash
wfc register-sample --name cells --source raw/cells.csv --manifest raw/sample.yaml
```

`--name` is what pipelines call the sample. Leave out `--manifest` if you have no description. wfc keeps its own copy of the file, so a pipeline always reads exactly what you registered, and your file stays where it is.

### 7. Write and run the pipeline

A pipeline says which samples go into which methods. Pipelines are usually built in the Canvas, which writes this file for you; see [Build, Run and Inspect in the Canvas](../how-to/canvas.md). Here you write a short one by hand. Create `pipeline.json` in the project root:

```json
{
  "name": "quality-filter",
  "nodes": [
    {"id": "input", "type": "input_selector", "samples": ["cells"]},
    {"id": "filter", "method": "filter_data", "module": "my_analysis",
     "params": {"min_quality": 0.5}}
  ],
  "links": [
    {"source": "input", "target": "filter", "target_slot": "data"}
  ]
}
```

Here is what each part does:

- **`name`** is the pipeline's name in the Canvas's History tab.
- **`nodes`** are the boxes you would see in the Canvas. Each `id` is a name for its node, used only inside this file so the links can refer to it.
  - The node `input` is an **input selector** because of its `"type"`: it picks which registered samples go in, here `cells`.
  - The node `filter` runs the `filter_data` method from the `my_analysis` module, with `min_quality` set to 0.5. A node without a `type` is a method node.
- **`links`** connect the boxes. This one sends the sample from `input` into `filter`'s `data` input, the input named in the method's contract.

[Pipeline Document](../reference/pipeline-document.md) lists every key a pipeline file takes.

Run it:

```bash
wfc run-pipeline --pipeline pipeline.json
```

wfc checks the pipeline, runs the step in the `first-pipeline` environment's container, records the run, and archives the output. The command ends with a summary:

```text
Status: succeeded
Total runs: 1  |  Passed: 1  |  Failed: 0  |  Cached: 0  |  Cancelled: 0
```

Run the same command again. Nothing has changed, so wfc reuses the earlier result instead of running the step, and the summary shows `Cached: 1`. Change `min_quality` in `pipeline.json` and the step runs again.

### 8. Look at the result

Open the Canvas:

```bash
wfc canvas
```

Go to `http://127.0.0.1:8500` and open the **History** tab. Select a `filter_data` run. Its detail panel shows the run ID and has tabs for the run's parameters, metrics and artifacts. Stop the Canvas with Ctrl+C.

To copy the output out of wfc, use `wfc export --run <run-id> --slot <slot> --dest <dest>`:

- `--run` is the run ID from the History tab or from `wfc runs`, for example `1`;
- `--slot` is the output name from `method.yaml`, here `filtered`;
- `--dest` is the file or folder to copy it to.

```bash
wfc export --run 1 --slot filtered --dest filtered.csv
```

`filtered.csv` holds the two rows with `quality` of at least 0.5. If you leave out `--slot` and `--dest`, wfc names the run's outputs. The other export options are in the [CLI Reference](../reference/cli-reference.md#cache-and-export-commands).

## Next Steps

- [Exploring the Demo](wfc-demo.md): a five-method pipeline over three samples, set up with `wfc demo`.
- [Share runs as a bundle anyone can rerun](../how-to/run-and-inspect-results.md#share-runs-as-a-bundle-anyone-can-rerun): export your pipeline's result as one zip that reruns on another computer and checks each output.
- [Writing a Method in Python](authoring-a-method-script.md): the script API.
- [Write an R Method](../how-to/write-an-r-method.md): a method written in R.
- [Registering an Environment](registering-an-environment.md): capturing conda and pixi environments, using your own image, and working inside an environment's container.
- [Writing Contracts](writing-contracts.md): declare the inputs, outputs and parameters that wire methods together.
- [Build, Run and Inspect in the Canvas](../how-to/canvas.md): build and run pipelines in the browser, and browse past runs in History.
- [Run and Inspect from the Command Line](../how-to/run-and-inspect-results.md): run, inspect and export from the command line.
- [Project Anatomy](../explanation/project-anatomy.md): what each directory and file in a project is for.
