<!-- generated from pm_mvp::docs/consumer/tutorials/authoring-a-method-script @ 13d90e300f92; do not edit -->

# Tutorial: Writing a Method in Python

## Writing a Method in Python

A *method* is one step of a pipeline: it takes input files and parameters, does its work, and writes output files. In this tutorial you write a small Python method that keeps the rows of a CSV whose `score` is above a threshold, then register it.

The script uses the `wfc-client` decorator: you decorate one function, read inputs from a context object, and declare each output you write. Methods can also be written in R or bash; see [Write an R Method](../how-to/write-an-r-method.md) and the [Method Script Contract](../reference/method-script-contract.md).

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

A `module.yaml` can also list outputs every method in the module must provide; [Writing Contracts](writing-contracts.md) covers that. wfc keeps its own registered copy of each method under `.wfc/registry/methods/<module>/<name>/`; write your code under `src/`, not there.

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
| `ctx.input(slot)` | The input files for a slot, as a list of paths. |
| `ctx.params` | The node's parameter values, as a dict. A parameter you leave unset is in it at its declared default. |
| `ctx.run_dir` | The run directory. Everything you save must be inside it. |
| `ctx.workdir` | A scratch directory inside the run directory. |
| `ctx.save_artifact(slot, path)` | Declares that the file (or directory) at `path` is the output for `slot`. |
| `ctx.log_metric(name, value)` | Records a number or short string for this run. |
| `wfc.run()` | Runs the decorated function. |

A parameter you leave unset in the canvas arrives at the `default` you wrote in `method.yaml` (here `threshold` is `0.5`), and the run records it. A parameter with no `default` arrives only when it is set.

When you register the method, wfc checks each `ctx.save_artifact` call in the decorated function against `method.yaml`: an output name that isn't declared, or a declared output that is never saved, stops the registration. Write the output name as a string literal in the decorated function so the check can see it.

When the function returns, `wfc.run()` writes a small `_wfc_results.json` file listing your saved outputs and metrics. wfc reads it after the method exits.

The decorator is optional. Underneath it, wfc passes the method its inputs and parameters in environment variables and collects the files it writes. The [Method Script Contract](../reference/method-script-contract.md) lists them, with this method written using only the standard library.


## Step 4 — Register the method

Methods belong to a module. From the project root, register the module once, then the method:

```bash
wfc register-module --name my_analysis --module-dir src/my_analysis
wfc register-method src/my_analysis/filter_data --module my_analysis
```

`--module-dir` points at the directory holding `module.yaml`. `wfc register-method <method-dir> --module <module>` takes the method's directory as its one positional argument, and `--module` names the module it joins. See the [CLI Reference](../reference/cli-reference.md) for every flag.

Registration reads `method.yaml`, checks it, and checks that the environment named in `env:` is registered. It copies the method's script files and `method.yaml` into `.wfc/registry/methods/my_analysis/filter_data/` and commits that copy to the project's git repository. It first says whether this registers a new method, adds a new version or adds nothing, and asks `Proceed? [y/N]` before it adds one (pass `--yes` to skip the question). A run executes the registered code, and wfc compares against it to decide whether a step must recompute.

To change the method, edit the script (or `method.yaml`) in `src/my_analysis/filter_data/`, then run `wfc register-method` again. Each change adds a new version; earlier versions stay runnable, and a pipeline node can pin one.

A method name is unique within its module; two modules may each have a method of the same name.

The method now appears in the canvas sidebar under its module, ready to drop into a pipeline.


## Writing outputs that rerun the same

When someone reruns an exported run, each output is compared byte for byte with the recorded one. A method that writes the same bytes every time gives a clean `same`. To get there:

- Fix random seeds, and take them from a parameter so they are recorded with the run.
- Don't embed the time the file was made. Most plotting and PDF libraries can leave it out.
- Sort rows before writing them, so the order does not depend on which parallel job finished first.
- Set the precision of numbers in final tables, so the last digits don't vary between machines.
- Use one thread when exact repeatability matters.

If an output still says `DIFFERENT`, see [If a step says DIFFERENT](../how-to/run-and-inspect-results.md#if-a-step-says-different).

## Next steps

- Wire the method into a pipeline and run it: [Build, Run and Inspect in the Canvas](../how-to/canvas.md).
- Declare the columns your slots carry and add column pickers: [Writing Contracts](writing-contracts.md).
- Look up any `method.yaml` key: [method.yaml Schema](../reference/method-yaml-schema.md).
- See what happens when a step runs: [How a Run Executes](../explanation/how-a-run-executes.md).

## Inputs are read-only

A method reads its inputs and writes its results to the step's output folder. Open input files in read mode.

A method that saves back over its input fails at the save. Do not write into or beside an input: on Linux and macOS a tool that does fails, because the folder holding an input is read-only. Three cases to watch for:

- A reader that writes an index or memo file next to the input (Bio-Formats' memo files, for example). Point it at a folder in the step's working directory.
- A SQLite database in WAL mode, which needs `-wal` and `-shm` files beside it even to read. Open it with `?mode=ro&immutable=1`, or copy it into the step's working folder first.
- A file opened for update, such as HDF5 or `h5ad` in `r+` or `a` mode, or a zarr store being appended to. Open it read-only and write a new output.
