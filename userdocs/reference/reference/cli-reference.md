<!-- generated from pm_mvp::docs.consumer.reference.cli-reference @ d86a9bb184a3; do not edit -->

# CLI Reference

## Overview

All commands are invoked via `wfc <command>` (or `python -m wfc <command>`). The CLI is the primary developer interface to the core pipeline, covering project setup, registration, execution, and caching. (Lineage is queried separately via `python -m wfc.lineage --run-id <id>`, not a `wfc` subcommand.)

## Project Commands

### wfc init

Set up a new project so it is ready to run. `wfc init` is a guided wizard: it scaffolds the directory structure (`.wfc/`, `modules/`, `methods/`, `.runs/`, `data/samples/`), configures an output archive, initializes a local git repository with a clean first commit, and checks that Docker is available. It is **idempotent** — safe to re-run at any time. Each step is guarded by a "does this already exist?" check, so re-running only completes what is missing and never re-asks for or overwrites configuration you already have.

When it finishes, it prints a short health summary (the same checks as `wfc doctor`) so you can see at a glance whether the project is ready to run.

By default the wizard is interactive and prompts you (with sensible defaults you can accept by pressing Enter). Pass `--yes` to run it non-interactively for scripts and CI — every prompt takes its default.

What it sets up:

- **Output archive (always on).** Your run outputs are copied to a backup folder so results stay safe and reusable (this is the project's DVC archive). Only the *location* is prompted; the default is a durable folder outside the repo (`~/.wfc/archives/<project>`). Pass `--archive PATH` to set the location non-interactively.
- **Local git repository.** If the directory is not already a repo, `wfc init` runs `git init` **and makes an initial commit** of the scaffold. A real commit is required because pipeline runs need a clean commit to compute cache keys; starting from a committed, clean tree means your first run is not blocked. If git has no global identity, a repo-local identity is set automatically so the commit always lands (you can change it later with `git config`). This is a *local* repo only — no GitHub account, no network, and no login are needed; wfc never pushes.
- **Docker check.** Container execution is required to run anything, so the wizard checks that Docker is installed and its daemon is running, and reports the result. It cannot install Docker for you, but scaffolding still completes so no work is lost; the summary makes clear that runs are blocked until Docker is present.

The summary also prints one honest note about recoverability: the archive stores content-addressed blobs indexed by `.wfc/wfc.db`, and that database is **not** tracked in git — so to keep archived outputs recoverable you must back up the `.wfc/` directory.

| Arg | Description |
|---|---|
| `--dir` | Target directory (default: current directory) |
| `--archive PATH` | Output archive location (default: `~/.wfc/archives/<project>`) |
| `--git` | Accepted no-op alias for scripts and docs that pass it explicitly; git init is on by default |
| `--yes` | Run non-interactively, accepting every default (for scripts and CI) |

Recovery for any missing tool is uniform: install the tool, re-run `wfc init` (it completes only what is missing), then `wfc doctor` to confirm.

### wfc doctor

Check whether the project is ready to run, anytime. `wfc doctor` runs the same health checks the wizard runs at the end of `wfc init` and prints a health table:

- **git** — is git installed, is this a repo, does it have a commit, and is the working tree clean?
- **archive (DVC)** — is an archive location configured, and is it reachable?
- **Docker** — is `docker` on your PATH and is the daemon running?

Each check reports `ok`, `warn`, or `fail` with a short fix hint. `wfc doctor` **exits non-zero if any check fails**, so you can use it as a pre-run gate or a CI check ("is this project runnable?") before launching a pipeline.

| Arg | Description |
|---|---|
| *(none)* | Runs all checks against the current project |

### wfc canvas

Launch the Canvas web UI -- a browser-based visual pipeline builder and run history viewer.

| Arg | Description |
|---|---|
| `--host` | Bind address (default: `127.0.0.1`) |
| `--port` | Port number (default: `8500`) |
| `--reload` | Enable auto-reload for development |
| `--project-root` | Override the project root for auto-load |

For a walkthrough of setting up and running your first project, see [[getting-started]].

## Registration Commands

### wfc register-module

Create a module with input/output contracts. Contracts can be provided via `module.yaml` in the module directory or directly via CLI.

| Arg | Description |
|---|---|
| `--name` | Module name |
| `--module-dir` | Path to the module directory |
| `--contracts` | JSON contracts (optional if `module.yaml` exists) |
| `--description` | Module description |

### wfc register-method

Register a method: performs AST scan of the method script, reads `method.yaml`, validates against module contracts, and creates a git commit.

| Arg | Description |
|---|---|
| `method_dir` | Path to the method directory (positional) |
| `--module` | Parent module name |
| `--name` | Method name |
| `--script` | Script filename to scan |

### wfc register-sample

Import a data file into the project. Content-hashes the file via DVC and records the hash in the database. Requires a `[dvc]` section in `wf-canvas.toml`.

| Arg | Description |
|---|---|
| `--name` | Sample name |
| `--source` | Path to the source data file |

### wfc restore-sample

Restore a registered sample from the DVC cache to `data/samples/{name}/`. Verifies integrity automatically (skips if hash matches, replaces if mismatched).

| Arg | Description |
|---|---|
| `--name` | Sample name |
| `--hash` | Content hash to restore |

## Container Env Commands

Manage container environments used by methods and dev-loop commands. Container envs are registered in `.wfc/envs.json` and referenced by method `env:` fields.

### wfc register-env

Build and register a container env. Generates a Dockerfile for the chosen backend, runs `docker build` with BuildKit, resolves the image digest, and writes a manifest entry to `.wfc/envs.json`. Three input modes select where the build sources its package list from:

- **Positional typed-spec** — capture from a live local env. The CLI resolves the env, shells out for the package list (conda explicit list or pixi.lock + pixi.toml) plus a pip freeze, and stages the captured contents into the build context. Backend is inferred from the spec prefix; the captured package-list md5 is stored as `source_fingerprint` on the manifest record so the canvas can show what packages went into the image.
- **File-mode (`--from <path>`)** — copy a user-supplied source file into the build context under the generator's expected filename (`pixi.lock` for pixi, `explicit-list.txt` for conda). For pixi, an adjacent `pixi.toml` next to the lock is also staged when present. Requires `--backend pixi` or `--backend conda`.
- **Legacy (`--backend` alone)** — expects source files at the project root.

Modes are mutually exclusive; combining a positional typed-spec with `--backend` or `--from` errors before any docker subprocess fires. With `--dry-run`, writes the Dockerfile to `.wfc/build/<name>/Dockerfile` and exits (legacy mode only).

| Arg | Description |
|---|---|
| `name` | Env name (key in `.wfc/envs.json`) |
| `spec` | Optional typed env spec to capture from a live env: `conda:<env>`, `pixi:<name>`, or `pixi:<proj>:<env>`. Mutually exclusive with `--backend` and `--from`. |
| `--backend` | Build backend: `pixi`, `conda`, or `byo`. Inferred from positional spec when present; required for `--from` and legacy modes. |
| `--from PATH` | File-mode: copy this file into the build context under the generator's expected filename. Requires explicit `--backend pixi` or `--backend conda`. |
| `--image` | `docker://` image reference for `byo` backend |
| `--base-image` | Override the default base image for this env |
| `--dry-run` | Write Dockerfile only; do not invoke docker (legacy mode only) |
| `--force` | Overwrite an existing manifest entry for `name` |

**Examples:**

```bash
# Capture from a live conda env named cell_pose
wfc register-env cell_pose conda:cell_pose

# Capture from a pixi project's env
wfc register-env analysis pixi:wcia:hello

# File-mode: build from a checked-in lock file
wfc register-env analysis --backend pixi --from envs/analysis/pixi.lock

# BYO image — pull a pre-built image by digest
wfc register-env vendor --backend byo --image docker://ghcr.io/org/img@sha256:...
```

Live-env capture records the env's current state, including any ad-hoc `pip install` mutations on top of the conda/pixi env. To inspect what went into an image, open the canvas **Registry → Envs** tab and expand the env: its **Packages** panel lists the installed `name==version` packages, tagged by source (conda/pixi/pip). A `byo` image has no manifest to show, and an env registered before its packages were captured (or never rebuilt since) shows as not captured until you re-register it.

### wfc list-envs

Print a fixed-width table of all registered envs from `.wfc/envs.json`. Columns: NAME, BACKEND, CONTAINER (digest-pinned ref), BUILT AT.

No arguments.

### wfc show-env

Print the full record for a single registered env as key/value lines. Fields: name, backend, source, container, env_fingerprint, built_from_lock, built_at.

| Arg | Description |
|---|---|
| `name` | Env name (key in `.wfc/envs.json`) |

### wfc delete-env

Remove a container env from `.wfc/envs.json`. Warns and lists any methods that reference the env before prompting for confirmation. Methods are NOT auto-deleted; you must retarget them manually. The registry tag (if any) is not removed.

| Arg | Description |
|---|---|
| `name` | Env name to delete |
| `--force` | Skip the confirmation prompt (warn-on-reference listing still prints) |

### wfc exec-method

In-container entrypoint used by `wfc run-step` when dispatching to a container env. Executes a method script with the `WFC_*` environment variables already set by the outer `docker run -e` invocation. Does not call `pre_run`, `complete_run`, or touch the database — all run-state is owned by the outer host `wfc run-step`.

| Arg | Description |
|---|---|
| `--run-id` | Outer run ID (for error-message clarity) |
| `--node-id` | Pipeline node ID (for error-message clarity) |
| `--script` | Absolute path inside the container to the method script |

## Dev-Loop Commands

Interactive development commands that launch an ephemeral container of the env's digest-pinned image. The project directory is bind-mounted at `/work` inside the container, matching the exact layout `wfc run-step` uses, so methods run in production-parity conditions at dev time.

**Important:** Each container is spawned fresh per invocation. Changes made inside the container — including packages installed via `pip install` — do not persist into pipeline runs. The container exits when the command or session ends; nothing is committed back to the image.

**Cluster executor note:** `executor=slurm` is not supported for dev-loop commands in v1. Running any of these commands under a project with `[executor] type = "slurm"` in `wf-canvas.toml` will error clearly with an "out of scope for v1" message.

### wfc jupyter

Launch Jupyter Lab inside an ephemeral container of the env's image. The Jupyter server inside the container always binds port 8888; the host port is either the explicit `--port` value or autopicked from the range 8888–8999 (port 8000 is always skipped). The token-bearing URL (`http://127.0.0.1:<port>/?token=...`) is printed by Jupyter on startup.

| Arg | Description |
|---|---|
| `env` | Env name (key in `.wfc/envs.json`) |
| `--port` | Host port to forward to the container's 8888. Default: autopick the first free port in 8888–8999 (port 8000 always skipped) |

### wfc shell

Drop into an interactive shell inside an ephemeral container of the env's image. Tries `bash` first; falls back to `sh` on slim images that do not include bash. The project is bind-mounted at `/work`.

| Arg | Description |
|---|---|
| `env` | Env name (key in `.wfc/envs.json`) |

### wfc exec

Run an arbitrary command inside an ephemeral container of the env's image. The command is passed verbatim as the container argv. Uses `-i` (no TTY), so the command works correctly when its output is piped or redirected (e.g., `wfc exec myenv cat file.txt > out.txt`).

| Arg | Description |
|---|---|
| `env` | Env name (key in `.wfc/envs.json`) |
| `cmd...` | Command and arguments to run inside the container |

## Pipeline Commands

### wfc run-pipeline

Generate a Snakefile from a pipeline JSON and execute it via Snakemake. After the pipeline completes, outputs are auto-archived unless `--no-archive` is passed.

| Arg | Description |
|---|---|
| `--pipeline` | Path to the pipeline JSON file |
| `--cores` | Number of Snakemake cores (default: 4) |
| `--project-root` | Project root directory |
| `--wfc-root` | Directory added to PYTHONPATH for workers so they can `import wfc`. Defaults to wfc's installed location. |
| `--snakefile` | Where to write the generated Snakefile (default: `<project-root>/Snakefile`) |
| `--archive` / `--no-archive` | Enable or disable auto-archiving of outputs after completion |
| `--keep-going` | Pass `--keep-going` to Snakemake — keep running independent branches after a step fails, instead of stopping at the first failure |

**Running from CLI vs Canvas:** From the command line, use `wfc run-pipeline --pipeline path/to/pipeline.json`. From Canvas, click the Run button in the Builder toolbar -- this calls the same underlying pipeline execution through the web API (`POST /api/workflow/run`).

## Cache Commands

### wfc cache archive

Hash and cache all un-archived run outputs (those with `content_hash = NULL`) into the DVC cache. Shows per-file progress. Use this after running a pipeline with `--no-archive`, or to archive a specific run.

| Arg | Description |
|---|---|
| `--run-id` | Optional: limit archiving to a specific run |

### wfc cache prune

Remove old run archives and optionally DVC local cache entries to reclaim disk space. Before pruning, verifies the DVC remote is reachable (skipped with `--force`). Runs with un-archived outputs are skipped with a warning.

| Arg | Description |
|---|---|
| `--all` | Remove all archives regardless of reference status |
| `--include-local` | Also prune `.dvc/cache/` unreferenced hashes |
| `--dry-run` | Print what would be deleted without deleting |
| `--force` | Skip the confirmation prompt and remote reachability check |
