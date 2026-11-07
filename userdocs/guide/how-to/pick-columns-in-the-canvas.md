<!-- generated from pm_mvp::docs/consumer/how-to/pick-columns-in-the-canvas @ 234ddf202cd6; do not edit -->

# Let Users Pick Columns in the Canvas

## Declaring output options

A slot's `type: .csv` says what kind of file it is. To say which names the file carries, add an `options:` block to an *output* slot. The names can be anything the output holds: column names, gene names, sample labels.

```yaml
outputs:
  table:
    type: .csv
    options:
      fixed: ["cell_index", "condition", "proliferative"]
      patterns: ["*_intensity"]
```

Three keys, which you can combine:

| Key | Meaning |
|---|---|
| `fixed` | Exact names. |
| `from_params` | Names built from the run's parameter values (see below). |
| `patterns` | Glob patterns describing a family of names, shown in the picker and never expanded. |

Options are suggestions for the person editing the pipeline. wfc does not check a value against them. An input slot declares no options of its own: a parameter reads the options of whatever is wired to the input.

A malformed `options:` block (for example, `fixed` written as a single string instead of a list) is refused at registration with a message naming the slot.


## Worked example: from_params and a picking parameter

Use `from_params` when an output's names depend on the run's parameters. Say a method scores cells against a list of marker genes and writes one column per marker, named `<marker>_score`:

```yaml
params:
  markers:
    type: list
    default: ["cd3", "cd8"]

outputs:
  scores:
    type: .csv
    options:
      from_params:
        - params: ["markers"]
          pattern: "{}_score"
```

Each entry names one or more `params` and a `pattern`, a Python format string (default `"{}"`). wfc takes each named parameter's value (a list contributes all its items), forms every combination across the named parameters, and passes each combination through the pattern.

With `markers: ["cd3", "cd8"]` the output offers `cd3_score` and `cd8_score`. Set `markers` to `["cd3", "cd8", "foxp3"]` and it offers three names, with no change to `method.yaml`. The list follows the upstream step's defaults and variants.

A downstream method picks from these names with `options_from`. On a string parameter, `options_from: <input slot>` turns its text box into a picker of the options the upstream step wired to that input offers:

```yaml
inputs:
  data:
    type: .csv
params:
  group_by:
    type: str
    options_from: data
```

In the Inspector the parameter then offers these names as a list, filtered as you type (see [Set Parameters](set-parameters.md)).
