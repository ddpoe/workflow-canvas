<!-- generated from pm_mvp::docs.consumer.tutorials.authoring-a-method-script @ 16391e807643; do not edit -->

# Tutorial: Authoring a Method Script

## Authoring a Method Script

A *method* is one step of a pipeline: it takes input files and parameters, does its work, and writes output files. In this tutorial you write a small method that keeps the rows of a CSV whose `score` is above a threshold, then register it.

You can write a method two ways:

1. **With the `wfc-client` decorator** (Python). You decorate one function, read inputs from a context object, and declare each output you write.
2. **Directly against environment variables and files**, in any language. wfc tells the method where its inputs and parameters are through a few environment variables, and the method writes its outputs into a run directory. The decorator is a thin layer over this.

You need a project first; [Getting Started](getting-started.md) sets one up. The commands on this page run from the project root, called `wfc_project_root` here (`wfc init --dir wfc_project_root`, then `cd wfc_project_root`).

Each step runs in a container: a packaged copy of the software and dependencies your method needs (see [How the Pieces Fit Together](../explanation/how-the-pieces-fit-together.md)). wfc calls this an environment, and you need one registered for the method to run in; [Registering an Environment](registering-an-environment.md) covers it.


## Step 1 — Create the method directory

A method is a directory holding two files:

| File | Purpose |
|---|---|
| `{method_name}.py` (or `.R`, `.r`, `.sh`) | The method's script, in Python, R or bash. |
| `method.yaml` | Declares the method's inputs, outputs, parameters and environment. |

The script is named after the directory. Methods belong to a module, and the suggested place for your own code is `src/<module>/<method>/`, with the module's `module.yaml` in `src/<module>/`:

```
wfc_project_root/
  src/
    my_analysis/
      module.yaml
      filter_data/
        filter_data.py
        method.yaml
```

Create those directories now, and write a one-line `src/my_analysis/module.yaml`:

```yaml
description: Filtering steps for scored data
```

A `module.yaml` can also list outputs every method in the module must provide; [Writing Contracts](writing-contracts.md) covers that. wfc keeps its own registered copy of each method under `methods/<name>/`; write your code under `src/`, not there.

To use a different script name, set `script:` in `method.yaml` (see the [method.yaml Schema](../reference/method-yaml-schema.md)).

You can put helper scripts beside the main script and import or source them as usual. Every `.py`, `.R`, `.r` and `.sh` file in the method directory counts as part of the method: when you edit any of them and run `wfc register-method` again, the next run recomputes. Keep scratch scripts somewhere else. To list the helper files explicitly, use `helpers:` in `method.yaml`.


## Step 2 — Declare the slots in method.yaml

`method.yaml` names the method's input and output *slots*, its parameters, and its environment. The canvas uses it to draw the node, and the script uses the same names to find its files.

Write `src/my_analysis/filter_data/method.yaml`:

```yaml
inputs:
  data:
    type: csv
    description: Scored rows to filter.

outputs:
  filtered:
    type: csv
    description: Rows whose score exceeds the threshold.

params:
  threshold:
    type: float
    default: 0.5

env: my-analysis
```

- **`inputs`**: each name is an input slot. A method needs at least one.
- **`outputs`**: each name is an output slot. The `type` is the file extension, so the `filtered` output is `filtered.csv`.
- **`params`**: each parameter gets an editor in the canvas inspector.
- **`env`**: the name of a registered environment. Replace `my-analysis` with yours.

Every key and field is listed in the [method.yaml Schema](../reference/method-yaml-schema.md). Column declarations are covered in [Writing Contracts](writing-contracts.md).

## Step 3 — Write the script with the wfc-client decorator

`wfc-client` is a small Python package that handles the bookkeeping between your method and wfc. This example also reads and writes the CSV with pandas. Add both `wfc-client` and `pandas` to your environment's dependencies before you build the environment with `wfc register-env`.

Write `src/my_analysis/filter_data/filter_data.py`:

```python
import pandas as pd

import wfc_client as wfc


@wfc.method
def filter_data(ctx):
    data_path = ctx.input("data")[0]              # the file wired into the "data" slot
    threshold = float(ctx.params.get("threshold", 0.5))

    df = pd.read_csv(data_path)
    kept = df[df["score"] > threshold]

    out_path = ctx.workdir / "filtered.csv"
    kept.to_csv(out_path, index=False)

    ctx.save_artifact("filtered", out_path)       # "filtered" is the output slot
    ctx.log_metric("kept_rows", len(kept))


if __name__ == "__main__":
    wfc.run()
```

| Member | Purpose |
|---|---|
| `@wfc.method` | Marks the method's function. A script has exactly one. |
| `ctx.input(slot)` | The input files for a slot, as a list of paths. |
| `ctx.params` | The parameter values set on the node, as a dict. |
| `ctx.run_dir` | The run directory. Everything you save must be inside it. |
| `ctx.workdir` | A scratch directory inside the run directory. |
| `ctx.save_artifact(slot, path)` | Declares that the file (or directory) at `path` is the output for `slot`. |
| `ctx.log_metric(name, value)` | Records a number or short string for this run. |
| `wfc.run()` | Runs the decorated function. |

A parameter you leave unset in the canvas is not in `ctx.params`, so read it with `.get()` and the same default you wrote in `method.yaml`.

When you register the method, wfc checks each `ctx.save_artifact` call in the decorated function against `method.yaml`: an output name that isn't declared, or a declared output that is never saved, stops the registration. Write the output name as a string literal in the decorated function so the check can see it.

When the function returns, `wfc.run()` writes a small `_wfc_results.json` file listing your saved outputs and metrics. wfc reads it after the method exits.


## Step 4 — Or write against the environment variables directly

Underneath the decorator, a method is a process that reads a few environment variables and writes files. You can write a method this way in any language with no wfc package installed. This is how R and bash methods work.

### What wfc gives the method

| Variable | Contents |
|---|---|
| `WFC_RUN_DIR` | The run directory. Write your outputs here. |
| `WFC_INPUT_PATHS` | JSON `{slot: [paths]}`: the files (or directories) wired into each input slot. |
| `WFC_PARAMS` | JSON `{name: value}`: the parameter values set on the node. Unset parameters are absent. |
| `WFC_RUN_ID` | The run's id. |
| `WFC_SAMPLE` | The sample this run is for (`__all__` for a step that bundles several samples). |
| `WFC_NODE_ID` | The node's id in the pipeline. |
| `WFC_PIPELINE_ID` | The pipeline run's id. |
| `WFC_VARIANT` | The parameter variant's name. |

### What the method gives back

- Write each output into `WFC_RUN_DIR`, named after its slot plus the slot's `type`: the `filtered` output with `type: csv` is `filtered.csv`. A `dir` output is a directory named after the slot.
- Exit 0 on success and non-zero on failure.
- Anything printed to stdout or stderr is kept in the run's log.

Every declared output must be there when the method exits, or the step fails. Other files in the run directory are ignored.

### The same method with the standard library

```python
import csv
import json
import os
from pathlib import Path

run_dir = Path(os.environ["WFC_RUN_DIR"])
inputs = json.loads(os.environ["WFC_INPUT_PATHS"])
params = json.loads(os.environ.get("WFC_PARAMS", "{}"))

threshold = float(params.get("threshold", 0.5))

with open(inputs["data"][0], newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = [r for r in reader if float(r["score"]) > threshold]

with open(run_dir / "filtered.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
```

### R

```r
inputs <- jsonlite::fromJSON(Sys.getenv("WFC_INPUT_PATHS"))
params <- jsonlite::fromJSON(Sys.getenv("WFC_PARAMS"))
threshold <- if (is.null(params$threshold)) 0.5 else params$threshold

rows <- read.csv(inputs$data[1])
write.csv(rows[rows$score > threshold, ],
          file.path(Sys.getenv("WFC_RUN_DIR"), "filtered.csv"), row.names = FALSE)
```

### bash

This example uses `jq`, so the environment needs it installed:

```bash
input=$(jq -r '.data[0]' <<< "$WFC_INPUT_PATHS")
threshold=$(jq -r '.threshold // 0.5' <<< "$WFC_PARAMS")
awk -F, -v t="$threshold" 'NR == 1 || $2 > t' "$input" > "$WFC_RUN_DIR/filtered.csv"
```

The bash example assumes `score` is the second column.

On Windows, save `.sh` and `.R` scripts with LF line endings (for example with `git config core.autocrlf input`). A bash script with CRLF line endings fails in the container, and the step reports that it did not produce its declared slot.

### Metrics without the decorator

To record metrics, write `_wfc_results.json` into `WFC_RUN_DIR`:

```json
{"outputs": {"filtered": "filtered.csv"}, "metrics": {"kept_rows": 42}}
```

Output paths are relative to the run directory, so a file listed here can have any name. Without this file, wfc looks for each output under its slot name and extension.

## Step 5 — Register the method

Methods belong to a module. From the project root, register the module once, then the method:

```bash
wfc register-module --name my_analysis --module-dir src/my_analysis
wfc register-method src/my_analysis/filter_data --module my_analysis
```

`--module-dir` points at the directory holding `module.yaml`. `wfc register-method <method-dir> --module <module>` takes the method's directory as its one positional argument, and `--module` names the module it joins. See the [CLI Reference](../reference/cli-reference.md) for every flag.

Registration reads `method.yaml`, checks it, and checks that the environment named in `env:` is registered. It commits the method directory to the project's git repository and copies the method's script files and `method.yaml` into `methods/filter_data/`. wfc compares against that copy to decide whether a step must recompute.

To change the method, edit the script (or `method.yaml`) in `src/my_analysis/filter_data/`, then run `wfc register-method` again.

Method names are unique across the project: registration refuses a name that another module already uses.

An R or bash method runs under the interpreter its environment was registered with. Register that environment with `--interpreter` pointing at `Rscript` or `bash` inside the image; [Registering an Environment](registering-an-environment.md) shows how.

The method now appears in the canvas sidebar under its module, ready to drop into a pipeline.


## Next steps

- Wire the method into a pipeline and run it: [Build, Run and Inspect in the Canvas](../how-to/canvas.md).
- Declare the columns your slots carry and add column pickers: [Writing Contracts](writing-contracts.md).
- Look up any `method.yaml` key: [method.yaml Schema](../reference/method-yaml-schema.md).
- See what happens when a step runs: [How a Run Executes](../explanation/how-a-run-executes.md).
