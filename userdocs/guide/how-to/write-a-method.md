<!-- generated from pm_mvp::docs/consumer/how-to/write-a-method @ 211b09bc86a3; do not edit -->

# Write a Method

## Write a method

A method is one step of a pipeline: a folder holding a script and a `method.yaml`. The `method.yaml` is the method's *contract*: the inputs it takes, the outputs it produces, the parameters it accepts and the env it runs in. Workflow Canvas reads the contract when you register the method, when you build and load a pipeline, and when a step runs, so a wiring or naming mistake shows up before or right after the step that caused it.

This page covers what is the same in every language: the contract, registering the method, when the contract is checked, and how a method treats its inputs. The script itself is on the page for its language:

- [Draft a Method](../tutorials/drafting-a-method.md): run a method folder on your data before you register it, from the command line or in the canvas.
- [Write a Method in Python](../tutorials/authoring-a-method-script.md)
- [Write a Method in R](write-an-r-method.md)

Already have a script? An AI agent with the wfc plugin can turn it into a method folder, try it on your data and list each change it made; see [Bring a Script In as a Method](../tutorials/work-with-an-ai-agent.md#bring-a-script-in-as-a-method).

For every `method.yaml` key and its default, see [method.yaml Schema](../reference/method-yaml-schema.md).


## The method contract

**Method contract (`method.yaml`)** -- declares the inputs, outputs and parameters of one method, and the env it runs in. Each step runs in a container: a packaged copy of the software and dependencies your method needs, which wfc calls an environment, or env (see [The Tools Behind Workflow Canvas](../explanation/how-the-pieces-fit-together.md)). You write one per method, next to its script in the method's directory (`src/<module>/<method>/`):

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

If the module requires outputs, `method.yaml` must declare them; see [Register a Module](register-a-module.md).

A method can declare more outputs than the module requires; it cannot leave out a required one.


## Registering a Method

### Command

```bash
wfc register-method <method-dir> --module <module>
wfc register-method src/my_analysis/preprocess --module my_analysis
```

`<method-dir>` is the directory holding the script and `method.yaml`; `<module>` is the registered module the method joins. The method's name defaults to the directory name; set it with `--name`.

### Before you register

- The module is registered.
- The directory has a `method.yaml` with an `env:` naming a registered env (see [Register an Environment](../tutorials/registering-an-environment.md)) and at least one input.
- If the module's contract has required outputs, `method.yaml` declares them.
- The project is a git repository (`wfc init` sets this up).

wfc finds the script from the `script:` key in `method.yaml`, or from a single script file named after the method. If it finds more than one, set `script:`.

### What registration tells you first

Before it writes anything, `wfc register-method` says which of three things will happen:

- **New method:** `<module>/<method> is not registered yet; this registers it as v1.`
- **Changed since the newest version:** `<module>/<method> is already registered (newest v1); this adds v2.` It names the code files that changed and what changed in the params or contract.
- **Same as the newest version:** `<module>/<method> matches its newest version v2; no version is added.`

When a version would be added, the command then asks `Proceed? [y/N]`. Answer `y` to register; any other answer changes nothing. In a script or CI job, add `--yes` to skip the question.

### What registration does

- Records the method, its contract from `method.yaml`, and its env.
- Keeps its own copy of the method's scripts and `method.yaml` for each version. Runs use that copy, not your source folder.
- Does not copy other files in the source directory. It prints a "Not registered" list naming them. If your method reads one of these files at run time, register it as a sample and wire it as an input.
- Adds a version (`v1`, `v2`, ...) when the code, parameters, contract or env changed. Registering content that matches an earlier version again adds nothing.

If any check fails, the command prints the reason and exits with an error, and nothing is registered.

### Versions stay runnable

Registering a change never removes an earlier version. Every version stays registered and runnable, and a pipeline node can pin the version it runs; a node that does not pin follows the newest. See [Wire a Pipeline](wire-a-pipeline.md) for choosing a version on a node.

To change a method, edit its script or `method.yaml` in `src/<module>/<method>/`, then run `wfc register-method` again. Edits to the registered copy by hand never reach a run.

### Checking `save_artifact` names

For a script that uses the `@wfc.method` decorator, registration also checks each `ctx.save_artifact("<name>", ...)` call against the outputs in `method.yaml`. A saved name that `method.yaml` does not declare, or a required output that is never saved, stops registration, so a typo in an output name shows up now instead of at run time.


## When contracts are checked

**1. At registration (`wfc register-method`).** wfc reads `method.yaml` and refuses the method, with a message naming the problem, if:

- it names no `env`;
- it declares no input slot (a method that starts a pipeline takes its input from an Input Selector, so it still declares one);
- an output slot has no `type`;
- an `options:` block or a parameter `type` is malformed;
- it leaves out an output the module requires.

For example:

```
Method 'train' is missing required module output(s): ['model']. Module 'cell_classifiers' requires outputs: ['model']. Method declares: ['predictions']
```

**2. When you validate and load a pipeline.** Validating in the canvas, `wfc check-pipeline` and the load a run makes all refuse a step whose method has a required input that nothing is connected to, naming the step, the method and the input. An input counts as required unless its declaration says `required: false`; connect it or mark it `required: false`.

**3. When a step finishes.** Every output the method declares must be written by the time the step finishes. A missing output fails the step:

```
Method 'train' did not produce declared slot 'model' (expected at .../model.pkl)
```

Metrics a method reports are stored with the run; a method does not have to report any.


## Methods read their inputs and write only to their output folder

Samples and upstream results reach a method as read-only files, and the folder holding them is read-only. Write results to the step's output folder and open input files in read mode. A method that writes to or beside its inputs fails at that write. See [Write a Method in Python](../tutorials/authoring-a-method-script.md) for the file formats that need a change.

<!-- group_index: nested pages -->
```{toctree}
:hidden:

/tutorials/drafting-a-method
/tutorials/authoring-a-method-script
/how-to/write-an-r-method
```
