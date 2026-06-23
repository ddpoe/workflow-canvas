# CLI Reference

## Overview

All commands are invoked via `wfc <command>` (or `python -m wfc <command>`). The CLI is the primary developer interface to the core pipeline, covering project setup, registration, execution, and caching. (Lineage is queried separately via `python -m wfc.lineage --run-id <id>`, not a `wfc` subcommand.)

## Project Commands

### wfc init

Scaffold a new project directory structure (`.wfc/`, `modules/`, `methods/`, `.runs/`, `data/samples/`). Auto-initializes DVC when a `[dvc]` section is present in `wf-canvas.toml`.

| Arg | Description |
|---|---|
| `--dir` | Target directory (default: current directory) |
| `--git` | Initialize a git repository |

### wfc canvas

Launch the Canvas web UI -- a browser-based visual pipeline builder and run history viewer.

| Arg | Description |
|---|---|
| `--host` | Bind address (default: `127.0.0.1`) |
| `--port` | Port number (default: `8500`) |
| `--reload` | Enable auto-reload for development |
| `--project-root` | Override the project root for auto-load |

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
| `--backend` | Build backend: `pixi`, `conda`, `inherit`, or `byo`. Inferred from positional spec when present; required for `--from` and legacy modes. |
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

Live-env capture records the env's current state, including any ad-hoc `pip install` mutations on top of the conda/pixi env. Inspect the captured package list in the canvas env-detail panel (or via `GET /api/registry/envs/blob/<source_fingerprint>`) before relying on the image for downstream runs.

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
