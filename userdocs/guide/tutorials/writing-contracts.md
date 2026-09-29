<!-- generated from pm_mvp::docs.consumer.tutorials.writing-contracts @ d549a21f3d7a; do not edit -->

# Tutorial: Writing Contracts

## Writing contracts

A *contract* is what a method declares about its data: which inputs it takes, which outputs it produces, and which columns those files carry. Workflow Canvas reads contracts when you register a method, when you build and load a pipeline, and when a step runs, so a wiring or naming mistake shows up before or right after the step that caused it.

This tutorial covers:

- **Module and method contracts** -- a module contract names outputs every method in the module must declare; a method contract declares one method's inputs, outputs and parameters.
- **Column declarations** -- naming the columns a CSV or Parquet slot carries.
- **When contracts are checked** -- at registration, when a pipeline is validated and loaded, and when a step runs.

It builds on the method from [Authoring a Method Script](authoring-a-method-script.md). For every key and its default, see [method.yaml Schema](../reference/method-yaml-schema.md).


## Two tiers: module and method contracts

**Method contract (`method.yaml`)** -- declares the inputs, outputs and parameters of one method, and the env it runs in. Each step runs in a container: a packaged copy of the software and dependencies your method needs, which wfc calls an environment, or env (see [How the Pieces Fit Together](../explanation/how-the-pieces-fit-together.md)). You write one per method, next to its script in the method's directory (`src/<module>/<method>/`):

```yaml
# src/cell_classifiers/binary_labeling/method.yaml
env: sklearn
inputs:
  data:
    type: .csv
    required: true
    description: "Labeled cell CSV"
outputs:
  predictions:
    type: .csv
  model:
    type: .pkl
params:
  threshold:
    type: float
    default: 0.5
```

An output slot's `type` is the file extension of the file it produces (`csv` and `.csv` are the same), or `dir` for a directory. An input slot's `type` describes what the slot expects and is shown in the canvas.

**Module contract (`module.yaml`)** -- sits one level up, in the module's directory (`src/<module>/module.yaml`), and names outputs that every method in the module must declare. Use it for promises that hold across a module, such as "every classifier produces a `model`":

```yaml
# src/cell_classifiers/module.yaml
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

Each entry has `type` (`output` or `metric`), `name`, an optional `value_type`, and `required` (default `true`). When you register a method, wfc checks every required `output` entry: the method's `method.yaml` must declare an output slot with that name. `metric` entries and `value_type` are recorded with the module as documentation for method authors.

A method can declare more outputs than the module requires; it cannot leave out a required one.


## Registering a module contract

Register the module before its methods, so its contract is in place when each method is checked. From the project root, point `wfc register-module` at the module's directory with `--module-dir`; it reads `module.yaml` from there:

```bash
wfc register-module --name cell_classifiers --module-dir src/cell_classifiers
```

You can instead pass the contracts inline, as a JSON string or a path to a JSON file:

```bash
wfc register-module --name cell_classifiers \
  --contracts '[{"type":"output","name":"model","value_type":"model","required":true}]'
```

When both are present, `--contracts` wins. Prefer `module.yaml`: it lives next to the code and is version-controlled. Registering a module again replaces its contracts with the new set.

Method contracts have no CLI equivalent: `method.yaml` is always where a method's inputs and outputs are declared. Register the method from its directory:

```bash
wfc register-method src/cell_classifiers/binary_labeling --module cell_classifiers
```

The first argument is the method's directory; `--module` names the module it joins. See [register-method](../reference/cli-reference.md) in the CLI reference.


## Declaring columns

A slot's `type: .csv` says what kind of file it is. To say which columns the file carries, add a `columns:` block to a CSV or Parquet slot:

```yaml
inputs:
  data:
    type: .csv
    columns:
      strict: ["cell_index", "condition", "proliferative"]
      patterns: ["*_intensity"]
```

Three keys, which you can combine:

| Key | Meaning |
|---|---|
| `strict` | Exact column names. |
| `from_params` | Column names built from the run's parameter values (see below). |
| `patterns` | Glob patterns describing a family of columns, for readers of the contract. |

wfc uses column declarations in two places:

- **In the canvas**, a parameter can offer the declared columns of an upstream node as a dropdown (see the end of the next section).
- **When a pipeline loads**, the `strict` columns an input slot lists are compared with the `strict` columns the connected upstream output lists, and a mismatch is logged as a warning.

Column declarations describe names; wfc does not open your data files to compare them. A malformed `columns:` block (for example, `strict` written as a single string instead of a list) is refused at registration with a message naming the slot.


## Worked example: from_params

Use `from_params` when a slot's column names depend on the run's parameters. Say a method scores cells against a list of marker genes and expects one column per marker, named `<marker>_score`:

```yaml
params:
  markers:
    type: list
    default: ["cd3", "cd8"]

inputs:
  data:
    type: .csv
    columns:
      from_params:
        - params: ["markers"]
          pattern: "{}_score"
```

Each entry names one or more `params` and a `pattern`, a Python format string (default `"{}"`). wfc takes each named parameter's value (a list contributes all its items), forms every combination across the named parameters, and passes each combination through the pattern.

With `markers: ["cd3", "cd8"]` the slot declares `cd3_score` and `cd8_score`. Set `markers` to `["cd3", "cd8", "foxp3"]` and it declares three columns, with no change to `method.yaml`. Two parameters, `markers: ["cd3", "cd8"]` and `stains: ["dapi"]`, with `pattern: "{}_{}"` declare `cd3_dapi` and `cd8_dapi`. An entry whose parameter the run does not set contributes no columns.

Two parameter keys use these declarations in the canvas inspector. On a string parameter, `column_of_input: <slot>` turns its text box into a dropdown of the columns the upstream node declares for that input slot. `new_column: true` marks a parameter that names a column this method creates; it stays a free-text box.


## When contracts are checked

**1. At registration (`wfc register-method`).** wfc reads `method.yaml` and refuses the method, with a message naming the problem, if:

- it names no `env`;
- it declares no input slot (a method that starts a pipeline takes its input from an Input Selector, so it still declares one);
- an output slot has no `type`;
- a `columns:` block or a parameter `type` is malformed;
- it leaves out an output the module requires.

For example:

```
Method 'train' is missing required module output(s): ['model']. Module 'cell_classifiers' requires outputs: ['model']. Method declares: ['predictions']
```

**2. When you validate and load a pipeline.** Validating in the canvas warns about any required input slot left unconnected. When the pipeline loads, the `strict` column comparison between connected steps runs and logs a warning for each mismatch.

**3. When a step starts.** A step whose method declares an input slot with `required: true` that nothing in the pipeline feeds is refused, and the message tells you to wire the slot or mark it `required: false`.

**4. When a step finishes.** Every output slot the method declares must exist, either in the run directory under its slot filename or at the path the method recorded for it. A missing output fails the step:

```
Method 'train' did not produce declared slot 'model' (expected at .../model.pkl)
```

Metrics a method reports are stored with the run; a method does not have to report any.


## Next steps

- For every `method.yaml` key, including all column options and the `env` and `executor` fields, see [method.yaml Schema](../reference/method-yaml-schema.md).
- To write the script a contract describes, see [Authoring a Method Script](authoring-a-method-script.md).
- To build and register the env a method runs in, see [Registering an Environment](registering-an-environment.md).
- To wire methods into a pipeline, see [Build, Run and Inspect in the Canvas](../how-to/canvas.md).

