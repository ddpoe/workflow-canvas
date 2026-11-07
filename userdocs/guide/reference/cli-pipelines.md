<!-- generated from pm_mvp::docs/consumer/reference/cli-pipelines @ de1123cdbcde; do not edit -->

# Pipeline Commands

## Pipeline Commands

### wfc run-pipeline

Run a pipeline file from the command line. This is the same run the Canvas Run button starts. wfc generates a Snakefile from the pipeline, runs it with Snakemake, and then archives the outputs unless you pass `--no-archive`.

`--pipeline` is required. To run one registered method on one sample without writing a pipeline file, use [`wfc run-method`](#wfc-run-method).

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: run-pipeline
   :nodescription:
   :noepilog:
   :nodefault:
```

When every step succeeds, the command exits 0. When a step fails, it prints the pipeline summary and then one `ERROR: <message>` line naming the failure, and exits 1. If the pipeline cannot start (for example, a sample it reads cannot be found, or Docker is not running), it stops with an error that says why and exits 1 without running anything. A pipeline the check refuses prints one `ERROR: the pipeline document is refused:` line with each reason indented under it, naming the node and the field.

A draft node's `method.yaml` is read from its folder on every run. If a pipeline no longer matches the contract (a parameter the contract no longer declares, a value of the wrong type, a link to a slot it no longer declares), the run is refused, naming the node and the field. wfc never changes your pipeline file; edit it yourself.

### wfc run-method

`wfc run-method <module>.<method> --sample <sample>` runs one registered method on one registered sample. wfc writes a one-step pipeline for it, saves it in the project's `pipelines/` folder and runs it like any pipeline, recorded and cached. The command first prints `Saved the pipeline to <path>; running it.`, or `Running <path> (already saved with this document).` when the same file is already there.

`--params` takes the method's parameter values as a JSON object, for example `--params '{"threshold": 0.5}'`; parameters it leaves out run at their declared defaults. A value that is not a JSON object is a usage error (exit 2). A parameter the method does not declare, a value of the wrong type or outside the declared range or choices, and a required parameter left out are refused before anything is saved, naming the node and the parameter:

```text
ERROR: the pipeline document is refused:
  Node 'filter_data' (my_analysis.filter_data v1): parameter 'treshold' is not declared by v1; remove it
```

Where the file goes:

- No `--params` and no `--name`: `pipelines/<module>.<method>-<sample>.json`.
- With `--params`: `pipelines/<module>.<method>-<sample>-<fingerprint>.json`. The fingerprint is 8 characters computed from the values: the same values give the same file whatever their order or spacing, and other values give another file.
- With `--name NAME`: `pipelines/NAME.json`, and the pipeline is named `NAME`. A name cannot be empty, cannot hold any of `/ \ < > : " | ? *`, and cannot end with a dot or a space.
- With `--save PATH`: that path; a relative path is taken from the project root. `--name` then names the pipeline only.

A different file already at the path is refused, naming it, and never overwritten; give the run another `--name`, or with `--save` another path. The sample feeds the method's one input: the single required one, or, when none is required, the single optional one. If several inputs could take it, the command refuses and names them; write a pipeline and check it with `wfc check-pipeline`. An unregistered method or sample is refused before anything is saved. `--cores`, `--snakefile`, `--no-archive`, `--keep-going` and `--project-root` work as for `wfc run-pipeline`, and the exit codes are the same.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: run-method
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc cancel

Stop a running pipeline by its ID, whether it was started from the Canvas or with `wfc run-pipeline`. It lists the pipeline and its running steps and asks before stopping it; `--yes` skips the question. It refuses an ID wfc has no record of.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cancel
   :nodescription:
   :noepilog:
   :nodefault:
```
### wfc check-pipeline

Check a pipeline file without running it. It runs the checks the Canvas Validate button runs (errors, warnings and refused draft nodes) and the checks `wfc run-pipeline` makes when it loads the file. It needs no Docker and changes nothing.

Warnings print as `WARNING:` lines and errors as `ERROR:` lines on stderr. A step with a required input that nothing is connected to is an error, naming the step and the input; an input marked `required: false` may stay unconnected. stdout carries one summary line (`<file> passes the check; ...` or `<file> is refused: N error(s).`). The command exits 0 when the file passes, with or without warnings, and 1 on any error. With `--json` stdout is one object with `ok`, `valid`, `errors`, `warnings` and `draftRefusals`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: check-pipeline
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc delete-draft

`wfc delete-draft <module>.<method> [--outputs-only] [--yes]` removes a method's draft. It prints what it will do as a **Deletes** list and a **Keeps** list, then a hint naming the other delete, and asks `Proceed? [y/N]`. The Canvas shows the same lists and hint in its delete dialogs.

- Without `--outputs-only` (Remove draft), it deletes the draft and everything wfc keeps for it: its link to your method folder, its draft-run record and its outputs. Results of later drafts made from it are cleared too, and the pipelines that show those results are named. It keeps your method folder and every file in it, the registered versions and their recorded runs. The hint reads `Only want to free the results? Use Delete outputs.`
- With `--outputs-only` (Delete outputs), it deletes only the draft run and its results and keeps the draft, so you can run it again. The hint reads `Want wfc to forget this draft's script and contract too? Use Remove draft instead.` On a draft with no draft run it says nothing is deleted and does not ask.

`--yes` answers the question, for scripts. With no terminal and no `--yes`, nothing is deleted and the command tells you to pass `--yes`. Answering anything but `y` deletes nothing. It takes no `--json`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: delete-draft
   :nodescription:
   :noepilog:
   :nodefault:
```
