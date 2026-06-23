<!-- generated from pm_mvp::docs.consumer.writing-methods @ efaf02366f71; do not edit -->

# Writing Methods

## Method Directory Layout

A method is a self-contained directory built from one required file and one strongly-recommended file:

| File | Purpose |
|---|---|
| `{method_name}.py` | Python script containing the implementation (required) |
| `method.yaml` | Contract file declaring inputs, outputs, params, and env (recommended — without it the method registers but has no slot-level metadata in the database or canvas widgets) |

The script filename must match the directory name. For example, a method called `preprocess` lives in a directory named `preprocess/` and its script is `preprocess.py`.

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

Both layouts are registered the same way:
```bash
wfc register-method modules/my_analysis/preprocess --module my_analysis
wfc register-method methods/feature_qc --module data_tools
```

At registration, the method directory is snapshotted into `methods/{method_name}/` for code fingerprinting, regardless of where the source lives.

## Writing the Python Script

A method is just a script that reads a few environment variables and writes its declared output files. That is the **canonical contract** — the supported-forever interface. Your script imports nothing from wfc; it only needs the Python standard library (plus whatever your analysis itself uses). If your script honors this contract, wfc will run it.

### The env-var + file contract

Before launching your script, wfc sets these environment variables:

| Variable | Type | Meaning |
|---|---|---|
| `WFC_RUN_DIR` | path | Directory to write your declared outputs into. Everything you produce goes here. |
| `WFC_INPUT_PATHS` | JSON | `{slot_name: [absolute paths]}` — resolved input files for each input slot in `method.yaml`. |
| `WFC_PARAMS` | JSON | `{param_name: value}` — params from `method.yaml` defaults merged with pipeline overrides. |

Your contract back to wfc:

- Read inputs and params from those env vars.
- Write each declared output to `${WFC_RUN_DIR}/<output_name>.<ext>` matching your `method.yaml` `outputs:` declarations.
- Print whatever you like to stdout/stderr — wfc captures both into the run logs automatically.
- **Exit 0 on success, non-zero on failure.** That is how wfc knows whether the step succeeded.

### Minimal example (stdlib only, no wfc import)

This mirrors the in-repo fixture methods (`tests/fixtures/methods/heartbeat/heartbeat.py`, `tests/fixtures/methods/qc/qc.py`):

```python
import csv
import json
import os
from pathlib import Path


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    input_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    params = json.loads(os.environ.get("WFC_PARAMS", "{}"))

    # Resolve the "data" input slot declared in method.yaml.
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

The script reads `WFC_RUN_DIR` / `WFC_INPUT_PATHS` / `WFC_PARAMS`, writes its outputs into `WFC_RUN_DIR`, and exits 0. It imports zero wfc code — pandas, R, bash, or any other language works the same way as long as the script honors the env-var + file contract.

### What wfc does after your script exits

When your process exits 0, wfc scans `WFC_RUN_DIR` for the output filenames declared in `method.yaml`, hashes each one into the DVC content cache, and records the run in the database. Undeclared files in `WFC_RUN_DIR` are ignored. A missing required output, or a non-zero exit, fails the step.

### Tier 1 sugar: the `@wfc_method` decorator

The env-var + file contract above is the canonical, supported-forever interface — write your methods against it. A thinner ergonomic wrapper, the `@wfc_method` decorator, gives you a `ctx` object instead of raw env vars. A working decorator ships **today** inside wfc as `wfc.method` (`from wfc.method import wfc_method, wfc_method_main`); older in-tree methods use it and keep working. It is treated as transitional.

The longer-term plan is to extract that sugar into a separately-installable **`pm-client`** package, so you could opt in to the decorator by adding `pm-client` to your env like any other dependency while wfc itself stays out of your environment. That packaging is **planned, not yet released** — a proposed direction, not a shipped feature. So write Tier 2 (env-var + file) methods today; the decorator is documented here only so the migration path is clear.

The intended `pm-client` surface gives you a `ctx` object:

```python
# PLANNED — the standalone `pm-client` package is not yet released.
from pm_client import wfc_method, wfc_method_main

@wfc_method
def filter_data(ctx):
    data_path = ctx.input("data")[0]      # resolved from WFC_INPUT_PATHS
    threshold = ctx.params.get("threshold", 0.5)
    # ... compute, write a file under ctx.workdir ...
    ctx.save_artifact("filtered", ctx.workdir / "filtered.csv")
    ctx.log_metric("kept_rows", 123)

if __name__ == "__main__":
    wfc_method_main()
```

The decorator is pure sugar: anything it does, you can do directly against the env-var + file contract above with no extra dependency. A `pm-client` decorator would never touch your data bytes — `save_artifact(name, path)` only records that the file at `path` (which must live inside `WFC_RUN_DIR`) is the declared output `name`; wfc does the hashing and archiving host-side.

> **Note for existing methods.** Older in-tree methods import `from wfc.method import wfc_method, wfc_method_main` and may `return` a value that wfc parses. That coupling is being retired in favor of the env-var + file contract above; new methods should be written against that contract and will keep working unchanged regardless of the `pm-client` rollout.

## The Contract File (method.yaml)

Every method should have a `method.yaml` that declares its inputs, outputs, and parameters. Methods without a `method.yaml` are still valid but won't have slot-level metadata in the database or canvas widgets.

### Example

```yaml
inputs:
  data:
    type: csv
    required: true
    description: "Labeled cell CSV"

outputs:
  predictions:
    type: csv
    required: true
    description: "Per-cell predictions"
  model:
    type: model
    required: true

params:
  threshold:
    type: float
    required: false
    default: 0.5
  feature_pairs:
    type: list
    required: true

executor: python
env: inherit
```

### Input slot fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | str | `"csv"` | `csv`, `parquet`, `pickle`, `png`, `any` |
| `required` | bool | `true` | Fail if not wired in the pipeline |
| `multiple` | bool | `false` | Accept a list of files (fan-in) |
| `description` | str | `""` | Human-readable description |
| `columns` | dict | None | Column presence spec (see below) |

### Output slot fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | str | `"csv"` | `csv`, `parquet`, `directory`, `model`, etc. |
| `multiple` | bool | `false` | Produces N files |
| `description` | str | `""` | Human-readable description |
| `columns` | dict | None | Column presence spec |
| `contents` | list[dict] | None | Directory content assertions |

### Param fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | str | `"str"` | `str`, `int`, `float`, `bool`, `list`, `dict` |
| `required` | bool | `true` | Fail if not provided at runtime |
| `default` | any | None | Default value used when not overridden |
| `description` | str | `""` | Human-readable description |

### Column presence validation

For `csv` and `parquet` slots, you can declare expected columns via the `columns` key:

```yaml
inputs:
  data:
    type: csv
    columns:
      strict: ["cell_index", "condition", "proliferative"]
      patterns: ["*_intensity"]
```

| Key | Purpose |
|---|---|
| `strict` | Exact column names that must be present |
| `from_params` | Columns derived from runtime params via cartesian expansion |
| `patterns` | fnmatch-style globs; at least one column must match each pattern |

Input column validation is a **hard gate** -- missing columns block execution. Output column validation is a **soft check** -- mismatches produce warnings but do not fail the run.

### How contracts are validated

At `register-method` time, method outputs are checked against module contracts: every required module output must appear in the method's output slots. At pipeline load time, link wiring is validated against the declared slots, and static cross-step column compatibility is checked for `strict` columns between connected steps.

## Column Picker Params (`column_of_input` and `new_column`)

Two optional param-level fields turn a free-text string param into a column-aware picker in the canvas inspector. Both are pass-through YAML keys — no Python-side model change is required.

### `column_of_input`

```yaml
params:
  label_column:
    type: str
    required: true
    description: "Name of the label column"
    column_of_input: data   # ← name of an input slot on this method
```

When set, `column_of_input` tells the canvas inspector which upstream input slot's column contract to resolve. The inspector walks the canvas graph to find the node connected to that slot, then fetches `GET /api/contracts/{upstream_method}/output_columns?slot=<source_slot>&params=<json>` — calling `resolve_columns` from `wfc/contracts.py` with the upstream method's current canvas params. The response feeds a combobox dropdown (dropdown options + free-text fallback). `patterns` from the upstream contract are displayed as a hint chip.

**`run_reference` upstream:** When the upstream node is a Run Reference, the endpoint resolves against the referenced method's contract using the referenced run's stored params (looked up via `/api/wfc/runs/{id}`).

**Empty or missing upstream columns:** The dropdown is empty and a free-text hint is shown. No error is raised.

### `new_column`

```yaml
params:
  output_column:
    type: str
    required: true
    description: "Name to give the new column"
    new_column: true   # ← always free-text, dropdown suppressed
```

When `new_column: true`, the inspector always renders a plain text field regardless of any `column_of_input` on the same param. Use this for params that name a column the method *produces*, not one it *reads*.

### Cross-referencing column contracts

`column_of_input` reuses the `outputs.<slot>.columns` vocabulary declared in module contracts via `resolve_columns` in `wfc/contracts.py`. No new contract fields are introduced — the producer side (`from_params`) already existed; `column_of_input` is the consumer side.

## Environment Isolation

Each method can run in its own isolated environment — pixi, conda, or a container image — preventing dependency conflicts between methods in the same pipeline. Environment isolation is configured via the `env` key in `method.yaml`.

### Env spec format

| Spec | Meaning |
|---|---|
| `inherit` | Use the project's own Python (default if `env` is omitted) |
| `pixi:<name>` | Standalone pixi project, default env |
| `pixi:<project>:<env>` | Pixi project with an explicit env name |
| `conda:<name>` | Conda environment by name |
| `container:<name>` | Container image registered in `.wfc/envs.json` via `wfc register-env` (v1: local-only) |
| `container:docker://<ref>@sha256:<hex>` | Per-method digest-pinned image escape hatch (no manifest lookup) |

Bare names without a prefix (e.g., `image-io` instead of `pixi:image-io`) are rejected with a `ValueError` at registration time — only typed prefixes are accepted. At execution time, resolution failure is caught and silently falls back to the project Python (`sys.executable`).

Container methods may also set `gpus: true` in `method.yaml` to request `--gpus all` at dispatch.

### Example

```yaml
# method.yaml
inputs:
  images:
    type: directory
outputs:
  features:
    type: csv
env: pixi:image-io
```

### Pixi root configuration

Pixi environment directories are located under a configurable root, set in `.wfc/wf-canvas.toml`:

```toml
[pixi]
root = ".pixi"   # relative to project root, or absolute

[conda]
root = "/home/user/anaconda3"   # optional
```

### Resolution cascade

1. **`pixi:<name>`** -- globs `{pixi_root}/{name}-*/envs/default`. Exactly one match required.
2. **`pixi:<project>:<env>`** -- globs `{pixi_root}/{project}-*/envs/{env}`. Exactly one match required.
3. **`conda:<name>`** -- checks `{conda_root}/envs/{name}` directly. Falls back to `conda info --base` if `conda_root` is not configured.

Pixi stores environments with a hash suffix (`{name}-{hash}/envs/{env}`), so the glob pattern `{name}-*` handles this layout.

### When resolution happens

- **Registration** (`wfc register-method`): `_resolve_env()` validates the environment exists and has a Python binary. Fails fast if missing.
- **Execution** (`run_step`): Re-resolves the env spec to handle hash changes from `pixi install` between registration and execution. Falls back to `sys.executable` if resolution fails.
- **Snakefile generation**: Pre-resolves all named environments into an `ENV_PYTHON_PATHS` dict. Each rule selects the correct interpreter from this dict.

When dispatching to a non-inherit environment, the PATH is rewritten to prioritize the target environment's directories, preventing ABI-mismatch issues from the parent environment's DLLs.

### Your environment never contains wfc

Whichever backend you use, the environment your method runs in contains **only your declared dependencies plus Python** — wfc is never installed into it. This is what makes the env-var + file contract the floor: your script reaches wfc through `WFC_RUN_DIR` / `WFC_INPUT_PATHS` / `WFC_PARAMS`, not through an import. If you later want the `@wfc_method` decorator sugar, you add the (future) `pm-client` package to your env's dependency list like any other library; even then, the full wfc package stays out of your environment.

## Module Contracts

Module contracts define required outputs and metrics that every method in a module must produce. They sit above method contracts and enforce domain-wide guarantees.

### module.yaml format

Declare module contracts in `modules/{module_name}/module.yaml`:

```yaml
description: Train and apply binary classifiers on labeled cell data
contracts:
  - type: output
    name: model
    value_type: model
    required: true
  - type: metric
    name: mcc
    value_type: float
    required: true
```

Each contract entry has:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | str | -- | `output` or `metric` |
| `name` | str | -- | Required output/metric name |
| `value_type` | str | None | Expected type (e.g., `float`, `model`, `.csv`) |
| `required` | bool | `true` | Whether the contract is enforced |

### CLI alternative

Module contracts can also be passed as JSON via the CLI:

```bash
wfc register-module --name my_analysis --contracts '[{"type": "output", "name": "model", "value_type": "model", "required": true}]'
```

The `--contracts` CLI flag and `module.yaml` serve the same purpose. When both are provided, the CLI `--contracts` flag takes precedence and `module.yaml` is used as a fallback.

### Enforcement

| When | What happens |
|---|---|
| `wfc register-method` | Every required module output must appear in the method's output slots. Missing outputs fail registration. |
| After the run | When your script exits, wfc checks `WFC_RUN_DIR` for the required contract outputs and metrics. A missing required output (or a non-zero exit) fails the step. |

Module contracts and method contracts compose: method contracts define the I/O shape (slots), while module contracts guarantee that certain named outputs and metrics are always produced.

## The Full Environment Contract

The [env-var + file contract](#the-script) is the floor every method builds on, and it's all you strictly need: read `WFC_RUN_DIR` / `WFC_INPUT_PATHS` / `WFC_PARAMS`, write outputs into `WFC_RUN_DIR`, exit 0. This section lists the complete set of env vars wfc sets, and describes the optional `RunContext` convenience layer that the future `pm-client` decorator builds on. Your script doesn't know or care which orchestrator launched it — the contract is identical whether wfc runs it locally or inside a container.

### How it works

1. `run_step` sets the `WFC_*` environment variables and launches your script as a subprocess.
2. Your script reads the env vars, does its work, and writes declared outputs into `WFC_RUN_DIR`.
3. On exit 0, wfc scans `WFC_RUN_DIR` for declared outputs, hashes and archives them, and records the run.

(If you use the future `pm-client` decorator, step 2 is wrapped: `wfc_method_main()` constructs a `RunContext` from the env vars and hands it to your function. The env vars are still the source of truth.)

### Environment variables

The three load-bearing vars are `WFC_RUN_DIR`, `WFC_INPUT_PATHS`, and `WFC_PARAMS` (covered above). wfc also sets these contextual vars, available to any script that wants them:

| Variable | Purpose |
|---|---|
| `WFC_RUN_DIR` | Directory for this run's outputs (**write your outputs here**) |
| `WFC_INPUT_PATHS` | JSON `dict[str, list[str]]` of input slot paths (**your inputs**) |
| `WFC_PARAMS` | JSON-encoded params dict (**your params**) |
| `WFC_RUN_ID` | Unique run identifier (int) |
| `WFC_SAMPLE` | Current sample name |
| `WFC_NODE_ID` | Node identifier within the pipeline |
| `WFC_PIPELINE_ID` | Pipeline identifier for this execution |
| `WFC_VARIANT` | Variant name for this run |

### Optional convenience: the `RunContext` layer

`RunContext` is the helper object the future `pm-client` decorator wraps these env vars in. **You never need it** — it's sugar over the same contract — but it's documented here so the decorator's behavior is clear:

**`load_input()`** — Returns `dict[str, list[Path]]` parsed from `WFC_INPUT_PATHS`; `None` when the node has no upstream input. Equivalent to `json.loads(os.environ["WFC_INPUT_PATHS"])` with paths wrapped in `Path`.

**`save_artifact(name, path)`** — Records that the file at `path` (which must already live inside `WFC_RUN_DIR`) is the declared output `name`. It does **not** copy or read the file — wfc hashes and archives it host-side after the run. Equivalent to writing your file into `WFC_RUN_DIR/<name>.<ext>` directly.

**`log_metric(name, value)`** — Records a scalar metric. Equivalent to including the metric in the results your script writes.

The `RunContext` / `pm-client` layer makes **no serialization choices for you** and reads none of your data bytes: you still write your own files. That's the deliberate design — the contract is "write files into `WFC_RUN_DIR`," and the convenience layer only records *which* files are which.

### What you don't need to do

You do not import wfc, construct a `RunContext`, or call any framework save method to write a Tier 2 (env-var + file) method — writing your output files into `WFC_RUN_DIR` and exiting 0 is the whole contract. The `RunContext` API above is documented for understanding the optional `pm-client` convenience layer, not as a requirement.

## Next Steps

Once you've written your method:

- **Register it** -- Use `wfc register-module` to create the module, then `wfc register-method` to register the method and validate its contracts.
- **Inspect results** -- After a pipeline run, use `wfc pipeline-summary` to review outcomes, or browse the `.runs/` directory for per-run outputs and metrics.
