<!-- generated from pm_mvp::docs/consumer/how-to/registration @ 77f88bbfc730; do not edit -->

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

wfc keeps its own registered copy of each method in `.wfc/registry/methods/<module>/<name>/`; don't author there.

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

### What registration tells you first

Before it writes anything, `wfc register-method` says which of three things will happen:

- **New method:** `<module>/<method> is not registered yet; this registers it as v1.`
- **Changed since the newest version:** `<module>/<method> is already registered (newest v1); this adds v2.` It names the code files that changed and what changed in the params or contract.
- **Same as the newest version:** `<module>/<method> matches its newest version v2; no version is added.`

When a version would be added, the command then asks `Proceed? [y/N]`. Answer `y` to register. Any other answer, or just Enter, prints `Nothing was registered.`, exits with an error and changes nothing. Add `--yes` to skip the question; the message is still printed. With no terminal (a script or CI job) and no `--yes`, a command that would add a version refuses and tells you to pass `--yes`; one that adds nothing still runs.

If you only changed descriptions or formatting in `method.yaml`, the command says the edit changes nothing a run uses, that it is not kept, and adds no version.

### What registration does

- Records the method, its contract from `method.yaml`, and its env.
- For a Python script, scans its public functions and their parameters.
- Copies the method's scripts and `method.yaml` into `.wfc/registry/methods/<module>/<name>/` and keeps a read-only copy of that version's code for runs. wfc fingerprints the code to decide whether a step's earlier result can be reused.
- Does not copy other files in the source directory. It prints a "Not registered" list naming them. If your method reads one of these files at run time, register it as a sample and wire it as an input.
- Commits that copy, and only that copy, to git. Your source directory is not committed.
- Adds a version (`v1`, `v2`, ...) when the code, parameters, contract or env changed. Registering content that matches an earlier version again adds nothing.

If any check fails, the command prints the reason and exits with an error, and nothing is registered.

Two modules can each register a method of the same name; each has its own copy and versions.

### Versions stay runnable

Registering a change never removes an earlier version. Every version stays registered and runnable, and a pipeline node can pin the version it runs; a node that does not pin follows the newest. A pinned node keeps its cached results when a newer version is registered. `wfc run-pipeline` prints a line for each unpinned node naming the version it ran on. See [Building a Pipeline in the Canvas](canvas.md) for choosing a version on a node.

Each version records the image of its env. If Docker no longer has that image, wfc pulls it from the registry when it was pushed (see [Share a Project](sharing-a-project.md)); an image that was never pushed, and is gone, leaves a run on that version refused until you register the method again. If a version's env is a container image you build yourself, give each rebuild its own image tag: when a rebuild reuses a tag, the older version's image is gone.

To change a method, edit its script or `method.yaml` in `src/<module>/<method>/`, then run `wfc register-method` again. Edits to the registered copy by hand never reach a run.

### Checking `save_artifact` names

For a script that uses the `@wfc.method` decorator, registration also checks each `ctx.save_artifact("<name>", ...)` call against the outputs in `method.yaml`. A saved name that `method.yaml` does not declare, or a required output that is never saved, stops registration, so a typo in an output name shows up now instead of at run time.

The `@wfc.method` decorator is optional. Plain scripts that read the `WFC_*` environment variables register the same way; see the [Method Script Contract](../reference/method-script-contract.md).


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

Pipelines copy each sample they need from the cache into `.wfc/workspace/samples/<name>/` when they run, so you do not copy data there yourself. To restore one by hand:

```bash
wfc restore-sample --name CFPAC_ERKi
```

wfc takes the sample from the local cache, or pulls it from the archive if the cache does not have it, and checks its content hash.

## Registering from the Canvas

You can also register modules, methods and samples in the canvas. Open the **Registry** tab, choose the **Modules**, **Methods** or **Samples** list, and click **+ Register**. The dialog has a switch for module, method or sample.

- **Module:** enter a name and an optional description. The module is registered with no contract; to add one, write a `module.yaml` and run `wfc register-module` again.
- **Sample:** enter a name and the absolute path to the source file.

For a module or a sample, click **Dry run** to run the same checks as registration without saving anything; each check is listed as passed, warning or failed. Click **Register** to save it. If the server refuses, the error appears in the dialog and nothing is saved.

### A method

The Method form starts with a choice: **New method** or **New method version**.

- **New method:** enter the method's folder, or click **Browse…** to pick it from the project, and choose the module. The method name is optional and defaults to the folder's name. If the module already has a method of that name, the form says so and offers **Register a new version instead**.
- **New method version:** choose the module, then the method, then the folder. Once the method is chosen the form shows its current version and the one this registration adds. **Register a new version…** in a method's **⋯** menu opens this form with the module and method already chosen.

Click **Register method** (or **Register version**) to check the folder. The check runs the same rules as `wfc register-method` and saves nothing:

- If the folder cannot be registered, each problem is shown in red with what wfc expects, for example the `script:` line to add to `method.yaml`. Fix the folder and click **Check again**.
- If it can, the dialog shows what the new version holds: what changed since the newest version (code files, inputs and outputs, parameters, env), each with its count and a before-and-after table, then the version's main script, its helpers and the files it leaves out. A new method lists everything it registers. Click **Confirm: register vN** to save exactly what was checked. If the folder changed in between, nothing is saved and the dialog checks it again.
- If the folder holds the same content as the newest version, the dialog says **nothing to register**.

**← Back** returns to the form with your choices kept. **×**, **Cancel** and Escape close the dialog without saving anything.

Envs are registered from the command line with `wfc register-env`.

## Next Steps

- **[Registering an Environment](../tutorials/registering-an-environment.md)**: build the env a method names.
- **[Build, Run and Inspect in the Canvas](canvas.md)**: build a pipeline from your registered methods and samples, run it, and look at its results and History.
- **[Run and Inspect from the Command Line](run-and-inspect-results.md)**: run, inspect and export results from the command line.


## Methods read their inputs and write only to their output folder

Samples and upstream results reach a method as read-only files, and the folder holding them is read-only. Write results to the step's output folder and open input files in read mode. A method that saves over its input, or writes an index, lock or memo file beside it, fails at that write. See [Tutorial: Writing a Method in Python](../tutorials/authoring-a-method-script.md) for the cases that need a change (SQLite in WAL mode, HDF5 and zarr opened for update, readers that write memo files).
