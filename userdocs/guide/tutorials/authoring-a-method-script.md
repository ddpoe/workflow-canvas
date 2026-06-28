<!-- generated from pm_mvp::docs.consumer.tutorials.authoring-a-method-script @ 03b33e9a9e43; do not edit -->

# Tutorial: Authoring a Method Script

## Authoring a Method Script

A *method* is the smallest unit of work in a pipeline: one step that takes some inputs and parameters, does something, and writes output files. This tutorial walks you through writing one from scratch.

There are two layers you can write against, and you'll meet both here:

1. **The `wfc-client` decorator** — the recommended path for Python authors. You decorate one function, read inputs off a context object, and declare your outputs. It's a tiny, pure-standard-library helper that does the bookkeeping for you.
2. **The canonical env-var + file contract** — the floor underneath the decorator. A method is really just a process that reads a handful of environment variables and writes some files. You can write a method this way in *any* language with *zero* dependencies, and that's what makes a recorded run reproducible forever.

We'll build the directory, write the script the recommended way, then show the exact contract it sits on. You only need a Python script and a small `method.yaml` to get started.

If you haven't set up a project yet, start with [[getting-started]] first — it scaffolds the project this method will live in.

## Step 1 — Lay out the method directory

A method is a self-contained directory built from one required file and one strongly-recommended file:

| File | Purpose |
|---|---|
| `{method_name}.py` | Python script containing the implementation (required) |
| `method.yaml` | Contract file declaring inputs, outputs, params, and env (recommended — without it the method registers but has no slot-level metadata in the database or canvas widgets) |

The script filename must match the directory name. A method called `filter_data` lives in a directory named `filter_data/` and its script is `filter_data.py`.

### Where methods live

**Nested under a module (typical):**
```
modules/{module_name}/{method_name}/
  {method_name}.py
  method.yaml
```
Example: `modules/binary_label_classification/train_classifier/train_classifier.py`

**Flat standalone:**
```
methods/{method_name}/
  {method_name}.py
  method.yaml
```
Example: `methods/feature_qc/feature_qc.py`

Both layouts register the same way:
```bash
wfc register-method modules/my_analysis/preprocess --module my_analysis
wfc register-method methods/feature_qc --module data_tools
```

At registration, `wfc` scans the script, reads `method.yaml`, and snapshots the whole method directory into `methods/{method_name}/` for code fingerprinting — regardless of where the source lives. That snapshot is what the cache keys against, so the same code always resolves to the same cached results.

## Step 2 — Write the script with the wfc-client decorator

The recommended way to write a method is the **`wfc-client`** decorator. It's a tiny, pure-standard-library package you add to your method's environment — no pandas, no database, no dependency on the `wfc` engine itself. You decorate one function with `@wfc.method`, write your output files, and declare each one with `ctx.save_artifact(name, path)`.

### Install

```bash
pip install wfc-client
```

Add `wfc-client` to your method's environment like any other dependency. It pulls in nothing else.

### The decorator surface

```python
import wfc_client as wfc


@wfc.method
def filter_data(ctx):
    # Resolve the "data" input slot declared in method.yaml.
    data_path = ctx.input("data")[0]          # list[Path] from the resolved inputs
    threshold = ctx.params.get("threshold", 0.5)

    import csv
    with open(data_path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if float(r["score"]) > threshold]

    # Write the file yourself, anywhere inside the run dir (ctx.workdir is a
    # scratch dir at run_dir/_workdir/), then declare it.
    out_path = ctx.workdir / "filtered.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    ctx.save_artifact("filtered", out_path)    # name must match method.yaml outputs
    ctx.log_metric("kept_rows", len(rows))


if __name__ == "__main__":
    wfc.run()
```

| Member | Purpose |
|---|---|
| `@wfc.method` | Marks the single entry-point function. Exactly one per method module. |
| `ctx.input(slot)` | Returns `list[Path]` of resolved input files for an input slot. |
| `ctx.params` | Dict of params (`method.yaml` defaults merged with pipeline overrides). |
| `ctx.run_dir` | The directory `wfc` reads after your method exits. |
| `ctx.workdir` | Scratch dir at `run_dir/_workdir/`, created on first access. |
| `ctx.save_artifact(name, path)` | Declares that the file at `path` is the output `name`. `path` must resolve **inside** the run dir or you get an immediate error. |
| `ctx.log_metric(name, value)` | Records a scalar metric. |
| `wfc.run()` | The entry point — resolves your one decorated function, builds `ctx`, runs it. |

The decorator **never touches your data bytes**. `save_artifact(name, path)` records *which* file is the declared output; `wfc` does the hashing and archiving on the host afterwards. There is **no return value** — outputs flow only through `ctx.save_artifact`, metrics only through `ctx.log_metric`. Trying to save a file written outside the run dir raises an immediate, clear error.

When your method exits, the client writes a single `_wfc_results.json` manifest recording your declared outputs (as paths relative to the run dir) and metrics. `wfc` reads that one file, resolves each output, hashes it into the content cache, and records the run. A missing required output, or a non-zero exit, fails the step.

## Step 3 — Understand the contract underneath

The decorator is sugar over a contract `wfc` guarantees: **a method is just a process that reads a few environment variables and writes its declared output files.** You can write a method against this contract directly with *zero* dependencies — not even `wfc-client` — in any language. That's also what makes a method rerunnable forever: the contract is plain env vars and files, so a recorded run can be reproduced without any specific client version.

### The contract

Before launching your script, `wfc` sets these environment variables:

| Variable | Type | Meaning |
|---|---|---|
| `WFC_RUN_DIR` | path | Directory to write your declared outputs into. Everything you produce goes here. |
| `WFC_INPUT_PATHS` | JSON | `{slot_name: [absolute paths]}` — resolved input files for each input slot in `method.yaml`. |
| `WFC_PARAMS` | JSON | `{param_name: value}` — params from `method.yaml` defaults merged with pipeline overrides. |
| `WFC_RUN_ID` | int | Unique run identifier. |
| `WFC_SAMPLE` | str | Current sample name. |
| `WFC_NODE_ID` | str | Node identifier within the pipeline. |
| `WFC_PIPELINE_ID` | str | Pipeline identifier for this execution. |
| `WFC_VARIANT` | str | Variant name for this run. |

Your contract back to `wfc`:

- Read inputs and params from those env vars.
- Write each declared output to `${WFC_RUN_DIR}/<output_name>.<ext>` matching your `method.yaml` `outputs:` declarations.
- Print whatever you like to stdout/stderr — `wfc` captures both into the run logs automatically.
- **Exit 0 on success, non-zero on failure.** That is how `wfc` knows whether the step succeeded.

### The same method, stdlib only

This is the Step 2 example rewritten with no imports from `wfc` or `wfc-client` — just the standard library reading the env vars directly. It mirrors the in-repo fixture methods (`tests/fixtures/methods/heartbeat/heartbeat.py`, `tests/fixtures/methods/qc/qc.py`):

```python
import csv
import json
import os
from pathlib import Path


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    input_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    params = json.loads(os.environ.get("WFC_PARAMS", "{}"))

    data_paths = input_paths.get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")

    threshold = float(params.get("threshold", 0.5))

    with open(data_paths[0], newline="") as f:
        rows = [r for r in csv.DictReader(f) if float(r["score"]) > threshold]

    # Write the declared output "filtered" into WFC_RUN_DIR.
    out_path = run_dir / "filtered.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
```

The script reads `WFC_RUN_DIR` / `WFC_INPUT_PATHS` / `WFC_PARAMS`, writes its outputs into `WFC_RUN_DIR`, and exits 0. It imports nothing from `wfc` — pandas, R, bash, or any other language works the same way as long as the script honors the env-var + file contract.

### Recording metrics without the client

The `wfc-client` decorator writes a single `_wfc_results.json` manifest at exit (declared outputs + metrics). A plain env-var + file method can do the same by hand if it wants to record metrics: write `${WFC_RUN_DIR}/_wfc_results.json` of shape `{"outputs": {name: run-dir-relative-path}, "metrics": {name: value}}`. If you only produce output files and no metrics, you can omit the manifest entirely — `wfc` scans `WFC_RUN_DIR` for the declared output filenames instead.

### What wfc does after your script exits

When your process exits 0, `wfc` reads `_wfc_results.json` if present (the single results channel for outputs and metrics); otherwise it scans `WFC_RUN_DIR` for the output filenames declared in `method.yaml`. Each declared output is hashed into the content cache and the run is recorded. Undeclared files in `WFC_RUN_DIR` are ignored. A missing required output, or a non-zero exit, fails the step.

Note that the `wfc` engine itself runs on the *host* — the machine that launches your method — and reaches your method only through these env vars and files. Your method's environment contains only your declared dependencies plus Python, and, if you opted into the decorator, the pure-stdlib `wfc-client`. The full `wfc` package is never installed alongside your method.

## Step 4 — Declare slots in method.yaml

Both versions of the script above refer to an input slot named `data`, an output named `filtered`, and a `threshold` param. Those names come from `method.yaml`, the contract file that sits next to your script. At registration `wfc` parses it into the slot-level metadata the database and canvas use to wire pipelines together.

A minimal `method.yaml` for our example:

```yaml
inputs:
  data:
    type: .csv
    description: Scored rows to filter.

outputs:
  filtered:
    type: .csv
    description: Rows whose score exceeds the threshold.

params:
  threshold:
    type: float
    default: 0.5

env: my-analysis-env
```

The essentials:

- **`inputs:`** — named input slots. Each name is what you pass to `ctx.input("data")` (or look up under `WFC_INPUT_PATHS["data"]`).
- **`outputs:`** — named output slots. Each name must match what you `ctx.save_artifact("filtered", ...)` (or the filename you write into `WFC_RUN_DIR`).
- **`params:`** — typed params with defaults; pipeline overrides merge on top, and the merged dict arrives as `ctx.params` / `WFC_PARAMS`.
- **`env:`** — the named container environment your method runs in. This must be a registered environment name (not a runtime package list).

This is the *basics* only. The full field reference — every input/output/param field, column validation, executor selection, and the complete `env:` vocabulary including pinned digests — lives in [[method-yaml-schema]]. For input/output *contracts* (column validation, `from_params`, module-level overrides) see [[writing-contracts]].

## Next steps

You now have a method directory, a script (either the `wfc-client` decorator or the bare env-var + file contract), and a `method.yaml` that declares its slots. From here:

- **Register the environment your method runs in.** The `env:` you named must point at a container environment that has been built first. See [[registering-an-environment]] — it covers `wfc register-env`, what `wfc doctor` checks, and how `wfc init` sets the project up.
- **Register the method itself** with `wfc register-method <path> --module <module>`, once its environment exists.
- **Flesh out the contract** — column validation, typed params, and module-level overrides — in [[writing-contracts]].
- **Look up any field** in the full [[method-yaml-schema]] reference.

With the method registered, you can drop it into a pipeline and run it. To wire and run pipelines, head to [[use-the-canvas]].
