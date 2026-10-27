<!-- generated from pm_mvp::docs/consumer/reference/method-yaml-schema @ e540d18d78ce; do not edit -->

# Reference: method.yaml Schema

## Overview

`method.yaml` sits next to a method script in the method's directory (for example `src/my_analysis/filter_data/method.yaml`) and declares what the method reads (`inputs`), what it writes (`outputs`), the parameters it accepts (`params`), and the container environment it runs in (`env`). The canvas reads it to draw slots and parameter editors; `wfc register-method` reads it to check the method and wire it into pipelines.

Every method needs a `method.yaml` with an `env` and at least one input slot. `wfc register-method` refuses a method without them. A key written with nothing under it (`outputs:`) is the same as leaving it out.

The tutorials [Writing a Method in Python](../tutorials/authoring-a-method-script.md) and [Writing Contracts](../tutorials/writing-contracts.md) introduce these keys in context. This page lists every key.

## Top-level keys

| Key | Type | Required | Purpose |
|---|---|---|---|
| `inputs` | mapping | **yes** (at least one slot) | One entry per input slot. |
| `outputs` | mapping | no | One entry per output slot. |
| `params` | mapping | no | One entry per parameter. |
| `env` | string | **yes** | Name of an environment registered with `wfc register-env`. |
| `gpus` | bool | no (default `false`) | `true` launches the container with `--gpus all`. |
| `executor` | string | no (default `local`) | How the method is dispatched. `local` runs it in a local Docker container. |
| `script` | string | no | The script's filename, when it is not `{method_name}.<ext>`. |
| `helpers` | list of strings | no | The exact set of helper files that belong to the method. |

## Slot names

Input and output slot names are how pipelines wire nodes together. A slot name cannot contain `:`, `=` or whitespace.

## What moves the cache key

After you edit `method.yaml` or the script, run `wfc register-method` again. Each registration that changes anything a run uses adds a new version of the method, and a run is reused from the cache only for the version it ran on.

Everything in `method.yaml` is part of a version except descriptions, formatting (key order, comments, quoting), `env` and `helpers`. Changing a slot name or `type`, a `columns` block, a param's `type`, `default`, `required` or `constraints`, `script`, `executor` or `gpus` adds a version. Editing only descriptions or formatting does not: `wfc register-method` says the edit is not kept and adds nothing. A different `env` is tracked on its own and also makes the next run recompute, and the `helpers` files themselves are code, so editing one adds a version. Parameter values are part of the cache key on their own. See [Caching and Reproducibility](../explanation/caching-and-reproducibility.md).

## inputs and outputs

Each entry under `inputs` and `outputs` is a named slot.

### Input slot fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | string | none | File extension the slot carries (`csv`, `.h5ad`), or `dir` for a directory. Used for display in the canvas. A value that doesn't follow the convention below prints a warning at registration. |
| `required` | bool | `true` | A required input that nothing feeds is flagged before the step runs. Set `false` for an optional input. |
| `multiple` | bool | `false` | Marks the slot as taking several files; the Registry shows it as `multi`. Bundling samples into one run is set on the Input Selector (see [Fan out and fan in](../how-to/canvas.md#fan-out-and-fan-in)). |
| `description` | string | `""` | Label shown in the canvas. |
| `columns` | mapping | none | Declared columns, for tabular slots (see Column contracts below). |

An input can receive a file or a directory. A directory sample or a directory output from an upstream step arrives as the path of that directory.

### Output slot fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | string | **required** | File extension of the output, or `dir` / `directory` for a directory. |
| `description` | string | `""` | Label shown in the canvas. |
| `columns` | mapping | none | Declared columns, for tabular slots (see Column contracts below). |

Every declared output must exist when the method exits, or the step fails naming the missing slot.

### Output types and file names

An output's `type` is its file extension. The leading dot is optional: `type: csv` and `type: .csv` are the same. When a method writes its outputs straight into the run directory, wfc looks for the slot name plus the extension: slot `scores` with `type: csv` is `scores.csv`. Compound extensions work the same way (`type: tar.gz` gives `scores.tar.gz`). The value is used exactly as written, so `type: anndata` means a file named `<slot>.anndata`.

A missing or empty output `type` is refused at registration.

`dir` or `directory` declares a directory output, found under the bare slot name. The directory must contain at least one file, and cannot contain a symlink or two names that differ only by case.

A method that uses the `wfc-client` decorator, or writes `_wfc_results.json` itself, can save each output under any file name; wfc finds it by slot. Two slots cannot be saved to the same file.

The canvas shows slot types without the leading dot (`csv`, `tar.gz`).

## params

Each entry under `params` declares a parameter. The canvas inspector shows an editor for each one, and the values set on a node reach the method as `ctx.params` or `WFC_PARAMS`. A parameter you leave unset reaches the method at its declared `default`, and the run records that value with the others.

### Param fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | string | `str` | One of `str`, `int`, `float`, `bool`, `list`, `dict`, `any`. Picks the editor in the canvas; `any` accepts any value. Common spellings are accepted, case-insensitive: `string`/`text` → `str`, `integer` → `int`, `number`/`double` → `float`, `boolean` → `bool`, `dictionary`/`map`/`mapping`/`object` → `dict`. Any other value is refused at registration, naming the parameter. |
| `default` | any | none | The value a parameter takes when you leave it unset. The canvas shows it, the method receives it in `ctx.params` or `WFC_PARAMS`, and the run records it. A `default` of `null` declares no default: the method receives the parameter only when you set it. A default that breaks the parameter's own `type` or `constraints` is refused at registration, naming the parameter. |
| `required` | bool | `false` | When `true`, the canvas marks the parameter with `*` and won't run the pipeline while it has neither a value nor a usable `default`. A `default` is usable unless it is `null`, or `""` on a required parameter. |
| `description` | string | `""` | Label shown in the canvas. |
| `constraints` | mapping | none | Limits the canvas editor: `enum` (a list of allowed values, shown as a dropdown), `min` and `max` (numeric bounds, shown beside the name and checked as you type). |

### Checking a value against its type

The canvas, `wfc run-pipeline` and the server all check a parameter value the same way, and refuse a pipeline whose value fails, naming the node and the parameter. A parameter set on a node that the node's method version does not declare is refused too.

- `int` accepts a whole number, including a whole-number float such as `3.0` (stored as `3`), within ±(2^53 − 1). `2.5`, `NaN`, `inf` or `true` is refused.
- `float` accepts any number, whole numbers included; `1` is read as `1.0`. `true` is refused.
- A value must be one of `constraints.enum`, compared by its JSON value (`1` equals `1.0`; `true` equals only `true`), and inside `min` and `max` when those are set.
- An empty value counts as no value: fine for an optional parameter, refused for a required one.
- Every value of a `param_sets` variant is checked the same way.

### Column-picker fields

Two optional fields turn a `str` parameter that names a column into a column picker in the inspector.

| Field | Type | Purpose |
|---|---|---|
| `column_of_input` | string | Names an input slot of this method. The inspector offers the columns declared by whatever feeds that slot as a dropdown, and still accepts free text. Use it for a parameter that names a column the method reads. |
| `new_column` | bool | When `true`, the inspector always shows a plain text field. Use it for a parameter that names a column the method creates. It wins over `column_of_input`. |

When the upstream declares no columns, the dropdown is empty and you type the name.

## Column contracts (columns:)

A `columns` block on a tabular slot declares which columns the slot carries. The canvas uses it to fill column pickers, and pipeline load uses it to catch mis-wired steps.

```yaml
inputs:
  data:
    type: csv
    columns:
      strict: ["cell_index", "condition", "proliferative"]
      from_params:
        - params: [markers]
          pattern: "{}_intensity"
      patterns: ["*_intensity"]
```

| Key | Purpose |
|---|---|
| `strict` | A list of exact column names. |
| `from_params` | Column names built from parameter values. Each entry has `params` (a list of parameter names) and `pattern`, a string with one `{}` per parameter. When a parameter holds a list, every combination is produced. |
| `patterns` | A list of glob patterns such as `*_intensity`, shown as hints. |

When a pipeline loads, the declared columns of connected slots are compared and a mismatch is reported as a warning. wfc does not open your data files to check their actual columns.

A malformed block (for example `patterns` written as a single string instead of a list) is refused at registration, naming the slot. [Writing Contracts](../tutorials/writing-contracts.md) explains how to design these declarations.

## env, gpus, and executor

These keys control where and how the method runs.

### env

`env` names the container environment the method runs in. Register the environment first with `wfc register-env <name>`, then write its name here:

```yaml
env: my-analysis   # registered with: wfc register-env my-analysis
```

The value is the bare environment name: letters, digits, `_` and `-`. How the image is built (a pixi or conda lock, a live environment, or your own image) is chosen when you run `wfc register-env`, not in `method.yaml`. If the name is not a registered environment, `wfc register-method` refuses the method and lists the environments that are registered.

A pipeline step always runs in the env its method version registered with, and its cache key uses that env. To change a method's env, edit `env:` and register the method again; earlier versions keep the env they registered with.

The method script runs under the environment's interpreter. For an R or bash method, register the environment with `--interpreter` pointing at `Rscript` or `bash` inside the image. See [Registering an Environment](../tutorials/registering-an-environment.md).

### gpus

`gpus: true` launches the method's container with `--gpus all`. The default is `false`.

### executor

`executor` selects how the method is dispatched. `local`, the default, runs it in a local Docker container, so you can leave the key out.

## script and helpers

### script

A method script can be Python (`.py`), R (`.R` or `.r`) or bash (`.sh`). A method folder holds one method: one `method.yaml`, and one main script. When the folder holds a single script, `wfc register-method` uses it. When it holds more than one, `method.yaml` must name the main one:

```yaml
script: run_analysis.R
```

Without it, registration stops, lists the scripts it found and gives the `script:` line to add. The other scripts register as the method's helpers. The `script:` line is part of the method's contract, so adding it to a method that is already registered makes its next registration a new version, and its runs compute again.

The file must be inside the method folder and have one of the extensions above. The `--script` flag on `wfc register-method` names the main script for a single registration and overrides `script:`. A new version of a method keeps its main script's file name: to register a folder whose main script has another name, rename it back.

A folder that holds a second `method.yaml`, in a subfolder, is refused: move that method into its own folder.

### helpers

Without `helpers:`, every `.py`, `.R`, `.r` and `.sh` file in the method folder and its subfolders counts as part of the method: all of them are copied at registration, and when you edit any of them and run `wfc register-method` again, the next run recomputes.

Set `helpers:` to list the method's helper files exactly:

```yaml
script: analysis.R
helpers:
  - utils.R
  - ../shared/plotting.R
```

Paths are relative to the method folder and may point elsewhere in the project with `../`. With `helpers:` set, the method is the main script plus the listed files, and registration refuses a method folder that holds any other script file. Each listed file must exist and have a recognized extension.

## Worked examples

### Minimal method.yaml

One input, one output, and the environment.

```yaml
inputs:
  data:
    type: csv
    description: "Labeled cell CSV"

outputs:
  predictions:
    type: csv
    description: "Per-cell predictions"

env: my-analysis
```

### Full method.yaml

Column declarations, constrained and column-picker parameters, an R script with a shared helper, a directory output, and a GPU.

```yaml
inputs:
  data:
    type: csv
    required: true
    description: "Labeled cell CSV"
    columns:
      strict: ["cell_index", "condition"]
      patterns: ["*_intensity"]

outputs:
  predictions:
    type: csv
    description: "Per-cell predictions"
  plots:
    type: dir
    description: "One PNG per condition"

params:
  threshold:
    type: float
    default: 0.5
    constraints: {min: 0, max: 1}
    description: "Decision threshold"
  method:
    type: str
    default: logistic
    constraints: {enum: [logistic, forest]}
  label_column:
    type: str
    required: true
    column_of_input: data        # dropdown of the columns feeding `data`
    description: "Name of the label column"
  score_column:
    type: str
    required: true
    new_column: true             # always free text
    description: "Name for the new score column"

script: classify.R
helpers:
  - ../shared/io.R
env: my-r-gpu-env                # registered with: wfc register-env ... --interpreter Rscript
gpus: true
```

## Next steps

- [Writing a Method in Python](../tutorials/authoring-a-method-script.md) — write the method script that reads these inputs and params and writes these outputs.
- [Registering an Environment](../tutorials/registering-an-environment.md) — build the container image that the `env` key names.
- [Writing Contracts](../tutorials/writing-contracts.md) — the mental model behind `columns`, module contracts, and column pickers.
