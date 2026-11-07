<!-- generated from pm_mvp::docs/consumer/tutorials/authoring-a-method-script @ 238ce1946465; do not edit -->

# Write a Method in Python

## Writing a Method in Python

A *method* is one step of a pipeline: it takes input files and parameters, does its work, and writes output files. In this tutorial you write a small Python method that keeps the rows of a CSV whose `score` is above a threshold, then register it.

The script uses the `wfc-client` decorator: you decorate one function, read inputs from a context object, and declare each output you write. Methods can also be written in R or bash; see [Write a Method in R](../how-to/write-an-r-method.md) and the [Method Script Contract](../reference/method-script-contract.md).

You need a project first; [Your First Pipeline](getting-started.md) sets one up. The commands on this page run from the project root, called `wfc_project_root` here (`wfc init --dir wfc_project_root`, then `cd wfc_project_root`).

Each step runs in a container: a packaged copy of the software and dependencies your method needs (see [The Tools Behind Workflow Canvas](../explanation/how-the-pieces-fit-together.md)). wfc calls this an environment, and you need one registered for the method to run in; [Register an Environment](registering-an-environment.md) covers it.


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

A `module.yaml` can also list outputs every method in the module must provide; [Register a Module](../how-to/register-a-module.md) covers that. When you register a method, wfc keeps its own copy of it; edit your code under `src/`.

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

Every key and field is listed in the [method.yaml Schema](../reference/method-yaml-schema.md). To let users pick an output's columns from a list in the canvas, see [Let Users Pick Columns in the Canvas](../how-to/pick-columns-in-the-canvas.md).


## Step 3 — Write the script with the wfc-client decorator

`wfc-client` is a small Python package that handles the bookkeeping between your method and wfc. This example also reads and writes the CSV with pandas. Add both `wfc-client` and `pandas` to your environment's dependencies before you build the environment with `wfc register-env`.

Write `src/my_analysis/filter_data/filter_data.py`:

```python
import pandas as pd

import wfc_client as wfc

@wfc.method
def filter_data(ctx):
    data_path = ctx.input("data")                 # the file wired into the "data" input
    threshold = float(ctx.params["threshold"])

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
| `ctx.input(slot)` | The file wired into an input, as a path. For an input declared `multiple: true`, a list of paths. |
| `ctx.params` | The node's parameter values, as a dict. A parameter you leave unset is in it at its declared default. |
| `ctx.run_dir` | The run directory. Everything you save must be inside it. |
| `ctx.workdir` | A scratch directory inside the run directory. |
| `ctx.save_artifact(slot, path)` | Declares that the file (or directory) at `path` is the output for `slot`. |
| `ctx.log_metric(name, value)` | Records a number or short string for this run. |
| `wfc.run()` | Runs the decorated function. |

A parameter with no `default` is in `ctx.params` only when it is set.

When you register the method, wfc checks each `ctx.save_artifact` call in the decorated function against `method.yaml`: an output name that isn't declared, or a declared output that is never saved, stops the registration. Write the output name as a string literal in the decorated function so the check can see it.

The decorator is optional. Underneath it, wfc passes the method its inputs and parameters in environment variables and collects the files it writes. The [Method Script Contract](../reference/method-script-contract.md) lists them, with this method written using only the standard library.


## Step 4 — Register the method

Methods belong to a module. From the project root, register the module once, then the method:

```bash
wfc register-module --name my_analysis --module-dir src/my_analysis
wfc register-method src/my_analysis/filter_data --module my_analysis
```

Once the module is registered, you can try the method before you register it:

```bash
wfc try src/my_analysis/filter_data --module my_analysis --sample <sample>
```

Nothing is registered or recorded. The command ends with a `wfc export` line for each output, which copies that output out. [Draft a Method](drafting-a-method.md) covers trying a method before you register it, from the command line or in the canvas.

See the [Registration Commands](../reference/cli-registration.md#wfc-register-method) for every flag.

Registration checks `method.yaml` and that its environment is registered, then saves a copy of the method in the project and commits it to git; runs use that copy. It says first whether this adds a new method or a new version, and asks `Proceed? [y/N]`.

To change the method, edit the script (or `method.yaml`) in `src/my_analysis/filter_data/`, then run `wfc register-method` again. Each change adds a new version; earlier versions stay runnable, and a pipeline node can pin one.

The method now appears in the canvas sidebar under its module, ready to drop into a pipeline.


## Writing outputs that rerun the same

When someone reruns an exported run, each output is compared byte for byte with the recorded one. A method that writes the same bytes every time gives a clean `same`. To get there:

- Fix random seeds, and take them from a parameter so they are recorded with the run.
- Don't embed the time the file was made. Most plotting and PDF libraries can leave it out: with matplotlib, `savefig("plot.pdf", metadata={"CreationDate": None})`. A gzip file stores one in its header too: `gzip.GzipFile(path, "wb", mtime=0)`.
- Sort rows before writing them, so the order does not depend on which parallel job finished first. Loop over a Python `set` of strings in sorted order too: Python orders it differently in each new process.
- Set the precision of numbers in final tables, so the last digits don't vary between machines.
- Use one thread when exact repeatability matters.

If an output still says `DIFFERENT`, see [If a step says DIFFERENT](../how-to/share-bundle.md#if-a-step-says-different).

## Next steps

- Wire the method into a pipeline and run it: [Wire a Pipeline](../how-to/wire-a-pipeline.md).
- Declare the options your outputs offer and add options pickers: [Let Users Pick Columns in the Canvas](../how-to/pick-columns-in-the-canvas.md).
- Look up any `method.yaml` key: [method.yaml Schema](../reference/method-yaml-schema.md).
- See what happens when a step runs: [How a Run Executes](../explanation/how-a-run-executes.md).

## Inputs are read-only

A method reads its inputs and writes its results to the step's output folder. Open input files in read mode.

A method that saves back over its input fails at the save. Do not write into or beside an input: on Linux and macOS a tool that does fails, because the folder holding an input is read-only. Three cases to watch for:

- A reader that writes an index or memo file next to the input (Bio-Formats' memo files, for example). Point it at a folder in the step's working directory.
- A SQLite database in WAL mode, which needs `-wal` and `-shm` files beside it even to read. Open it with `?mode=ro&immutable=1`, or copy it into the step's working folder first.
- A file opened for update, such as HDF5 or `h5ad` in `r+` or `a` mode, or a zarr store being appended to. Open it read-only and write a new output.
