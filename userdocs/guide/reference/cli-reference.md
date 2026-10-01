<!-- generated from pm_mvp::docs.consumer.reference.cli-reference @ 6f7abe87a494; do not edit -->

# CLI Reference

## Overview

Every command is run as `wfc <command>` (or `python -m wfc <command>`). `wfc <command> --help` prints the same flags listed here.

Commands find the project by walking up from the current directory to the nearest folder that contains `.wfc/wf-canvas.toml`, so you can run them from any subdirectory of a project. Set `WFC_PROJECT_ROOT` to name the project explicitly (see [Environment Variables](#environment-variables)).

`wfc --help` also lists a few commands this page does not cover, such as `run-step` and `pipeline-summary`. Generated pipelines call those; you do not run them by hand.

Exit codes: `0` means success, `1` means the command refused or failed and printed why, and `2` means the arguments were invalid.

## Project Commands

### wfc init

Make a directory into a wfc project, then run the other commands from inside it:

```bash
wfc init --dir wfc_project_root
cd wfc_project_root
```

`wfc init`:

1. Creates `.wfc/`, `modules/`, `methods/`, `data/samples/` and `.runs/`. It does not create a place for your own code; the suggested place is `src/<module>/<method>/` (see [Project Anatomy](../explanation/project-anatomy.md)).
2. Writes `.wfc/wf-canvas.toml` with a `[dvc]` section naming the output archive (see [wf-canvas.toml](wf-canvas-toml.md)), and creates the project database `.wfc/wfc.db`.
3. Adds wfc's entries to `.gitignore` and sets up DVC for the archive.
4. Runs `git init` if the directory is not a repository yet, and commits the files it wrote. If git has no global identity, the commit uses `wfc <wfc@wfc>`; change it with `git config user.name` / `user.email`. The repository is local only; wfc never pushes.
5. Prints a health table (the same checks as `wfc doctor`).

The archive location is where copies of your run outputs are kept. `wfc init` asks for it, with `~/.wfc/archives/<project>` as the default. Give a directory path, or a DVC remote URL such as `s3://bucket/path` (the matching DVC plugin must be installed, for example `pip install 'dvc[s3]'`).

In an existing project, `wfc init` lists what it would add and asks once before changing anything (in a script, pass `--yes`). When nothing is missing it says the project is already current and changes nothing. It never overwrites a config key you already set.

A missing git or Docker does not stop `wfc init`: the files are still written, and the health table shows what to install. Install it, re-run `wfc init` to fill in what is missing, then run `wfc doctor`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: init
   :nodescription:
   :noepilog:
   :nodefault:
```

The `.wfc/wfc.db` database indexes everything in the archive and is not tracked in git. Back up the `.wfc/` directory to keep archived outputs recoverable.

### wfc doctor

Check whether the project is ready to run. `wfc doctor` prints a health table with one row per check:

- **git**: git is installed, the project is a repository with a commit, and the working tree is clean.
- **dvc**: an archive is configured, set up in DVC, and reachable.
- **docker**: `docker` is installed and the daemon is running.
- **samples**: every registered sample's content is still in the local cache or the archive.

Each check reports `ok`, `warn` or `fail`, with a fix hint under any row that is not `ok`. `wfc doctor` exits 1 if any check fails (a `warn` does not), so it works as a pre-run check in scripts and CI. It takes no arguments.

### wfc demo

Add a complete, runnable demo pipeline to an initialised project and open it in the Canvas. `wfc demo` builds a small container environment, registers a module with five methods and three samples, writes `demo-pipeline.json`, and opens the Canvas with that pipeline loaded. Press Run to execute it.

Run `wfc init` first; `wfc demo` also needs Docker running. If a check fails, it exits 1 and changes nothing. If a demo is already present, pass `--force` to replace it.

`wfc demo --remove` deletes exactly what the demo added (its module, methods, samples, environment, runs, files and pipeline) and leaves everything you registered yourself. It lists what it will remove and asks first unless you pass `--yes`. Demo entries use the `__demo__` name prefix, which you cannot use for your own names.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: demo
   :nodescription:
   :noepilog:
   :nodefault:
```

For a walkthrough see [Exploring the Demo](../tutorials/wfc-demo.md).

### wfc canvas

Start the Canvas web UI for the current project and print its address.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: canvas
   :nodescription:
   :noepilog:
   :nodefault:
```

For a first-project walkthrough, see [Getting Started](../tutorials/getting-started.md).

## Registration Commands

The examples use the suggested layout: your code under `src/<module>/<method>/`, with each module's `module.yaml` in `src/<module>/`, and commands run from the project root.

### wfc register-module

Create a module, or update an existing one, with its output contracts. Contracts come from `--contracts` when given; otherwise from `module.yaml` in `--module-dir`. Without `--module-dir`, wfc looks only in `modules/<name>/`, so pass `--module-dir` for a module under `src/`. A `module.yaml` inside the repository is committed to git.

```bash
wfc register-module --name my_analysis --module-dir src/my_analysis
```

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-module
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc register-method

`wfc register-method <method-dir> --module <module>`, where `<method-dir>` is the method's directory and `<module>` is the registered module it joins.

```bash
wfc register-method src/my_analysis/filter_data --module my_analysis
```

Register a method from its directory: find the script, scan a Python script for its parameters, read `method.yaml`, check it against the module's contracts, copy the method into `methods/<name>/` (wfc's registered copy), and commit it to git. Registering the same name again updates the method: after you edit a method's script or `method.yaml`, run `wfc register-method` again.

The script is the one named by `--script`, else by the `script:` key in `method.yaml`, else the single `<name>.py`, `.R`, `.r` or `.sh` file in the directory. If the script cannot be found or the check fails, it prints an `ERROR:` line and registers nothing.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-method
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc register-sample

Register a data file or directory as a sample. wfc hashes the content, copies it into the project's DVC cache (the source stays where it is) and records it; the sample is pushed to the archive when one is reachable. Pipelines restore it into `data/samples/<name>/` when a run needs it. A sample name can be registered once.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-sample
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc restore-sample

Copy a registered sample from the cache into `data/samples/<name>/`, pulling it from the archive if it is not in the local cache. A file that is already present with the right content is left alone; a changed one is replaced. Pipelines run this for you before a step reads a sample.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: restore-sample
   :nodescription:
   :noepilog:
   :nodefault:
```

See [Registration](../how-to/registration.md) for walkthroughs of all four.


## Container Env Commands

Container environments are Docker images that methods run in. They are recorded in `.wfc/envs.json`, and a method names one with the `env:` key in its `method.yaml`.

### wfc register-env

`wfc register-env <name> [<spec>] [flags]`, where `<name>` is the name methods use for the env and `<spec>` (optional) is a local env to capture.

Build a Docker image for an environment, pin it by digest, and record it in `.wfc/envs.json`. Docker must be running (except with `--dry-run`). Choose one of three sources:

- **Capture a local env** (positional `spec`): wfc reads the package list of an env on your machine (a conda env's explicit list, or a pixi env's `pixi.lock` and `pixi.toml`, plus a `pip freeze`) and builds an image with the same packages, including anything you added with `pip install`. `conda:<env>` looks under `[conda] root`, or the conda base found by `conda info --base`. `pixi:<name>` and `pixi:<proj>:<env>` look under `[pixi] root`, then in the project's own `.pixi/envs/`. See [wf-canvas.toml](wf-canvas-toml.md).
- **Build from a file** (`--backend pixi|conda --from PATH`): build from a checked-in `pixi.lock` (with the `pixi.toml` next to it, if there is one) or a conda explicit list.
- **Use an existing image** (`--backend byo --image docker://...`): wfc pulls the image and records its digest without building anything.

The image does not need wfc installed. wfc runs the method script directly with the env's interpreter (see `--interpreter`).

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-env
   :nodescription:
   :noepilog:
   :nodefault:
```

**Examples:**

```bash
# Capture a local conda env named cell_pose
wfc register-env cell_pose conda:cell_pose

# Capture env "hello" of the pixi project "wcia"
wfc register-env analysis pixi:wcia:hello

# Build from a checked-in lock file
wfc register-env analysis --backend pixi --from envs/analysis/pixi.lock

# Use a pre-built image, with an explicit interpreter
wfc register-env vendor --backend byo --image docker://ghcr.io/org/img@sha256:... --interpreter /usr/bin/python3
```

To see what went into an image, open the Canvas **Registry → Envs** tab and expand the env: its **Packages** panel lists each `name==version` with its source (conda, pixi or pip). A `byo` image has no package list.

### wfc list-envs

Print a table of the registered envs with columns NAME, BACKEND, CONTAINER (the digest-pinned image) and BUILT AT. Takes no arguments.

### wfc show-env

`wfc show-env <name>`, where `<name>` is the env's name. Print one env's record: `name`, `backend`, `source`, `container`, `python` (the interpreter path), `env_fingerprint`, `source_fingerprint`, `built_from_lock` and `built_at`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: show-env
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc delete-env

`wfc delete-env <name> [--force]`, where `<name>` is the env's name. Remove an env from `.wfc/envs.json`. If methods use the env, wfc lists them first; they stay registered, so re-register them against another env. It asks for confirmation unless you pass `--force`. The Docker image itself is not deleted.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: delete-env
   :nodescription:
   :noepilog:
   :nodefault:
```

See [Registering an Environment](../tutorials/registering-an-environment.md) for a walkthrough.

## Dev-Loop Commands

These commands start a fresh container from a registered env's image, with the project mounted at `/work` as the working directory, the same layout a pipeline step runs in. Use them to try a method interactively in its real environment.

Each container is removed when the command ends. Anything you change inside it, including packages you `pip install`, does not carry into pipeline runs. To change an env's packages, update the env and run `wfc register-env` again with `--force`. To change a method, edit its script in your project, then run `wfc register-method` again.

### wfc jupyter

`wfc jupyter <env> [--port PORT]`, where `<env>` is the env's name. Start Jupyter Lab in the env's container. Open the `http://127.0.0.1:<port>/?token=...` URL that Jupyter prints.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: jupyter
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc shell

`wfc shell <env>`, where `<env>` is the env's name. Open an interactive shell (`bash`, or `sh` if the image has no bash) in the env's container.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: shell
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc exec

`wfc exec <env> <cmd...>`, where `<env>` is the env's name and `<cmd...>` is the command to run and its arguments. Everything after the env name is passed through as the command. Output can be piped or redirected, for example `wfc exec myenv cat file.txt > out.txt`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: exec
   :nodescription:
   :noepilog:
   :nodefault:
```


## Pipeline Commands

### wfc run-pipeline

Run a pipeline file from the command line. This is the same run the Canvas Run button starts. wfc generates a Snakefile from the pipeline, runs it with Snakemake, and then archives the outputs unless you pass `--no-archive`.

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

When every step succeeds, the command exits 0. When a step fails, it prints the pipeline summary and then one `ERROR: <message>` line naming the failure, and exits 1. If the pipeline cannot start (for example, a sample it reads cannot be found), it prints the `ERROR:` line with the fix and exits 1 without running anything.

## Cache and Export Commands

### wfc cache archive

Archive run outputs that are not archived yet: hash each file, copy it into the DVC cache, and record it. Use it after `wfc run-pipeline --no-archive`. It prints progress per file and saves each one as it finishes, so after an interruption, running it again archives only what is left.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cache archive
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc cache prune

Free disk space. By default, `wfc cache prune` removes run directories under `.runs/` that no recorded output needs. It lists what it will delete and asks before deleting.

Before deleting anything it checks that the archive is reachable, and stops if it is not. Runs with outputs that are not archived yet are skipped with a warning; run `wfc cache archive` first. With `--include-local`, entries whose content has not been pushed to the archive are kept.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cache prune
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc export

`wfc export <run-id> <slot> <dest>`, where `<run-id>` is the run to export from, `<slot>` is the output slot, and `<dest>` is where to write the copy. For example, `wfc export 412 masks results/masks.png`.

Copy a run's output out of the cache into a file you own, or print where it is (for example, `p=$(wfc export 412 masks --path)`). Cache files are read-only (see [Storage & Provenance](../explanation/storage-and-provenance.md)); the exported copy is writable. For a run that reused a cached result, the original run's output is exported.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: export
   :nodescription:
   :noepilog:
   :nodefault:
```

If any requested output cannot be exported, nothing is written and the command exits 1 with the reason.

## Environment Variables

None of these is required; each overrides a default.

| Variable | Effect |
|---|---|
| `WFC_PROJECT_ROOT` | The project directory to use instead of walking up from the current directory. It must contain `.wfc/wf-canvas.toml`. |
| `DATABASE_URL` | The database to read and write instead of the project's `.wfc/wfc.db`. `wfc demo` refuses to run when it names another project's database. |
| `WFC_GIT_COMMIT_TIMEOUT` | Seconds a `git commit` made by wfc may take before it is stopped (default `300`). Raise it if your git hooks are slow. Must be a positive number. |
