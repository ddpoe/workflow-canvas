<!-- generated from pm_mvp::docs.consumer.explanation.project-anatomy @ 68b3f897d035; do not edit -->

# Project Anatomy

## Directory Structure

`wfc init` turns a directory into a wfc project. It creates this layout, makes it a git repository, and commits the files it wrote:

```
wfc_project_root/
  .wfc/
    wf-canvas.toml   # project config; its presence marks the project root
    wfc.db           # SQLite database: registrations, runs, lineage
  modules/           # created empty; the suggested layout keeps your code in src/ instead
  methods/           # wfc's registered copies of your methods, one directory each
  data/samples/      # where registered samples are restored for a run
  .runs/             # run outputs, logs and per-pipeline files
  .dvc/              # DVC setup and the local content-addressed cache
  .gitignore
```

wfc commands run from this project root.

`wfc init` does not create a place for the code you write. The suggested place is `src/<module>/<method>/`, with each module's `module.yaml` in `src/<module>/`:

```
src/
  my_analysis/
    module.yaml
    preprocess/
      preprocess.py
      method.yaml
```

Registering an environment adds `.wfc/envs.json`, and registering a method copies it into `methods/`:

```
methods/
  preprocess/
    preprocess.py
    method.yaml
```

`methods/<name>/` is wfc's copy, not where you edit: change the script under `src/`, then run `wfc register-method` again.

What git tracks:

- **Tracked:** `.wfc/wf-canvas.toml`, `.wfc/envs.json` (the record of each registered environment and its image digest), `methods/`, your method directories, and `.dvc/config`. `wfc register-env` and `wfc register-method` commit their own files.
- **Ignored:** `.wfc/wfc.db`, `.runs/`, `data/`, `.dvc/cache/`, and wfc's build and engine scratch directories.

Your results live outside git: the bytes in the DVC cache and its archive location, and the record of which output is which in `.wfc/wfc.db`. Back up both. [Storage & Provenance](storage-and-provenance.md) explains why.


## The Database

`.wfc/wfc.db` is a SQLite database holding the project's state: registered modules, methods and samples, and every run with its method, sample, parameters, status, metrics, outputs and the upstream runs it read. The canvas, the CLI and lineage views all read from it. You never edit it by hand; wfc commands and the canvas write it.

It is also the index to your results: each output row names the content hash that holds the output's bytes. Because it records state rather than source, it is not committed to git, so include it in your backups.

## Configuration

Project settings live in `.wfc/wf-canvas.toml`, which is committed to git. `wfc init` writes one section, `[dvc]`, whose `url` is the archive location for your outputs (by default `~/.wfc/archives/<project>`, outside the repository; set it with `wfc init --archive`). Every key is listed in [wf-canvas.toml](../reference/wf-canvas-toml.md).

wfc finds the project by walking up from the current directory to the nearest `.wfc/wf-canvas.toml`, so you can run commands from any subdirectory.

## Modules vs. Methods

A **module** is a named group of methods, such as `cell_analysis`. It can declare required outputs and metrics that every method in it must produce, in its `module.yaml` (read with `wfc register-module --name <name> --module-dir src/<name>`) or with `--contracts`.

A **method** is one analysis script (`.py`, `.R`, `.r` or `.sh`) plus a `method.yaml` that declares its input slots, output slots, parameters and the environment it runs in. Each step runs in a container: a packaged copy of the software and dependencies your method needs (see [How the Pieces Fit Together](how-the-pieces-fit-together.md)); the environment is that container's recipe. Every method belongs to one module, named with `--module` when you register it.

Keep a method's source in `src/<module>/<method>/`, for example `src/cell_analysis/preprocess/`. `wfc register-method <method-dir> --module <module>` checks the method (its script, its contract, its environment, and the module's required outputs), copies the script and `method.yaml` into `methods/<name>/`, and commits that copy. The cache identifies your method's code by that registered copy, so after you edit the script, run `wfc register-method` again.

[Authoring a Method Script](../tutorials/authoring-a-method-script.md) walks through writing a method, and the [registration how-to](../how-to/registration.md) covers the commands.


## Run outputs and restored samples

`.runs/` holds what running pipelines writes:

- **`.runs/<run id>/`** — one directory per run, named by the run id padded to eight digits (run 42 is `.runs/00000042/`). The method writes its outputs here, and wfc writes the run's `stdout.log` and `stderr.log` beside them.
- **`.runs/pipelines/<pipeline id>/`** — one directory per pipeline launch: the frozen copy of the pipeline that ran, the generated Snakefile, and the pipeline's logs.
- **`.runs/sentinels/`** — small marker files Snakemake uses to know which steps have finished. They hold no data.

When a pipeline succeeds, the archive pass hashes each new output and copies it into the DVC cache under `.dvc/cache/`; from then on the database points at the cached copy, and wfc pushes it to the archive location. The run directory stays until `wfc cache prune` removes it. A step whose earlier result has been pruned runs again the next time instead of reusing it. If you ran with `--no-archive`, run `wfc cache archive` before pruning.

`data/samples/<name>/` is where a registered sample is restored when a pipeline needs it. The sample's bytes live in the DVC cache, so this directory can be deleted and is refilled on the next run.

For how outputs are stored and backed up, see [Storage & Provenance](storage-and-provenance.md). For how wfc decides whether a step re-runs, see [Caching & Reproducibility](caching-and-reproducibility.md).

## Next Steps

- [How a Run Executes](how-a-run-executes.md) — what happens to these directories when you run a pipeline.
- [wf-canvas.toml](../reference/wf-canvas-toml.md) — every key in the project config.
- [Authoring a Method Script](../tutorials/authoring-a-method-script.md) — writing a method and its `method.yaml`.
- [Registering an Environment](../tutorials/registering-an-environment.md) — building the container a method runs in.
- [Storage & Provenance](storage-and-provenance.md) and [Caching & Reproducibility](caching-and-reproducibility.md) — where outputs live and why steps re-run.
