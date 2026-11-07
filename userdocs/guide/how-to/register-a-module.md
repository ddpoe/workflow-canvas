<!-- generated from pm_mvp::docs/consumer/how-to/register-a-module @ 51bdc1106084; do not edit -->

# Register a Module

## Registering a Module

A **module** groups related methods and can declare outputs and metrics that every method in it must provide. Run every command on this page from the project root (for example `wfc_project_root/`).

### Suggested layout

Keep your own code under `src/`, one directory per module and one per method:

```
wfc_project_root/
  src/
    my_analysis/
      module.yaml
      preprocess/
        preprocess.py
        method.yaml
```

### Command

Register the module before its methods, so its contract is in place when each method is checked.

```bash
wfc register-module --name my_analysis --module-dir src/my_analysis [--description "My analysis module"]
```

`--module-dir` is the directory holding the module's `module.yaml`.

### Declaring the module's contract

Put a `module.yaml` in the module's directory:

```yaml
# src/my_analysis/module.yaml
description: Normalize and score expression data
contracts:
  - type: output
    name: normalized
    value_type: .csv
    required: true
  - type: metric
    name: mcc
    value_type: float
    required: true
```

Each entry has `type` (`output` or `metric`), `name`, an optional `value_type`, and `required` (default `true`). `metric` entries and `value_type` are recorded with the module for method authors.

A module with no contract needs only the `description:` line. You can pass the contract with `--contracts` instead, as inline JSON or a JSON file.

When a method registers into the module, its `method.yaml` must declare every output the module marks `required`.

Run the command again with the same name to update the module's description and contract.
