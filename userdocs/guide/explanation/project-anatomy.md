<!-- generated from pm_mvp::docs/consumer/explanation/project-anatomy @ bf331c8cdfaa; do not edit -->

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

The registry copy is wfc's, not where you edit: change the script under `src/`, then run `wfc register-method` again. Two modules can each register a method of the same name; each gets its own copy.

What git tracks:

- **Tracked:** `.wfc/wf-canvas.toml`, `.wfc/envs.json` (the record of each registered environment and its image digest), `.wfc/registry/`, and `.dvc/config`. `wfc register-env` and `wfc register-method` commit their own files. Your source under `src/` is yours to commit.
- **Ignored:** `.wfc/wfc.db`, the run outputs and restored samples under `.wfc/`, `.dvc/cache/`, and wfc's build and engine scratch directories.

Your results live outside git: the bytes in the DVC cache and its archive location, and the record of which output is which in `.wfc/wfc.db`. Back up both. [Storage & Provenance](storage-and-provenance.md) explains why.


## The Database

`.wfc/wfc.db` is a SQLite database holding the project's state: registered modules, methods and samples, and every run with its method, sample, parameters, status, metrics, outputs and the upstream runs it read. The canvas, the CLI and lineage views all read from it. You never edit it by hand; wfc commands and the canvas write it.

It is also the index to your results: each output row names the content hash that holds the output's bytes. Because it records state rather than source, it is not committed to git, so include it in your backups.

**Upgrading.** Opening a database from an earlier release brings it up to the current schema automatically. If its rows break a uniqueness rule the current release enforces, such as two methods with the same name in one module, the open stops with an error like `Cannot bring table methods up to the current schema: its rows break uq_method_module_name on (module_id, name): the key (the duplicated values) appears in more than one row. The tables are unchanged.` Nothing has been modified. Remove or rename the duplicate row it names (for example with the `sqlite3` shell, after copying `.wfc/wfc.db` as a backup), then run any wfc command again to retry.

## Configuration

Project settings live in `.wfc/wf-canvas.toml`, which is committed to git. `wfc init` writes one section, `[dvc]`, whose `url` is the archive location for your outputs (by default `~/.wfc/archives/<project>`, outside the repository; set it with `wfc init --archive`). Once you push environment images, `wfc push-env` adds a `[registry]` section with the registry address. Every key is listed in [wf-canvas.toml](../reference/wf-canvas-toml.md).

wfc finds the project by walking up from the current directory to the nearest `.wfc/wf-canvas.toml`, so you can run commands from any subdirectory.

## Modules vs. Methods

A **module** is a named group of methods, such as `cell_analysis`. It can declare required outputs and metrics that every method in it must produce, in its `module.yaml` (read with `wfc register-module --name <name> --module-dir src/<name>`) or with `--contracts`.

A **method** is one analysis script (`.py`, `.R`, `.r` or `.sh`) plus a `method.yaml` that declares its input slots, output slots, parameters and the environment it runs in. Each step runs in a container: a packaged copy of the software and dependencies your method needs (see [How the Pieces Fit Together](how-the-pieces-fit-together.md)); the environment is that container's recipe. Every method belongs to one module, named with `--module` when you register it.

Keep a method's source in `src/<module>/<method>/`, for example `src/cell_analysis/preprocess/`. `wfc register-method <method-dir> --module <module>` checks the method (its script, its contract, its environment, and the module's required outputs), copies the script and `method.yaml` into `.wfc/registry/methods/<module>/<name>/`, and commits that copy. Your source directory can be anywhere and is not committed. The cache identifies your method's code by the registered copy, so after you edit the script, run `wfc register-method` again.

[Writing a Method in Python](../tutorials/authoring-a-method-script.md) walks through writing a method, and the [registration how-to](../how-to/registration.md) covers the commands.


## Run outputs and restored samples

`.wfc/runs/` holds what running pipelines writes:

- **`.wfc/runs/<run id>/`** — one directory per run, named by the run id padded to eight digits (run 42 is `.wfc/runs/00000042/`). The method writes its outputs here, and wfc writes the run's `stdout.log` and `stderr.log` beside them.
- **`.wfc/runs/pipelines/<pipeline id>/`** — one directory per pipeline launch: the frozen copy of the pipeline that ran, the generated Snakefile, and the pipeline's logs.
- **`.wfc/runs/sentinels/`** — small marker files Snakemake uses to know which steps have finished. They hold no data.

When a pipeline succeeds, the archive pass hashes each new output and puts it into the DVC cache under `.dvc/cache/`, then removes it from the run directory; from then on the database points at the cached file, and wfc pushes it to the archive location. The run directory keeps the run's logs until `wfc cache prune` removes it. A step whose earlier result has been pruned runs again the next time instead of reusing it. If you ran with `--no-archive`, run `wfc cache archive` before pruning.

`.wfc/workspace/samples/<name>/` is where a registered sample is restored when a pipeline needs it, as the sample's file under its original file name. It is a read-only view of the cache entry, not a second copy; the sample's bytes live in the DVC cache. wfc removes the folder when the pipeline ends and restores it on the next run, so you never need to clear it.

For how outputs are stored and backed up, see [Storage & Provenance](storage-and-provenance.md). For how wfc decides whether a step re-runs, see [Caching & Reproducibility](caching-and-reproducibility.md).


## Next Steps

- [How a Run Executes](how-a-run-executes.md) — what happens to these directories when you run a pipeline.
- [wf-canvas.toml](../reference/wf-canvas-toml.md) — every key in the project config.
- [Writing a Method in Python](../tutorials/authoring-a-method-script.md) — writing a method and its `method.yaml`.
- [Registering an Environment](../tutorials/registering-an-environment.md) — building the container a method runs in.
- [Storage & Provenance](storage-and-provenance.md) and [Caching & Reproducibility](caching-and-reproducibility.md) — where outputs live and why steps re-run.
