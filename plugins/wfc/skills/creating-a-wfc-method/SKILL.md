---
name: creating-a-wfc-method
description: Use when a user wants their own code to run inside Workflow Canvas (wfc) - porting, wrapping or converting a Python, R or bash script or notebook into a wfc method or canvas pipeline step, writing or fixing a method.yaml, getting a script that works on their laptop to run reproducibly in a wfc module and env, testing it on a sample, writing a new wfc method, or changing one that is already registered (its next version). A request that names a script or analysis and also mentions wfc, workflow canvas, method.yaml, a module, an env or a sample belongs here; use it before reading the script, because wfc's method folder, method.yaml schema, wfc_client calls and try, draft, register steps are specific to wfc. Not for wfc concept questions, registering only an env or sample, debugging a failed run, or Docker, packaging or tests outside wfc.
---

# Creating a wfc method

A wfc method is a folder holding a script and a `method.yaml`. It runs in a Linux container (its env), reads the inputs a pipeline wires into it, and saves declared outputs. Bringing a script in means writing that folder, trying it until it runs, and only then registering it, because registering adds a method version and a git commit to the user's project.

This skill assumes a wfc project exists, with an env and a module registered. If not, set them up first as the **using-wfc** skill describes (`wfc init`, `wfc doctor`, `wfc register-env`, `wfc register-module`).

Work through these steps in order. Say what you are doing at each one; the user should never be surprised by something wfc recorded.

If you cannot run commands here (a chat with no access to the user's computer), still work through the steps: write the method folder's files for the user to save, and give each `wfc` command for them to run and paste back the output. Never say a command ran when you did not run it.

## 1. Read the script

Before writing anything, work out from the script:

- **Inputs**: every file or folder it reads. A hard-coded path or a path argument becomes an input slot.
- **Outputs**: every file or folder it writes. Each becomes an output slot.
- **Parameters**: values a user would change (thresholds, names, seeds), from `argparse`, constants or config. Each becomes a parameter with a type and a default. When you turn any other hard-coded constant into a parameter, say so and ask, since the user may want it fixed; a random seed is the exception (step 2).
- **What it needs to run**: packages, command-line tools, the language version.
- **What only works on this machine**: absolute host paths, Windows or macOS paths and drive letters, tools installed on the host but not in the env, environment variables, a GPU, network access, files written next to inputs.
- **What would make a rerun differ**: anything that gives different files from the same inputs and parameters. The docs page `tutorials/authoring-a-method-script.html#writing-outputs-that-rerun-the-same` lists the usual causes; check the script against it.

If the env the user names lacks something the script needs, say so now; the env has to be registered again with it (`wfc register-env <name> ... --force`).

## 2. Write the method folder, then show it

Leave the original script where it is and unchanged. Write the method in a new folder, by convention `src/<module>/<method>/`, where the folder name is the method name and the script is named after it (`<method>.py`, `.R` or `.sh`).

The folder's name decides what it becomes. Named after a method the module already has, it is that method's **draft version**, and registering it adds the next version (v2, v3, ...). With a new name, it is a **draft method**, and registering it adds the method as v1. Each method has one draft at a time. Tell the user which of the two it is before going on.

`method.yaml`:

```yaml
inputs:
  data:
    type: csv            # the file extension
    description: What the input is.
outputs:
  filtered:
    type: csv
    description: What the output is.
params:
  threshold:
    type: float
    default: 0.5
env: <registered-env-name>
```

An input that takes several files needs `multiple: true`. The docs page `reference/method-yaml-schema.html` lists every key, `tutorials/drafting-a-method.html` explains how drafts work, and `how-to/draft-from-the-command-line.html` walks through `wfc try` to registering (the **using-wfc** skill says which docs version to read).

For Python, change the script's edges and leave its analysis alone. Read inputs and parameters from `ctx`, write into `ctx.workdir`, and save each output by its declared name:

```python
import wfc_client as wfc

@wfc.method
def filter_data(ctx):
    data_path = ctx.input("data")          # one path; a list with multiple: true
    threshold = float(ctx.params["threshold"])
    out_path = ctx.workdir / "filtered.csv"
    # ... the original analysis, unchanged ...
    ctx.save_artifact("filtered", out_path)  # the name as a string literal

if __name__ == "__main__":
    wfc.run()
```

The env needs `wfc-client` installed. For R or bash, follow the docs page `reference/method-script-contract.html` (and `how-to/write-an-r-method.html`), which passes inputs and parameters in environment variables.

**Keep reruns identical.** wfc records each run and reuses results on the promise that the same inputs and parameters give the same files, and most users don't know what quietly breaks that. Fix each cause you found in step 1 as part of writing the method, without waiting to be asked: a random seed becomes a parameter, its default the seed the script used or a fixed one when it had none, so every run records it; a format that stores the time it was made gets that left out. Mention only what this script has, never the whole list, and give each fix one plain line with its reason, such as "Your PDF stored the time it was made, so the same run gave a different file each time; I left the date out."

Then show the user `method.yaml` and the changes to the script (a diff against the original), and say how each input, output and parameter maps to the original script. List every change to what the script computes, reads or writes, however small (a new seed, a changed read option, a dropped print): the user has to trust that the method does what their script did, so don't describe one change as the only one unless it is.

## 3. Try it on a real sample, until it runs

`wfc try` needs a registered sample. Ask the user which one to use (the canvas lists the project's samples); if none fits, ask which data to use and register it (`wfc register-sample --name <name> --source <path>`). A sample is the user's data, not a method version.

```bash
wfc try src/<module>/<method> --module <module> --sample <sample>
```

Add `--params '{"threshold": 0.7}'` to set parameters. Nothing is registered or recorded. The folder becomes the module's draft of the method, and the command ends with `Export its outputs with:` and one `wfc export --run draft:... --slot <slot> --dest <dir>` line per output. Export the outputs and look at them: `wfc export --run draft:... --all --dest <dir>` creates the folder and writes every output into it, while `--slot` with a folder that doesn't exist yet writes the file under that folder's name. Put `<dir>` outside the project (a temporary folder) unless the user names one, so the check leaves no untracked files in their project, and tell them where it is.

When it fails, read the error, fix the method folder (or the env), and try again. Repeat until it runs and the outputs look right. If you fixed something that made reruns differ, try it a second time and compare the two exports (their hashes): they should be identical. Common causes: a package missing from the env, a host path, a file written outside `ctx.workdir`, an output saved under a name `method.yaml` doesn't declare.

Edits to the script and to `method.yaml` are both picked up by the next `wfc try`: every run reads the draft's `method.yaml` again, so no Update is needed first. If the pipeline or the try still sets a param, or links a slot, that `method.yaml` no longer declares, the run is refused naming the node and the field; change the one or the other and try again. **↻ Update** on the draft in the canvas Registry's **Methods** tab checks `method.yaml` without running anything.

## 4. Look at it as a draft in the canvas

If the canvas is running (the user says so, or `http://127.0.0.1:8500` answers), suggest opening the Registry's **Methods** tab: the folder is listed under **Drafts**, and **+ Draft from folder…** adds one from a folder path. The draft's panel shows the **Registration check** and a **Draft run** tab with the last draft run's outputs. In the Builder the draft can be dropped into a pipeline next to registered methods; steps downstream of it run as drafts too. The docs page `how-to/draft-in-the-canvas.html` walks through it.

If the canvas isn't running, offer to start it with `wfc canvas`; don't start it unasked.

## 5. Stay in draft until the user is satisfied

Suggest that the user keep the method as a draft while they check its outputs: a draft can be changed and run again as often as needed, and nothing it does is recorded. Ask whether they want it registered. **Register only when the user says yes.** In the same question, ask whether they want an intake record of the change (step 8), and where: suggest `wfc-intake/<module>.<method>.md` at the project root, or the place they name.

## 6. Register, when the user agrees

```bash
wfc register-method src/<module>/<method> --module <module>
```

It first says whether it registers a new method, adds a version or adds nothing, then asks to proceed; in an agent's shell it can't ask, so it needs `--yes`. The try that made the folder the draft printed what registering would do (`registering it adds <method> as v1`, or `adds v2`); a later try of the same draft prints only `Uses <module>.<method>'s draft`, and then the next version is the newest one `wfc list-methods` shows, plus one. Tell the user that when you ask, and once they say yes, run the command with `--yes`. Don't start it without `--yes` to preview it. Registering copies the folder into the project and commits it to git; runs use that copy from now on. In the canvas, **Register as v<N>** on the draft's panel does the same and also removes the draft; after `wfc register-method` the draft stays until `wfc delete-draft <module>.<method>` removes it.

## 7. One recorded test run

Register the sample for the run if it isn't registered yet. Then run the method once, so the run is recorded:

```bash
wfc run-method <module>.<method> --sample <sample>
```

Add `--params '{"threshold": 0.7}'` for values other than the defaults. It saves a one-step pipeline in the project's `pipelines/` folder, says where, and runs it like any pipeline; it ends with a pipeline summary whose `Status:` line reads `succeeded`. `wfc runs --method <module>.<method>` shows the recorded run. A method with more than one input the sample could feed is refused, naming them: then write a pipeline that feeds each one (the docs page `reference/pipeline-document.html` lists every key), check it with `wfc check-pipeline --pipeline <file>.json`, and run it with `wfc run-pipeline --pipeline <file>.json`. In the canvas, running a pipeline that holds the method does the same.

## 8. The intake record, if the user wants one

When the user said yes to a record in step 5, write it after registering, and finish it after the recorded run. It keeps one file per method, `wfc-intake/<module>.<method>.md` by default, with one dated section per registered version, newest first, headed like `## v1 — 2026-11-06`. wfc doesn't keep or read this file (a registered version holds only the scripts and `method.yaml`), so tell the user it is theirs to commit with their method folder. Nothing is written for a draft that isn't registered. A v1 section includes:

- **Changes to the script**: which script it came from, what was changed and why, with the slot and parameter mapping.
- **Environment differences**: what is different between the host the script ran on and the Linux container: operating system and paths, package and language versions, tools present on the host but not in the env, CPU threads, GPU, locale, line endings.
- **Suspected consequences**: where those differences might change results (numeric precision, random seeds, sort order, file encoding, a missing optional dependency), and how to check.
- **How to run it**: the module, method, env and sample names, and the commands used.

## Changing a registered method

To change a method that is already registered (a fix, a new parameter, a new output), edit the user's own method folder, by convention `src/<module>/<method>/`, never the registered copy under `.wfc/`. That folder is now the draft of the next version: go through steps 3 to 6 again (try it, look at it, register it when the user agrees), and registering adds the next version. Earlier versions stay registered, with their runs. Show the change as a diff against the registered version. When the method has an intake record (look in `wfc-intake/` first, and ask the user where they keep it if it isn't there), add the new version's section on top: what changed from the previous version and why, and the try results. When there is none, ask once, with the register question, whether to start one.

In a pipeline, a node with no version pinned runs the newest version; a node pinned to a version keeps it until the pin is changed. Say which of the user's pipelines will pick up the new version.

## If wfc itself seems wrong

Only wfc's own behaviour counts as a wfc bug: a wfc traceback or crash, a result that contradicts the docs or `--help` for the installed version, or an error that doesn't say what to do. An error from the user's script, Docker not running, or an env that isn't registered is setup: say so and point to `wfc doctor`. Something unintuitive that works as documented is a rough edge, not a bug.

Before calling it a bug, reproduce it once, check the docs for the installed version (the **using-wfc** skill says which), and search the existing issues (`gh issue list --repo ddpoe/workflow-canvas --search "<words>"`, when `gh` is available).

Don't interrupt the task for it. If you work around a wfc bug, say that you are doing so; never work around one silently. At the end, add one short block:

> **I think I found a wfc bug:** `<command>` did `<what happened>`; I expected `<what the docs or --help say>`.

Then offer to draft an issue with the wfc version (`wfc --version`), the OS, the command, its output and what was expected, the body ending with "Reported by an agent using the wfc skill." File it only when the user says yes: `gh issue create --repo ddpoe/workflow-canvas`, or, without `gh`, a prefilled link `https://github.com/ddpoe/workflow-canvas/issues/new?title=<title>&body=<body>` with both parts URL-encoded.
