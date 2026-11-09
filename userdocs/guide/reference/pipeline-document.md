<!-- generated from pm_mvp::docs/consumer/reference/pipeline-document @ 2d0f09b9fd2e; do not edit -->

# Pipeline Document

## Overview

A pipeline document is the JSON file `wfc run-pipeline --pipeline <file>` runs. The Canvas writes one when you click **Export** or **Download pipeline JSON**, and you can write one by hand. This page lists every key the document takes.

A document is refused before anything runs when it carries a key wfc does not read. The refusal names the node and the key, and lists the keys allowed there:

```text
ERROR: the pipeline document is refused:
  Node 'cohort_in': unknown key 'mode' (a node takes env, env_strategy, fan_mode, folder, id, keep_going, label, method, module, output_ext, params, position, run_id, samples, slot_outputs, slot_types, source, type, version)
```

## Top-level keys

| Key | Required | Meaning |
|---|---|---|
| `nodes` | yes | The nodes: input selectors, method nodes and run references. |
| `links` | yes | The connections between nodes. |
| `name` | no | The pipeline's name, shown in History. |
| `samples` | no | A list of sample names. The samples of every per-sample input selector are added to it. |
| `param_sets` | no | Variants: named parameter sets per node. See [Variants](#variants). |
| `explicit_combos` | no | The exact sample and variant pairs to run. See [Variants](#variants). |
| `variables` | no | Pipeline variables. See [Variables](#variables). |
| `keep_going` | no | The Canvas's **Keep going** switch. `wfc run-pipeline` does not read it; pass `--keep-going` instead. |

## Nodes

Every node has an `id`, unique in the document, and a `type`: `method` (the default when `type` is left out), `input_selector` or `run_reference`. `label` is an optional display name.

**Method node.** Runs a registered method.

| Key | Meaning |
|---|---|
| `module`, `method` | The registered method. |
| `params` | Parameter values by name. A parameter left out runs at the default its `method.yaml` declares. |
| `version` | Optional pin to a registered version, such as `"v2"`, or `"draft"` to run the method's draft (made by `wfc try` or the canvas). A draft node and every node downstream of it run as drafts: nothing is recorded or cached. Without it the node runs the newest version. A downloaded pipeline JSON pins every node to the version it ran. |
| `folder` | With `"version": "draft"`: the draft's folder, as an absolute path. Leave it out to run the method's current draft. |

**Input selector** (`"type": "input_selector"`). Chooses registered samples.

| Key | Meaning |
|---|---|
| `samples` | The sample names. |
| `fan_mode` | `"out"` (the default): each consumer runs once per sample. `"in"`: the consumer runs once and receives all the samples together on one input; every step downstream of it also runs once. |

**Run reference** (`"type": "run_reference"`). Feeds an output of an earlier run into a method.

| Key | Meaning |
|---|---|
| `run_id` | The run whose output it delivers. The link's `source_slot` names which output; it can be left out when the run has only one. |

The Canvas also writes keys for its own layout and records, such as `position`. They are accepted; a hand-written document leaves them out.


## Links

A link connects one node's output to another node's input.

| Key | Meaning |
|---|---|
| `source` | The id of the node the data comes from. |
| `target` | The id of the node it goes to. |
| `source_slot` | Which output of the source, by the name its `method.yaml` declares. Needed when the source has several outputs. |
| `target_slot` | Which input of the target. Defaults to `data`. |

The Canvas writes `sourceHandle` and `targetHandle` for the two slots; they are read the same way.

These wirings are refused:

- A method node with no incoming link, in a document that has an input selector or a run reference: there, only input selectors and run references start a pipeline.
- Two links into one input, unless every link into it comes from a run reference and the input is declared `multiple: true`; then they arrive as a list, in link order.
- A `source_slot` or `target_slot` that names an output or input the node's version of the method does not declare. The refusal names the node and the slot, and lists the declared ones.
- An input nothing is connected to, unless its `method.yaml` declares it `required: false`. The refusal names the node and the input.
- More than one file into an input that takes one file (an input without `multiple: true` in its `method.yaml`), such as a fan-in selector bundling several samples into it. The refusal names the node and the input.
- A method node fed by two per-sample selectors. Put both samples on one selector, or give the second its own method node.
- A fan-in selector with no samples, or one whose consumer has another upstream node.
- A step that runs once over a fan-in bundle and also takes the outputs of a step that runs per sample.

## Variants

`param_sets` maps a node id to its variants, each a name and the parameter values that differ from the node's `params`:

```json
"param_sets": {
  "qc": {"loose": {"threshold": 0.5}, "strict": {"threshold": 0.9}}
}
```

The variant names of all nodes form one list, and every node runs every name on it; a node with no variant of that name runs its own `params`. Each step runs once per sample and variant.

`explicit_combos` replaces that with an exact list of pairs, run by every step:

```json
"explicit_combos": [
  {"sample": "cells_a", "variant": "strict"},
  {"sample": "cells_b", "variant": "loose"}
]
```

A pipeline with a fan-in selector cannot use `explicit_combos`.

## Variables

`variables` names values that several parameters share. A value is written bare or as `{"type": ..., "value": ...}`, and a parameter uses it with `{"$var": "<name>"}`:

```json
"variables": {"cut": {"type": "number", "value": 0.25}},
"nodes": [
  {"id": "qc", "module": "my_analysis", "method": "filter_data",
   "params": {"min_quality": {"$var": "cut"}}}
]
```

An unknown name is refused, and so is a variable whose value is itself a reference.


## Example: fan-in

Three samples go into one run of `merge_counts`, whose `tables` input takes several files; `plot` runs once on the result:

```json
{
  "name": "merged",
  "nodes": [
    {"id": "all", "type": "input_selector", "fan_mode": "in",
     "samples": ["run_1", "run_2", "run_3"]},
    {"id": "merge", "module": "my_analysis", "method": "merge_counts"},
    {"id": "plot", "module": "my_analysis", "method": "plot_counts",
     "params": {"title": "All runs"}}
  ],
  "links": [
    {"source": "all", "target": "merge", "target_slot": "tables"},
    {"source": "merge", "source_slot": "merged", "target": "plot"}
  ]
}
```
