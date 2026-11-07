<!-- generated from pm_mvp::docs/consumer/explanation/project-anatomy @ 56f7b79ec90c; do not edit -->

# Project Anatomy

## Directory Structure

`wfc init` turns a directory into a wfc project. It creates this layout, makes it a git repository, and commits the files it wrote:

```
wfc_project_root/
  .wfc/
    wf-canvas.toml   # project config; its presence marks the project root
    wfc.db           # SQLite database: registrations, runs, lineage
  .dvc/              # DVC setup and the local content-addressed cache
  .gitignore
```

wfc commands run from this project root. Everything else wfc writes goes under `.wfc/`, so the rest of the directory is yours.

`wfc init` does not create a place for the code you write. The suggested place is `src/<module>/<method>/`, with each module's `module.yaml` in `src/<module>/`:

```
src/
  my_analysis/
    module.yaml
    preprocess/
      preprocess.py
      method.yaml
```

Registering an environment adds `.wfc/envs.json`, and registering a method copies it into the method registry under `.wfc/registry/methods/<module>/<method>/`:

```
.wfc/
  registry/
    methods/
      my_analysis/
        preprocess/
          preprocess.py
          method.yaml
```

The registry copy is wfc's, not where you edit: change the script under `src/`, then run `wfc register-method` again.

What git tracks:

- **Tracked:** `.wfc/wf-canvas.toml`, `.wfc/envs.json` (the record of each registered environment and its image digest), `.wfc/registry/`, and `.dvc/config`. `wfc register-env` and `wfc register-method` commit their own files. Your source under `src/` is yours to commit.
- **Ignored:** `.wfc/wfc.db`, the run outputs and restored samples under `.wfc/`, `.dvc/cache/`, and wfc's build and engine scratch directories.

Your results live outside git: the bytes in the DVC cache and its archive location, and the record of which output is which in `.wfc/wfc.db`. Back up both. [Storage & Provenance](storage-and-provenance.md) explains why.


## The Database

`.wfc/wfc.db` is the project's database: its registered modules, methods and samples, and every run with what went into it and what it produced. The canvas, the CLI and lineage views all read from it. You never edit it by hand; wfc commands and the canvas write it.

It is also the index to your results: it records which stored file is each run's output. git does not track it, so include it in your backups.

**Upgrading.** Opening a database from an earlier release upgrades it automatically. If the upgrade stops, its error names the duplicate row to remove or rename.


## Configuration

Project settings live in `.wfc/wf-canvas.toml`, which is committed to git. `wfc init` writes one section, `[dvc]`, whose `url` is the archive location for your outputs (by default `~/.wfc/archives/<project>`, outside the repository; set it with `wfc init --archive`). Once you push environment images, `wfc push-env` adds a `[registry]` section with the registry address. Every key is listed in [wf-canvas.toml](../reference/wf-canvas-toml.md).

You can run wfc commands from any folder inside the project.


## Modules vs. Methods

A **module** is a named group of methods, such as `cell_analysis`. It can declare required outputs and metrics that every method in it must produce, in its `module.yaml`.

A **method** is one analysis script (`.py`, `.R`, `.r` or `.sh`) plus a `method.yaml` that declares its input slots, output slots, parameters and the environment it runs in. The environment is the software the method's container is built from (see [The Tools Behind Workflow Canvas](how-the-pieces-fit-together.md)). Every method belongs to one module, named with `--module` when you register it.

Keep a method's source in `src/<module>/<method>/`, for example `src/cell_analysis/preprocess/`. `wfc register-method` checks the method and saves a copy of it in the project, so after you edit the script, run `wfc register-method` again.

[Write a Method in Python](../tutorials/authoring-a-method-script.md) walks through writing a method, and [Register a Module](../how-to/register-a-module.md) and [Write a Method](../how-to/write-a-method.md) cover the commands.


## Run outputs and restored samples

`.wfc/runs/` holds what running pipelines writes:

- **`.wfc/runs/<run id>/`** — one directory per run, named by the run id padded to eight digits (run 42 is `.wfc/runs/00000042/`). The method writes its outputs here, and wfc writes the run's `stdout.log` and `stderr.log` beside them.
- **`.wfc/runs/pipelines/<pipeline id>/`** — one directory per pipeline launch: the frozen copy of the pipeline that ran, the generated Snakefile, and the pipeline's logs.

When a pipeline succeeds, wfc moves each new output into the project's cache and backs it up to the archive; the run directory keeps the run's logs. See [Storage & Provenance](storage-and-provenance.md). If you ran with `--no-archive`, run `wfc cache archive` before pruning.

`.wfc/workspace/samples/<name>/` is where a registered sample is restored when a pipeline needs it, as the sample's file under its original file name. wfc removes it when the pipeline ends.

For how outputs are stored and backed up, see [Storage & Provenance](storage-and-provenance.md). For how wfc decides whether a step re-runs, see [Caching & Reproducibility](caching-and-reproducibility.md).


## Next Steps

- [How a Run Executes](how-a-run-executes.md) — what happens to these directories when you run a pipeline.
- [wf-canvas.toml](../reference/wf-canvas-toml.md) — every key in the project config.
- [Write a Method in Python](../tutorials/authoring-a-method-script.md) — writing a method and its `method.yaml`.
- [Register an Environment](../tutorials/registering-an-environment.md) — building the container a method runs in.
- [Storage & Provenance](storage-and-provenance.md) and [Caching & Reproducibility](caching-and-reproducibility.md) — where outputs live and why steps re-run.
