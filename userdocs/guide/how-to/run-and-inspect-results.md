<!-- generated from pm_mvp::docs.consumer.how-to.run-and-inspect-results @ 6ef3704b85f0; do not edit -->

# How-to: Run and Inspect from the Command Line

## Run and inspect from the command line

This guide covers running a pipeline with `wfc run-pipeline`, seeing what ran, and copying a run's output files out with `wfc export`. Run every command from your project root (for example `wfc_project_root/`).

To build, run and browse results in the browser instead, including the History tab, see [Build, Run and Inspect in the Canvas](canvas.md).

## Run a pipeline

A pipeline is a JSON file. The usual way to get one is to build the pipeline in the Canvas Builder and click **Export**, which downloads it named after the pipeline. Run it with:

```bash
wfc run-pipeline --pipeline my-pipeline.json
```

`--pipeline` is the path to the pipeline JSON file. The command runs the same pipeline the Canvas would, including variants, per-sample values, fan-in and pipeline variables. Each step runs in a container: a packaged copy of the software and dependencies your method needs (see [How the Pieces Fit Together](../explanation/how-the-pieces-fit-together.md)), so Docker must be running. Steps whose results already exist are reused instead of re-run (see [Caching and Reproducibility](../explanation/caching-and-reproducibility.md)).

Options:

- `--cores N`: how many steps can run in parallel (default 4).
- `--keep-going`: when one step fails, independent steps keep running, so the other samples of a fan-out still finish. Without it, the first failure stops the pipeline.
- `--no-archive`: skip archiving outputs when the pipeline finishes. Run `wfc cache archive` later to archive them.
- `--project-root <path>`: run against a different project than the current directory.
- `--snakefile <path>`: where to write the generated workflow file, if you want to keep it somewhere specific.

The full list is in the [CLI Reference](../reference/cli-reference.md#wfc-run-pipeline).

## See what ran

When the pipeline ends, successfully or not, `wfc run-pipeline` prints a summary counted from its runs:

```text
============================================================
PIPELINE SUMMARY
============================================================
Pipeline ID: <pipeline-id>
Status: completed
Total runs: 6  |  Passed: 4  |  Failed: 0  |  Cached: 2  |  Cancelled: 0
```

Every step the pipeline schedules, for each sample and each variant, becomes one **run** with a numeric run ID. `Cached` counts runs that reused an earlier result instead of executing. If any run failed, the summary adds a `FAILED RUNS:` list with each failed run's ID, sample and error, and the command ends with a single `ERROR:` line and exit code 1. A run is cancelled when an upstream step it needed failed first.

A run records the parameters it ran with, the parent runs and samples it read, its output files and metrics, its log, and its status. The History tab in the Canvas shows all of this for every run, including runs started from the command line, and its detail panel has a button to copy a run's ID for `wfc export`; see [Build, Run and Inspect in the Canvas](canvas.md#the-history-tab).

Output files are not kept in a per-run folder. When the pipeline finishes, each output is moved into the project's read-only cache and the run records where to find it. `wfc export` copies outputs out of it. If an output isn't in the local cache but has been pushed to your DVC remote, it is pulled when you ask for it. See [Storage and Provenance](../explanation/storage-and-provenance.md) for where outputs live.

## Export a run's outputs

Outputs in the cache are read-only. `wfc export` copies them out as normal files you can edit, or prints where they are so you can read them in place. It takes up to three positional arguments:

```bash
wfc export <run-id> <slot> <dest>
```

- `<run-id>`: the numeric ID of the run.
- `<slot>`: the name of the output slot to export (leave it out to list them).
- `<dest>`: where to put the copy, a directory or a file path.

Every run ID works, including cached runs, which export the outputs of the run they reused. See the [CLI Reference](../reference/cli-reference.md#wfc-export) for every option.

### List a run's outputs

```bash
wfc export 412
```

With only a run ID, the command lists the run's output slots and the file each was saved as.

### Copy one output

```bash
wfc export 412 counts results/
```

This copies the `counts` output of run 412. If `results/` is an existing directory, the file lands inside it under its original name; otherwise the copy is written to that path. If a file is already there, add `--force` to overwrite it.

### Copy every output

```bash
wfc export 412 --all results/
```

With `--all`, leave out `<slot>` and give a directory as `<dest>`. Each output goes into it under its file name, and a directory output under its own folder. If two outputs were saved with the same file name, each goes into a folder named after its slot, for example `qc/report.csv` and `final/report.csv`. If any target already exists, nothing is copied until you add `--force`.

### Read a large output in place

```bash
p=$(wfc export 412 masks --path)
```

`--path` prints the output's local path instead of copying it, so leave out `<dest>`. Nothing else goes to stdout, so it works in scripts. The file is read-only: you can read it, but writing to it fails. With `--all`, `--path` prints one `name<TAB>path` line per output.

If a run's outputs haven't been archived yet (for example after `wfc run-pipeline --no-archive`), export tells you to run `wfc cache archive` first.

## Next steps

- [Build, Run and Inspect in the Canvas](canvas.md): build pipelines, run them over many samples and parameter values, and browse runs in the History tab.
- [Caching and Reproducibility](../explanation/caching-and-reproducibility.md): when a step is reused and how a run traces back to its samples.
- [Storage and Provenance](../explanation/storage-and-provenance.md): where outputs are stored and how to keep them recoverable.
- [CLI Reference](../reference/cli-reference.md): every `wfc` command and flag.
