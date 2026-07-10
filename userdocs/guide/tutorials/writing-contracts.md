<!-- generated from pm_mvp::docs.consumer.tutorials.writing-contracts @ 4078d6d1a2b7; do not edit -->

# Tutorial: Writing Contracts

## Writing contracts

A *contract* is the promise a method makes about its data: which inputs it accepts, which outputs it produces, and what those files must contain. Contracts are how Workflow Canvas catches a broken pipeline early -- when you wire two nodes together, or the moment a step starts -- instead of after a long run has already burned compute and produced garbage.

This tutorial covers the three things you need to write contracts well:

- **The two tiers** -- a *module* contract that every method in a module must honor, and a *method* contract that declares each method's own inputs and outputs.
- **Column validation** -- declaring which CSV/Parquet columns a slot requires, so a typo'd or missing column is caught instead of silently dropped.
- **The three enforcement points** -- registration, pipeline load, and runtime -- and the mental model for *why* a pipeline is rejected at one stage versus another.

This builds directly on the method you wrote in [Authoring a Method Script](../tutorials/authoring-a-method-script.md). If you haven't authored a method yet, start there. For the exhaustive field-by-field reference (every key and its default), see <a href="../reference/reference/method-yaml-schema.html">method.yaml Schema</a> -- this tutorial teaches the model, that page is the lookup table.

## Two tiers: module and method contracts

Contracts live at two levels, and they compose.

**Method contract (`method.yaml`)** -- declares the inputs, outputs, and parameters of *one* method. This file is the source of truth for that method's I/O shape. You write one per method, alongside its script:

```yaml
# methods/binary_labeling/method.yaml
inputs:
  data:
    type: .csv
    required: true
    description: "Labeled cell CSV"
outputs:
  predictions:
    type: .csv
    required: true
  model:
    type: .pkl
    required: true
params:
  threshold:
    type: float
    default: 0.5
```

**Module contract (`module.yaml`)** -- sits one level up and declares guarantees that *every* method in the module must satisfy. Use it for domain-wide promises: "every classifier in this module produces a `model` output and reports an `mcc` metric."

```yaml
# modules/cell_classifiers/module.yaml
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

Each module-contract entry has four fields: `type` (`output` or `metric`), `name` (the required output/metric name), `value_type` (the expected type, e.g. `float`, `model`, `.csv`), and `required` (default `true`).

**How they compose:** the method contract defines the *shape* (which slots exist and their types); the module contract guarantees that certain *named* outputs and metrics are always present, no matter which method you run. A method is free to declare more outputs than the module requires -- it just can't declare fewer of the required ones.

## Declaring module contracts: file vs. CLI

You can declare a module's contracts in `modules/{module}/module.yaml` (shown above) or pass them inline when you register the module:

```bash
wfc register-module --name cell_classifiers \
  --contracts '[{"type":"output","name":"model","value_type":"model","required":true}]'
```

`--contracts` accepts either an inline JSON string or a path to a JSON file. The flag and `module.yaml` serve the same purpose, so what happens when both are present? **The CLI `--contracts` flag takes precedence, and `module.yaml` is the fallback.** In day-to-day work, prefer `module.yaml` -- it lives next to the code, is version-controlled, and needs no re-typing. Reach for `--contracts` only when you're scripting registration or want to override the file for a one-off.

Method contracts have no CLI equivalent: `method.yaml` is always the source of truth for a method's own inputs and outputs.

## Column validation: what's inside the file

Declaring a slot's `type: .csv` only promises the *file format*. Often you need to promise the file's *contents* -- that it actually has the columns the next step reads. For `csv` and `parquet` slots, add a `columns:` block:

```yaml
inputs:
  data:
    type: .csv
    columns:
      strict: ["cell_index", "condition", "proliferative"]
      patterns: ["*_intensity"]
```

There are three ways to declare required columns, and they combine freely:

| Key | Meaning |
|---|---|
| `strict` | Exact column names that must be present, verbatim. |
| `from_params` | Column names *derived* from this run's parameter values (see below). |
| `patterns` | fnmatch-style globs; at least one real column must match each pattern. |

**The hard/soft split is the key idea.** Where you put the `columns:` block changes how strictly it's enforced:

- On an **input** slot, column validation is a **hard gate**. If a required column is missing when the step starts, the step fails immediately with an error naming the missing column. Nothing downstream runs on bad data.
- On an **output** slot, column validation is a **soft check**. A missing column produces a *warning* in the run log but does not fail the step. The idea is that you control your own outputs, so drift there is a heads-up, not a wall -- whereas inputs arrive from upstream and must be trusted before you compute on them.

## Worked example: from_params

`strict` is fine when column names are fixed. But often a method's required columns depend on its *parameters* -- you don't know the exact names until the run is configured. That's what `from_params` is for: it builds required column names from this run's parameter values.

Say a method scores each cell against a list of marker genes, and it expects one score column per marker, named like `<marker>_score`. The marker list is a parameter:

```yaml
params:
  markers:
    type: list
    required: true

inputs:
  data:
    type: .csv
    columns:
      from_params:
        - params: ["markers"]
          pattern: "{}_score"
```

Each `from_params` entry names one or more `params` and a `pattern` (a Python `str.format` template; the default is `"{}"`, which just uses the value as-is). At validation time, every named param's value is collected (a list expands to all its items), the **cartesian product** across the named params is taken, and each combination is fed through the pattern.

So if a run sets `markers: ["cd3", "cd8"]`, the contract requires the input CSV to contain `cd3_score` **and** `cd8_score`. Change the param to `["cd3", "cd8", "foxp3"]` and the contract automatically requires three columns -- no edit to `method.yaml`.

You can cross multiple params for a grid. Two params, `markers: ["cd3", "cd8"]` and `stains: ["dapi"]`, with `pattern: "{}_{}"` require `cd3_dapi` and `cd8_dapi` -- one column per combination. If any named param is missing from the run, that entry is skipped rather than erroring, so optional params won't break validation.

A practical companion to `from_params` lives on the parameter side: in the canvas inspector, a string param can declare `column_of_input: <slot>` to turn its text box into a dropdown populated from the *upstream* node's declared columns, or `new_column: true` to mark a param that *names a column this method produces* (kept as free text). These are authoring conveniences for picking column names correctly; they reuse the same column vocabulary your contracts declare.

## Where contracts are enforced

Contracts are checked at three distinct moments. Knowing which check fires when is the difference between a confusing failure and an obvious one.

**1. At registration (`wfc register-method`).** When you register a method, Workflow Canvas parses its `method.yaml` and validates two things against the module: every *required module output* must appear among the method's output slots, and the method must declare *at least one input slot* (a root-like method takes its input from a system node such as the Input Selector, never from nothing). A method that omits a required module output, or declares no inputs, fails registration with a message listing what's missing. Additionally, each output slot's `type` is validated at registration — a bare un-dotted name (`csv`), stale semantic name (`anndata`, `model`, `figure`), or missing/empty value raises a `ValueError` with a guidance message explaining the dotted-extension convention (e.g. `.csv`, `.pkl`, `.h5ad`). This is the earliest, cheapest check -- it runs before the method is ever wired into a pipeline.

**2. At pipeline load (`load_pipeline`).** When a pipeline is loaded -- whether you run it from the CLI or validate the graph in the canvas -- the wiring is checked against the declared slots: every required input slot must be connected, and connections must respect declared types. On top of that, a *static* column cross-check runs: for any two connected steps, the `strict` columns the downstream input requires are compared against the `strict` columns the upstream output declares. If the upstream provably can't supply a column the downstream demands, the pipeline is rejected here -- before a single step executes. (`from_params` and `patterns` columns are deferred to runtime, since their exact names depend on parameter values and real data.)

**3. At runtime (inside the running step).** When a step actually runs, the input column hard gate fires first: the input files are read and checked for all required columns (`strict`, expanded `from_params`, and `patterns`), and a missing column raises an error before your code runs. After your script finishes, two more checks run: the module contract is verified by looking for the required outputs and metrics in `WFC_RUN_DIR` (a missing required output, or a non-zero exit, fails the step), and the output column soft check warns on any output drift.

**The mental model -- load vs. runtime.** A pipeline is rejected *at load* when the failure is knowable from the contracts alone: a missing wire, a type mismatch, or a `strict` column the topology can never supply. It's rejected *at runtime* when the failure depends on the actual data or the resolved parameter values: a real input file that's missing a column, a `from_params` column that wasn't produced, or a declared output your script never wrote. Load-time checks are about *can this pipeline possibly be valid*; runtime checks are about *did this specific run honor the contract*.

## Next steps

You can now declare contracts at both tiers, validate file contents by column, and reason about which enforcement stage will catch which mistake.

- For the complete `method.yaml` field reference -- every input, output, and param key, all column-spec options, and the `env`/`executor` fields -- see <a href="../reference/reference/method-yaml-schema.html">method.yaml Schema</a>.
- To author the script those contracts wrap, revisit [Authoring a Method Script](../tutorials/authoring-a-method-script.md).
- Before a contracted method can run, its environment must be built and registered; see [Registering an Environment](../tutorials/registering-an-environment.md).
- To wire contracted methods into a pipeline and watch type checks block bad connections live, see [Canvas Visual Builder](../how-to/canvas.md).
