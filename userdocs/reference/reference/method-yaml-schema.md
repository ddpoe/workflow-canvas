<!-- generated from pm_mvp::docs.consumer.reference.method-yaml-schema @ 62b7baae590e; do not edit -->

# Reference: method.yaml Schema

## Overview

`method.yaml` is the contract file that sits next to a method script. It declares what the method reads (`inputs`), what it writes (`outputs`), the knobs it accepts (`params`), the language it runs (`executor`), the container environment it runs in (`env`), and whether it needs a GPU (`gpus`). The canvas reads it to draw input/output slots and parameter widgets; the engine reads it to validate wiring, check column contracts, and dispatch the run in the right container.

A method without a `method.yaml` still runs, but it has no slot-level metadata in the database or canvas. As soon as you add one, every key below becomes available.

This page is the canonical field-by-field reference. The tutorials <a href="../../tutorials/authoring-a-method-script.html">Authoring a Method Script</a> and <a href="../../tutorials/writing-contracts.html">Writing Contracts</a> introduce these keys in context and link back here for the exact fields rather than repeating the tables.

## Top-level keys

| Key | Type | Required | Purpose |
|---|---|---|---|
| `inputs` | mapping | no | One entry per input slot the method reads. |
| `outputs` | mapping | no | One entry per output slot the method writes. |
| `params` | mapping | no | One entry per tunable parameter. |
| `executor` | string | no (default `python`) | The interpreter/runner for the script. |
| `env` | string | **yes** | Name of a built container environment (see the executor/env/gpus section). A method with no `env` is rejected at registration. |
| `gpus` | bool | no (default `false`) | When `true`, the container is launched with `--gpus all`. |

Missing sections default to empty mappings, and `executor` defaults to `python`. `env` is the one key with no default: every method must name an environment that has already been built.

## Fields

_Stub._ One row per method.yaml key: `inputs` / `outputs` (slots, types), `params` (+ kinds incl. `column_of_input`, `new_column`), `columns:` (strict/from_params/patterns), `executor`, `env:` (inherit/pixi/conda/container:<name>/container:docker://...@sha256), `gpus:`. **To absorb:** writing-methods/method-yaml; ADR-002; ADR-005; ADR-019.

## inputs and outputs

Each entry under `inputs` and `outputs` is a named slot. The name is how the slot is referenced when wiring nodes together in a pipeline.

### Input slot fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | string | *(optional)* | File extension the slot carries (e.g. `.csv`, `.h5ad`). Advisory for inputs — present if you want canvas type-display; not enforced at registration. |
| `required` | bool | `true` | When `true`, the pipeline fails to load if nothing is wired into this slot. |
| `multiple` | bool | `false` | When `true`, the slot accepts a list of upstream files (fan-in). |
| `description` | string | `""` | Human-readable label shown in the canvas. |
| `columns` | mapping | none | Column-presence contract for tabular slots (see the column-contracts section). |

### Output slot fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | string | *(required)* | File extension for the slot output. Must be a leading-dot extension (e.g. `.csv`, `.h5ad`, `.parquet`) or the directory marker `dir` / `directory`. |
| `multiple` | bool | `false` | When `true`, the slot produces N files (fan-out). |
| `description` | string | `""` | Human-readable label shown in the canvas. |
| `columns` | mapping | none | Column-presence contract for tabular slots (a soft check on outputs). |
| `contents` | list of mappings | none | Content assertions for `directory` slots (glob patterns, `min_count`, per-file columns). |

### Type convention

The output slot `type` field **is** the file extension, declared verbatim. The engine concatenates it directly onto the slot name to produce the output filename — `<slot_name><type>` (e.g. slot `scores` with `type: .csv` → `scores.csv`).

Valid values:
- **Dotted extension** — any value starting with `.` and at least two characters long: `.csv`, `.h5ad`, `.parquet`, `.tar.gz`, `.pkl`, `.json`, `.png`, etc. Compound extensions work by verbatim concatenation.
- **Directory marker** — `dir` or `directory` (case-insensitive). Both are accepted; the engine normalises to the canonical `directory`. A directory slot gets no extension (the bare slot name).

Invalid values are rejected at registration time with a clear `ValueError` naming the slot and showing the accepted convention. There is no silent `.csv` default and no semantic-type→extension translation: declaring `type: anndata`, `type: csv` (no dot), or omitting `type` on an output slot all raise at registration.

Input slots follow the same convention when `type` is present, but validation is advisory (a warning, not a registration failure) so that optional type annotations don't block wiring.

### Canvas display

The slot colour in the canvas is derived client-side from the `type` value — common extensions have curated colours; any other extension gets a deterministic HSL hash colour so distinct extensions are visually distinct.

## params

Each entry under `params` declares a tunable value the method reads at runtime. Params appear as editable widgets in the canvas inspector and are passed to the method through the run parameters.

### Param fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `type` | string | `"str"` | One of `str`, `int`, `float`, `bool`, `list`, `dict`. |
| `required` | bool | `true` | When `true`, the run fails if no value is supplied. |
| `default` | any | none | Value used when the param is not overridden. |
| `description` | string | `""` | Human-readable label shown in the canvas. |

### Column-picker fields

Two optional fields turn a plain `str` param into a column-aware picker in the canvas inspector. They are pass-through YAML keys — nothing else in the method changes.

| Field | Type | Purpose |
|---|---|---|
| `column_of_input` | string | Names an input slot on this method. The inspector resolves that slot's upstream column contract and offers the columns as dropdown options (with a free-text fallback). Use it for a param that names a column the method *reads*. |
| `new_column` | bool | When `true`, the inspector always renders a plain text field and suppresses any dropdown. Use it for a param that names a column the method *produces*. |

When both are present on the same param, `new_column: true` wins and the field stays free-text. If the upstream has no resolvable columns, the dropdown is simply empty and a free-text hint is shown — no error.

## Column contracts (columns:)

For `csv` and `parquet` slots you can declare which columns must be present using a `columns` mapping on the slot. This lets the engine catch a mis-wired pipeline before any step runs, and lets the canvas surface column hints.

```yaml
inputs:
  data:
    type: .csv
    columns:
      strict: ["cell_index", "condition", "proliferative"]
      from_params:
        - params: [markers]
          pattern: "{}_intensity"
      patterns: ["*_intensity"]
```

| Key | Purpose |
|---|---|
| `strict` | Exact column names that must all be present. |
| `from_params` | Column names derived from the run's params. Each entry has `params` (a list of param names) and a `pattern` with positional `{}` placeholders; the placeholders are filled from the param values, taking the cartesian product when a param is list-valued. Useful when the required columns depend on the parameters chosen at run time. |
| `patterns` | fnmatch-style globs (for example `*_intensity`); at least one column must match each pattern. |

### Hard input gate, soft output check

Column validation behaves differently on the two sides of a method:

- On an **input** slot, the contract is a **hard gate**. If a required column is missing, the step is blocked and the run fails with a clear message.
- On an **output** slot, the contract is a **soft check**. A mismatch produces a warning but does not fail the run, because a method may legitimately add or rename columns.

At registration and pipeline-load time, `strict` columns are also cross-checked between connected steps so an incompatible wiring is caught statically. The full mental model for designing these contracts lives in <a href="../../tutorials/writing-contracts.html">Writing Contracts</a>.

## executor, env, and gpus

These three keys control how the method is run.

### executor

`executor` names the interpreter for the script. It defaults to `python`. Methods in other languages set it to the appropriate runner; the script still talks to the engine through the environment-variable and file contract, so any language works.

### env

`env` is required and names a container environment that has already been built. Execution is container-only — there is no host-Python fallback and no inherited default — so a `method.yaml` with no `env` is rejected at registration time.

Build an environment once with `wfc register-env <name>`, then name it here. The `env` value can take three forms:

| Spec | Meaning |
|---|---|
| `<name>` | A container image registered in `.wfc/envs.json` under that name. |
| `container:<name>` | Identical to the bare name; the `container:` prefix is accepted for clarity. |
| `container:docker://<ref>@sha256:<hex>` | A digest-pinned image used directly, with no manifest lookup. The escape hatch for bring-your-own images. |

**`env` is not where you describe how to build the image.** The build backends — `pixi:<project>`, `conda:<name>`, and bring-your-own — are arguments to `wfc register-env`, which produces the named image. By the time a method references an env, that work is already done, so the keywords `inherit`, `pixi:`, and `conda:` are *not* valid `env` values in `method.yaml`; using them raises an error. The environment your method runs in contains only your declared dependencies plus Python — the full `wfc` package is never installed into it.

### gpus

Set `gpus: true` to request a GPU. At dispatch the container is launched with `--gpus all`. It defaults to `false`, so methods opt in explicitly. Registering and building environments is covered step by step in <a href="../../tutorials/registering-an-environment.html">Registering an Environment</a>.

## Worked examples

### Minimal method.yaml

The smallest contract that still carries slot metadata: one input, one output, and the required `env`.

```yaml
inputs:
  data:
    type: .csv
    description: "Labeled cell CSV"

outputs:
  predictions:
    type: .csv
    description: "Per-cell predictions"

env: my-analysis   # built once with: wfc register-env my-analysis
```

### Full method.yaml

A complete contract exercising column validation, a column-picker param, a container env, and a GPU request:

```yaml
inputs:
  data:
    type: .csv
    required: true
    description: "Labeled cell CSV"
    columns:
      strict: ["cell_index", "condition"]
      patterns: ["*_intensity"]

outputs:
  predictions:
    type: .csv
    required: true
    description: "Per-cell predictions"
  model:
    type: .pkl
    required: true
    description: "Trained classifier"

params:
  threshold:
    type: float
    required: false
    default: 0.5
    description: "Decision threshold"
  label_column:
    type: str
    required: true
    description: "Name of the label column"
    column_of_input: data        # offer columns from the `data` input slot
  score_column:
    type: str
    required: true
    description: "Name to give the new score column"
    new_column: true             # always free-text

executor: python
env: container:my-gpu-analysis   # built with: wfc register-env my-gpu-analysis
gpus: true                       # launch the container with --gpus all
```

## Next steps

- <a href="../../tutorials/authoring-a-method-script.html">Authoring a Method Script</a> — write the method script that reads these inputs and params and writes these outputs.
- <a href="../../tutorials/registering-an-environment.html">Registering an Environment</a> — build the container image that the `env` key names.
- <a href="../../tutorials/writing-contracts.html">Writing Contracts</a> — the mental model behind `columns`, module contracts, and column pickers.
