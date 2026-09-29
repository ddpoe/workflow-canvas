<!-- generated from pm_mvp::docs.consumer.tutorials.getting-started @ 24a12dc2f117; do not edit -->

# Tutorial: Your First Pipeline

## Introduction

Workflow Canvas (wfc) runs your analysis scripts as pipeline steps, records what produced every result, and reuses a step's earlier result when its code, parameters, inputs and environment have not changed.

This tutorial takes you from installation to a one-step pipeline you built yourself: you create a project, an environment, a module, a method and a sample, run the pipeline, and look at the result. To see a complete pipeline before writing anything, install wfc and then follow [Exploring the Demo](wfc-demo.md). For the bigger picture, read [What is Workflow Canvas?](../index.md).

## Prerequisites

You need:

- **Python 3.11 or 3.12** to install and run wfc.
- **Docker**, installed and running. Each step runs in a container: a packaged copy of the software and dependencies your method needs (see [How the Pieces Fit Together](../explanation/how-the-pieces-fit-together.md)). On Windows and macOS this is Docker Desktop; on Linux, Docker Engine.
- **Git**. wfc records the commit of your method code with every run and will not run while tracked files have uncommitted changes. Everything stays local: no account or remote is needed.
- **[pixi](https://pixi.sh)**, used in this tutorial to describe the method's environment.

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

```bash
wfc init --dir wfc_project_root
cd wfc_project_root
```

`wfc init` asks one question, where to keep the output archive. Press Enter to accept the default, `~/.wfc/archives/wfc_project_root`. It then:

- creates `.wfc/` (the config file `wf-canvas.toml` and the project database), the folders wfc manages, and a `.gitignore`;
- sets up DVC with that archive;
- runs `git init` if needed and commits the files it wrote;
- prints a health table for git, the archive, Docker and samples.

To skip the question, pass `--yes` (accept every default) or `--archive PATH`. Running `wfc init` again is safe: it only adds what is missing.

If something is not ready, `wfc doctor` prints the same health table with a fix hint for each failing check. Run it whenever a run refuses to start.

The archive holds your outputs and the database in `.wfc/` indexes them. Back up `.wfc/` together with the archive folder.

### 2. Register an environment

The environment is the software your method runs with. Describe it as a pixi project, and wfc builds a container from its lock file. Create one in `env/` with Python, pip and the `wfc-client` authoring package:

```bash
pixi init env --platform linux-64 --platform win-64
pixi add --manifest-path env/pixi.toml python=3.12 pip
pixi add --manifest-path env/pixi.toml --pypi wfc-client
```

Containers run Linux, so the project must list `linux-64`. The second platform is your own machine, so pixi can also install the environment locally: use `osx-arm64` or `osx-64` on a Mac, and leave it out on Linux.

Register the lock file:

```bash
wfc register-env default --backend pixi --from env/pixi.lock
```

The name you register must be an environment defined in the lock; a new pixi project has one, `default`. wfc reads `pixi.toml` from beside the lock, builds the image, records it in `.wfc/envs.json`, commits that file, and prints the recorded image reference.

To capture a conda environment or pixi project you already have, or to use a container image you built yourself, see [Registering an Environment](registering-an-environment.md).

### 3. Register a module

A module groups related methods. Create `src/my_analysis/module.yaml`:

```yaml
description: Table filtering utilities
```

Then register it, pointing `--module-dir` at that folder:

```bash
wfc register-module --name my_analysis --module-dir src/my_analysis
```

`register-module` reads `module.yaml` from the folder you pass. A module can also declare output contracts that every method in it must provide; see [Writing Contracts](writing-contracts.md).

### 4. Write and register a method

A method is a folder with a script and a `method.yaml`. Create `src/my_analysis/filter_data/filter_data.py`:

```python
import csv

import wfc_client as wfc


@wfc.method
def filter_data(ctx):
    with open(ctx.input("data")[0], newline="") as f:
        rows = list(csv.DictReader(f))

    min_quality = float(ctx.params["min_quality"])
    kept = [r for r in rows if float(r["quality"]) >= min_quality]

    out_path = ctx.workdir / "filtered.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(kept)
    ctx.save_artifact("filtered", out_path)
    ctx.log_metric("rows_kept", len(kept))


if __name__ == "__main__":
    wfc.run()
```

And `src/my_analysis/filter_data/method.yaml`, which declares the input, output and parameter the script uses, and the environment it runs in:

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

env: default
```

Register it into the `my_analysis` module:

```bash
wfc register-method src/my_analysis/filter_data --module my_analysis
```

Registration reads the script's parameters, stores the `method.yaml` contract, checks that the `default` environment is registered, and commits the method's files to git. To change the method later, edit the script and run `wfc register-method` again. [Authoring a Method Script](authoring-a-method-script.md) explains the script API and `method.yaml` in full.

### 5. Register a sample

A sample is an input data file. Create `raw/cells_a.csv`:

```text
id,quality
cell_1,0.9
cell_2,0.3
cell_3,0.7
cell_4,0.2
```

Optionally, describe it in `raw/sample.yaml`. Its only key is `description`, which the Canvas shows next to the sample:

```yaml
description: Four cells with a quality score
```

Register it:

```bash
wfc register-sample --name cells_a --source raw/cells_a.csv --manifest raw/sample.yaml
```

Leave out `--manifest` if you have no description. wfc stores a copy of the file's content and leaves your file where it is. When a step needs the sample, wfc retrieves it.

### 6. Write and run the pipeline

Pipelines are usually built in the Canvas, which writes the pipeline file for you; see [Build, Run and Inspect in the Canvas](../how-to/canvas.md). Here you write a short one by hand. An `input_selector` node chooses the samples, a method node names a registered method and its parameters, and a link feeds the sample into the method's `data` input. Create `pipeline.json` in the project root:

```json
{
  "nodes": [
    {"id": "samples", "type": "input_selector", "samples": ["cells_a"]},
    {"id": "filter", "method": "filter_data", "module": "my_analysis",
     "params": {"min_quality": 0.5}}
  ],
  "links": [
    {"source": "samples", "target": "filter", "target_slot": "data"}
  ]
}
```

Run it:

```bash
wfc run-pipeline --pipeline pipeline.json
```

wfc checks the pipeline, runs the step in the `default` environment's container, records the run, and archives the output. The command ends with a summary:

```text
Status: completed
Total runs: 1  |  Passed: 1  |  Failed: 0  |  Cached: 0  |  Cancelled: 0
```

Run the same command again. Nothing has changed, so wfc reuses the earlier result instead of running the step, and the summary shows `Cached: 1`. Change `min_quality` in `pipeline.json` and the step runs again.

### 7. Look at the result

Open the Canvas:

```bash
wfc canvas
```

Go to `http://127.0.0.1:8500` and open the **History** tab. Select a `filter_data` run. Its detail panel shows the run ID and has tabs for the run's parameters, metrics and artifacts. Stop the Canvas with Ctrl+C.

To copy the output out of wfc, use `wfc export <run-id> <slot> <dest>`:

- `<run-id>` is the run ID from the History tab, for example `1`;
- `<slot>` is the output name from `method.yaml`, here `filtered`;
- `<dest>` is the file or folder to copy it to.

```bash
wfc export 1 filtered filtered.csv
```

`filtered.csv` holds the two rows with `quality` of at least 0.5. If you leave out `<slot>` and `<dest>`, wfc names the run's outputs. The other export options are in the [CLI Reference](../reference/cli-reference.md#cache-and-export-commands).

## Next Steps

- [Exploring the Demo](wfc-demo.md): a five-method pipeline over three samples, set up with `wfc demo`.
- [Authoring a Method Script](authoring-a-method-script.md): the script API, and methods written in R or bash.
- [Registering an Environment](registering-an-environment.md): capturing conda and pixi environments, using your own image, and working inside an environment's container.
- [Writing Contracts](writing-contracts.md): declare the inputs, outputs and parameters that wire methods together.
- [Build, Run and Inspect in the Canvas](../how-to/canvas.md): build and run pipelines in the browser, and browse past runs in History.
- [Run and Inspect from the Command Line](../how-to/run-and-inspect-results.md): run, inspect and export from the command line.
- [Project Anatomy](../explanation/project-anatomy.md): what each directory and file in a project is for.
