<!-- generated from pm_mvp::docs/consumer/how-to/run-and-inspect-results @ 820d028c1a1c; do not edit -->

# How-to: Run and Inspect from the Command Line

## Run and inspect from the command line

This guide covers running a pipeline with `wfc run-pipeline`, seeing what ran with `wfc runs`, copying a run's output files out with `wfc export`, and packaging runs as a zip anyone can rerun with `wfc export-run`. Run every command from your project root (for example `wfc_project_root/`).

To build, run and browse results in the browser instead, including the History tab, see [Build, Run and Inspect in the Canvas](canvas.md).

## Run a pipeline

A pipeline is a JSON file. The usual way to get one is to build the pipeline in the Canvas Builder and click **Export**, which downloads it named after the pipeline. Run it with:

```bash
wfc run-pipeline --pipeline my-pipeline.json
```

`--pipeline` is the path to the pipeline JSON file. The command runs the same pipeline the Canvas would, including variants, per-sample values, fan-in and pipeline variables. Each step runs in a container: a packaged copy of the software and dependencies your method needs (see [How the Pieces Fit Together](../explanation/how-the-pieces-fit-together.md)), so Docker must be running. Steps whose results already exist are reused instead of re-run (see [Caching and Reproducibility](../explanation/caching-and-reproducibility.md)).

A parameter a node leaves unset runs at the default its `method.yaml` declares. For each node that runs any declared default, `wfc run-pipeline` prints a line naming each one and its value. A variant gets its own line, with `variant '<name>'` after the node id:

```text
[run_pipeline] node 'filter' (my_analysis/filter_data) runs declared defaults: threshold=0.5
```

Options:

- `--cores N`: how many steps can run in parallel (default 4).
- `--keep-going`: when one step fails, independent steps keep running, so the other samples of a fan-out still finish. Without it, the first failure stops the pipeline.
- `--no-archive`: skip archiving outputs when the pipeline finishes. Run `wfc cache archive` later to archive them.
- `--project-root <path>`: run against a different project than the current directory.
- `--snakefile <path>`: where to write the generated workflow file, if you want to keep it somewhere specific.

The full list is in the [CLI Reference](../reference/cli-reference.md#wfc-run-pipeline).

To stop a running pipeline from another terminal, run `wfc cancel <pipeline id>`. It shows what it will stop and asks first; `--yes` skips the question.

## See what ran

When the pipeline ends, successfully or not, `wfc run-pipeline` prints a summary counted from its runs:

```text
============================================================
PIPELINE SUMMARY
============================================================
Pipeline ID: <pipeline-id>
Status: succeeded
Total runs: 6  |  Passed: 4  |  Failed: 0  |  Cached: 2  |  Cancelled: 0
```

`Status` is `succeeded` when every run succeeded, ran or cached; `failed` when none succeeded and at least one failed; `cancelled` when every run was cancelled; and `mixed` when some succeeded and others failed or were cancelled. Every step the pipeline schedules, for each sample and each variant, becomes one **run** with a numeric run ID. `Cached` counts runs that reused an earlier result instead of executing. If any run failed, the summary adds a `FAILED RUNS:` list with each failed run's ID, sample and error, and the command ends with a single `ERROR:` line and exit code 1. A run that never started ends cancelled: because an upstream step it needed failed, because you stopped the pipeline, or because the pipeline ended before it started.

To list the runs later, use `wfc runs`. It prints one line per run with its run ID, pipeline, node, method and version, sample and status; `wfc runs --pipeline <pipeline-id>` lists only that pipeline's runs, and `wfc runs --method <module>.<method>` only one method's runs.

A run records the parameter values it ran with, declared defaults included, the parent runs and samples it read, its output files and metrics, its log, and its status. The History tab in the Canvas shows all of this for every run, including runs started from the command line, and its detail panel has a button to copy a run's ID for `wfc export`; see [Build, Run and Inspect in the Canvas](canvas.md#the-history-tab).

Output files are not kept in a per-run folder. When the pipeline finishes, each output is moved into the project's read-only cache and the run records where to find it. `wfc export` copies outputs out of it. If an output isn't in the local cache but has been pushed to your DVC remote, it is pulled when you ask for it. See [Storage and Provenance](../explanation/storage-and-provenance.md) for where outputs live.

## Export a run's outputs

Outputs in the cache are read-only. `wfc export` copies them out as normal files you can edit, or prints where they are so you can read them in place. It takes its arguments as named flags:

```bash
wfc export --run <run-id> --slot <slot> --dest <dest>
```

- `--run <run-id>`: the numeric ID of the run (`wfc runs` lists them).
- `--slot <slot>`: the name of the output slot to export (leave it out to list them).
- `--dest <dest>`: where to put the copy, a directory or a file path.

Every run ID works, including cached runs, which export the outputs of the run they reused. The flags can come in any order. See the [CLI Reference](../reference/cli-reference.md#wfc-export) for every option.

### List a run's outputs

```bash
wfc export --run 412
```

With only a run ID, the command lists the run's output slots and the file each was saved as.

### Copy one output

```bash
wfc export --run 412 --slot counts --dest results/
```

This copies the `counts` output of run 412. If `results/` is an existing directory, the file lands inside it under its original name; otherwise the copy is written to that path. If a file is already there, add `--force` to overwrite it.

### Copy every output

```bash
wfc export --run 412 --all --dest results/
```

With `--all`, leave out `--slot` and give a directory as `--dest`. Each output goes into it under its file name, and a directory output under its own folder. If two outputs were saved with the same file name, each goes into a folder named after its slot, for example `qc/report.csv` and `final/report.csv`. If any target already exists, nothing is copied until you add `--force`.

### Read a large output in place

```bash
p=$(wfc export --run 412 --slot masks --path)
```

`--path` prints the output's local path instead of copying, so leave out `--dest`. Nothing else goes to stdout, so it works in scripts. The file is read-only: you can read it, but writing to it fails. With `--all`, `--path` prints one `name<TAB>path` line per output.

If a run's outputs haven't been archived yet (for example after `wfc run-pipeline --no-archive`), export tells you to run `wfc cache archive` first.

## Share runs as a bundle anyone can rerun

`wfc export-run` packages runs, and every run above them, as one zip. The person who receives it does not need wfc: they unzip it and run `python rerun.py`, which reruns each step in Docker and compares every output with the recorded one.

```bash
wfc export-run --run 412 --out bundle.zip
```

- `--run <run-id>`: a run to include, with every run it read from. Repeat the flag for more runs.
- `--out <file.zip>`: the zip to write. It must be a file path, not a directory.
- `--with-outputs`: also put the original output files in the zip, so the receiver can compare them with the rerun's. Without it the zip holds only their hashes.
- `--with-image`: also save each step's container image in the zip, for a receiver who cannot pull it.
- `--yes`: answer yes when a step's image was never pushed to a registry and `--with-image` is not given. Without a terminal and without `--yes`, such an export is refused.

The command prints the plan first. If anything stops the bundle from being complete, for example a run that did not finish, it lists every reason and writes nothing. A bundle that includes runs from before wfc recorded how each step was launched cannot be made; the plan names those runs.

The Canvas does the same: in the History tab, **Export run** in a run's detail panel, or **Export runs** after selecting several, opens a dialog with **Include outputs** and **Include images** options. Changing either shows the plan again before anything downloads.

### Rerun a bundle

The receiver unzips the file, then runs this in the unzipped folder:

```bash
python rerun.py
```

It needs Python 3.11 or newer and Docker. It prints `same` or `DIFFERENT` for each output. The exit code is 0 when every output is the same, 1 on any difference or any step that could not run, and 2 when the rerun could not start. The rerun's files go in a new `rerun-<time>/` folder, or in `--work DIR`. The bundle's `README.md` describes everything in it.

### What the receiver needs

- **With images included** (`--with-image`, or **Include images** in the Canvas): Python 3.11 or newer and Docker. The zip holds the samples, the code and the images, so nothing else is needed.
- **Without images:** the same, and each image must be pullable from the project's registry. A public image needs no login. A private GHCR image needs a GitHub login with `read:packages` and read access to the repository; see [Access by role](sharing-a-project.md#access-by-role).

An image that was never pushed to a registry has to be included, or the receiver cannot run that step. The export says so and asks before it writes such a bundle.

### If a step says DIFFERENT

`same` means the rerun wrote exactly the file that was recorded. `DIFFERENT` means the bytes differ, which on its own does not mean the result changed. Common causes that leave the result unchanged:

- the file records when it was made (PDFs and many image formats store a creation date);
- a different CPU or number of threads changes the last digits of a number;
- rows written in whatever order a parallel job finished;
- randomness without a fixed seed.

To find out:

1. Start at the first difference `rerun.py` names. Later differences often follow from it.
2. Compare the rerun's file with the original in `outputs/<step>/`: open both figures, or compare tables by value with a tolerance. A bundle exported without `--with-outputs` has no originals to compare, so **export with `--with-outputs` if the receiver should be able to compare**.
3. Small numeric differences that stay small further down the pipeline are expected between machines. A difference that changes a conclusion needs a full investigation from that step: a gene crossing a significance threshold, or a sample landing in a different cluster. Check that `rerun.py` reported the image as matching, compare the input hashes, and read the step's logs in `rerun-<time>/logs/`; the original run's logs are in `steps/<step>/logs/`.

To make your own outputs rerun the same, see [Writing outputs that rerun the same](../tutorials/authoring-a-method-script.md#writing-outputs-that-rerun-the-same).

## Next steps

- [Build, Run and Inspect in the Canvas](canvas.md): build pipelines, run them over many samples and parameter values, and browse runs in the History tab.
- [Caching and Reproducibility](../explanation/caching-and-reproducibility.md): when a step is reused and how a run traces back to its samples.
- [Storage and Provenance](../explanation/storage-and-provenance.md): where outputs are stored and how to keep them recoverable.
- [CLI Reference](../reference/cli-reference.md): every `wfc` command and flag.
