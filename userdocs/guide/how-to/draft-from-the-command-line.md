<!-- generated from pm_mvp::docs/consumer/how-to/draft-from-the-command-line @ abd3360ea3c6; do not edit -->

# Draft from the Command Line

## Overview

This page takes the `filter_data` method from [Write a Method in Python](../tutorials/authoring-a-method-script.md) through a draft to registration, from the command line. How drafts work is on [Draft a Method](../tutorials/drafting-a-method.md). You can also do this in the canvas: [Draft in the Canvas](draft-in-the-canvas.md).

You need:

- a project ([Your First Pipeline](../tutorials/getting-started.md)), with an environment registered ([Register an Environment](../tutorials/registering-an-environment.md));
- the module registered: `wfc register-module --name my_analysis --module-dir src/my_analysis`;
- the method folder `src/my_analysis/filter_data/`, with its `method.yaml` and `filter_data.py`, not yet registered;
- a registered sample with a `score` column, for example `wfc register-sample --name scores --source raw/scores.csv`.

The commands run from the project root.

## Step 1 — Try the folder on a sample

Run the folder on the sample:

```bash
wfc try src/my_analysis/filter_data --module my_analysis --sample scores
```

`--module` is the module the method belongs to, and `--sample` is the sample it runs on. The step runs in the environment `method.yaml` names, in a container, as it would in a pipeline. The first lines say what the folder becomes:

```text
Adds .../src/my_analysis/filter_data as the draft of my_analysis.filter_data. Module my_analysis has no method called filter_data. This folder becomes a DRAFT method; registering it adds filter_data as v1.
```

When the step finishes, the command names the draft step and gives an export command for each output:

```text
Tried my_analysis.filter_data on scores: draft:my_analysis.filter_data/filter_data@scores. Nothing was recorded.
Export its outputs with:
  wfc export --run draft:my_analysis.filter_data/filter_data@scores --slot filtered --dest <dir>
```

The `draft:...` string names this draft step. Commands that take a run ID, such as `wfc export --run`, also take it.

Put a folder of your choice in place of `<dir>` when you run the export command.

To set parameters, add `--params`, for example `--params '{"threshold": 0.8}'`. A parameter you leave out runs at its `default`.

If the step fails, the command ends with `The try of my_analysis.filter_data failed; nothing was recorded.` Read the error above it, fix the script and run `wfc try` again.


## Step 2 — Look at the outputs

Copy an output out with the export command the try printed:

```bash
wfc export --run draft:my_analysis.filter_data/filter_data@scores --slot filtered --dest results
```

This writes `filtered.csv` into the `results` folder (use any folder you like). Open it and check the rows are the ones you expect.

`wfc runs` lists the draft step with the recorded runs, with its draft string in the RUN column:

```text
RUN                                                PIPELINE         NODE         METHOD                  SAMPLE  STATUS
draft:my_analysis.filter_data/filter_data@scores   try-filter_data  filter_data  my_analysis.filter_data  scores  completed
```

## Step 3 — Change it and try again

Edit `filter_data.py` or `method.yaml` and run the same `wfc try` command again. The draft runs the folder as it is now, and its new results replace the last ones:

```text
Uses my_analysis.filter_data's draft, from .../src/my_analysis/filter_data.
Replaces the draft run of 14:02:07 in try-filter_data.
```

A changed `method.yaml` is checked again before the run, as registering would check it; see [Edit the Folder, Run It Again](../tutorials/drafting-a-method.md#edit-the-folder-run-it-again).

## Step 4 — Register it

When the outputs are right, register the method:

```bash
wfc register-method src/my_analysis/filter_data --module my_analysis
```

It says first that this registers `filter_data` as v1, and asks before it does. From then on the method's runs are recorded and cached. The draft stays; trying the folder again makes it the draft of v2.

To remove a draft instead, run `wfc delete-draft my_analysis.filter_data`. It removes the draft and its results, not your folder; with `--outputs-only` it removes only the results. It lists what goes and asks first.

## Next steps

- Every option of these commands: [wfc try](../reference/cli-environments.md#wfc-try), [wfc delete-draft](../reference/cli-pipelines.md#wfc-delete-draft) and [wfc register-method](../reference/cli-registration.md#wfc-register-method).
- Open a shell or Jupyter with a step's inputs and parameters: `wfc shell --method <dir> --module <module>` and `wfc jupyter --method <dir> --module <module>` ([Environment and Dev-Loop Commands](../reference/cli-environments.md)).
- Use the method in a pipeline: [Wire a Pipeline](wire-a-pipeline.md).
