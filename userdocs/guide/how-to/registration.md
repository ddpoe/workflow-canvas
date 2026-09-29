<!-- generated from pm_mvp::docs.consumer.how-to.registration @ a17f4f921d1b; do not edit -->

# Registering Modules, Methods, and Samples

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

wfc keeps its own registered copy of each method in `methods/<name>/`; don't author there.

### Command

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

A module with no contract needs only the `description:` line. You can instead pass the contract with `--contracts`, as inline JSON or the path to a JSON file; `--contracts` takes precedence over `module.yaml`.

When a method registers into the module, its `method.yaml` must declare every output the module marks `required`.

Run the command again with the same name to update the module's description and contract.


## Registering a Method

A **method** is one analysis step: a script (Python, R or bash) plus a `method.yaml` that declares its inputs, outputs, parameters and env. Each step runs in a container: a packaged copy of the software and dependencies your method needs, which wfc calls an environment, or env (see [How the Pieces Fit Together](../explanation/how-the-pieces-fit-together.md)).

### Command

```bash
wfc register-method <method-dir> --module <module>
wfc register-method src/my_analysis/preprocess --module my_analysis
```

`<method-dir>` is the directory holding the script and `method.yaml`; `<module>` is the registered module the method joins. The method's name defaults to the directory name; set it with `--name`.

### Before you register

- The module is registered.
- The directory has a `method.yaml` with an `env:` naming a registered env (see [Registering an Environment](../tutorials/registering-an-environment.md)) and at least one input.
- If the module's contract has required outputs, `method.yaml` declares them.
- The project is a git repository (`wfc init` sets this up).

wfc finds the script by `--script <file>`, then the `script:` key in `method.yaml`, then a single file named `<name>.py`, `<name>.R`, `<name>.r` or `<name>.sh`. If it finds more than one candidate, registration stops and asks you to set `script:`.

### What registration does

- Records the method, its contract from `method.yaml`, and its env.
- For a Python script, scans its public functions and their parameters.
- Commits the method directory to git.
- Copies the method's scripts and `method.yaml` into `methods/<name>/`. wfc fingerprints this copy to decide whether a step's earlier result can be reused.

If any check fails, the command prints the reason and exits with an error.

To change a method, edit its script or `method.yaml` in `src/<module>/<method>/`, then run `wfc register-method` again.

### Checking `save_artifact` names

For a script that uses the `@wfc.method` decorator, registration also checks each `ctx.save_artifact("<name>", ...)` call against the outputs in `method.yaml`. A saved name that `method.yaml` does not declare, or a required output that is never saved, stops registration, so a typo in an output name shows up now instead of at run time.

The `@wfc.method` decorator is optional. Plain scripts that read the `WFC_*` environment variables register the same way; see [Authoring a Method Script](../tutorials/authoring-a-method-script.md).

## Registering a Sample

A **sample** is a data file or a directory of files that a pipeline starts from.

### Command

```bash
wfc register-sample --name CFPAC_ERKi --source /data/raw/cfpac_erki.csv
```

`--source` can be a file or a directory. Sample names must be unique; registering a name that already exists is refused.

The project needs its DVC archive set up, which `wfc init` does. If it is not set up, registration stops with an error naming what is missing in `.wfc/wf-canvas.toml`.

### What registration does

wfc hashes the source, stores its content in the project's DVC cache, and pushes it to the archive. The source file stays where it is, and the sample is identified by its content hash from then on.

### Adding a description

Pass `--manifest <file>` to attach a description. The file is a small YAML file:

```yaml
description: CFPAC cells treated with ERK inhibitor, replicate 1
```

The description is shown when you pick samples in the canvas. It does not change the sample's identity.

### Restoring samples

Pipelines copy each sample they need from the cache into `data/samples/` when they run, so you do not copy data there yourself. To restore one by hand:

```bash
wfc restore-sample --name CFPAC_ERKi
```

wfc takes the sample from the local cache, or pulls it from the archive if the cache does not have it, and checks its content hash.

## Registering from the Canvas

You can also register modules, methods and samples in the canvas. Open the **Registry** tab, choose the **Modules**, **Methods** or **Samples** list, and click **+ Register**. The dialog has a switch for module, method or sample.

- **Module:** enter a name and an optional description. The module is registered with no contract; to add one, write a `module.yaml` and run `wfc register-module` again.
- **Method:** enter the method directory, or click **Browse…** to pick it from the project, and choose the module. The method name is optional and defaults to the directory name.
- **Sample:** enter a name and the absolute path to the source file.

Click **Dry run** to run the same checks as registration without saving anything. Each check is listed as passed, warning or failed. Click **Register** to save. If the server refuses, the error appears in the dialog and nothing is saved.

Envs are registered from the command line with `wfc register-env`.

## Next Steps

- **[Registering an Environment](../tutorials/registering-an-environment.md)**: build the env a method names.
- **[Build, Run and Inspect in the Canvas](canvas.md)**: build a pipeline from your registered methods and samples, run it, and look at its results and History.
- **[Run and Inspect from the Command Line](run-and-inspect-results.md)**: run, inspect and export results from the command line.

