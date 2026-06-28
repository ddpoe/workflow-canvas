<!-- generated from pm_mvp::docs.consumer.tutorials.getting-started @ b6e4774746f7; do not edit -->

# Getting Started with Workflow Canvas

## What is Workflow Canvas?

Workflow Canvas is a reproducible computational pipeline system built around **wfc** (Workflow Canvas CLI), its CLI. It solves the problem of managing multi-step analysis pipelines where you need:

- **Contracts** — Methods declare their inputs, outputs, and parameters via `method.yaml`. Modules enforce required outputs. The system catches wiring errors and missing outputs before you waste time on a long run.
- **Caching** — Each step is fingerprinted by its source code, parameters, and upstream inputs. Unchanged steps are skipped automatically. The system refuses to run on uncommitted code (`DirtyRepositoryError`) to ensure reproducibility.
- **Lineage** — Every run is recorded in a SQLite database. You can trace any output back through its full DAG ancestry, including cache hits which appear as audit rows in the lineage.
- **Snakemake execution** — Pipelines are defined as JSON and compiled to Snakefiles. Snakemake handles parallelism and dependency resolution.
- **Canvas UI** — A visual interface for building and inspecting pipelines (separate feature).

wfc manages the full lifecycle: register your modules, methods, and data samples, define a pipeline, run it, and query lineage — all from the command line.

## Prerequisites

Before you begin, make sure you have:

- **Python 3.11+** — Required. The config parser uses `tomllib` from the standard library (added in 3.11). Installing wfc also gives you `pip`, which ships with Python.
- **Docker** — Required. Methods always run inside a container, so a working Docker installation is a hard requirement: nothing runs without it. On Windows and macOS this means Docker Desktop; on Linux, the Docker Engine and its daemon. `wfc init` pre-flights Docker for you and `wfc doctor` checks it any time, but neither can install it — that part is on you.
- **Git** — Required, but only *locally*. wfc records a commit for every run so results are reproducible, and it refuses to run on uncommitted code. All you need is the `git` command and a local identity (a name and email); `wfc init` even sets a repo-local fallback identity if you have none configured. There is **no GitHub account, no login, and no network involved** — wfc never pushes anywhere. The git requirement is purely about a clean local history.

A few things you do **not** need to install separately:

- **DVC** ships with wfc — it is a dependency, installed automatically when you `pip install workflow-canvas`. wfc uses it as the content-addressed store for your run outputs and registered samples. `wfc init` configures a local archive for you; you never have to set DVC up by hand.
- **Snakemake** also ships with wfc and runs your pipelines under the hood — wfc generates the Snakefile and invokes it for you.

Container environments are built ahead of time from a backend such as a base image, pixi, or conda, but those tools belong to the environment-build step, not to running wfc itself. See [[registering-an-environment]] for how environments are built and named.


## Installation

```bash
# Install Workflow Canvas (includes the wfc CLI, plus DVC and Snakemake)
pip install workflow-canvas

# Verify the installation
wfc --help
```

You should see the wfc CLI help output listing available commands, including `init`, `doctor`, `register-module`, `register-method`, `register-sample`, `register-env`, and `run-pipeline`.


## Your First Pipeline

This walkthrough covers the happy path from project initialization to pipeline execution.

### 1. Initialize a project

```bash
wfc init --dir ./my_project
cd my_project
```

`wfc init` is a guided setup wizard that leaves you with a project that can actually run. Run it with no extra flags and it walks you through setup interactively; the goal is that when it finishes you can register a method and run a pipeline without any further hand-configuration. It does four things:

- **Scaffolds the project structure** — `.wfc/` (config + database), `modules/`, `methods/`, `data/samples/`, `.runs/`, and a `.gitignore`. The `modules/` and `methods/` directories start empty; you register your own in the next steps.
- **Configures a backup archive for your outputs.** Every project gets one — the wizard only asks *where* it should live, with a sensible default you can accept by pressing Enter (`~/.wfc/archives/<project>`, kept outside the repo so it survives). This is wired up as a live DVC archive; you do not configure DVC yourself.
- **Sets up git.** If the directory is not already a git repository, the wizard runs `git init` and makes a clean initial commit of the scaffold. That gives the run-gate a real starting commit and a clean tree, so your first run is not blocked by a missing `HEAD` or a "dirty repository" error. If you have no git identity configured, it sets a repo-local one for you so the commit always lands — you can change it later with `git config`.
- **Pre-flights Docker** and prints a health summary at the end, so you immediately know whether your project is ready to run or what is still missing.

The wizard is **idempotent**: it is always safe to re-run. It never re-asks questions you have already answered and never clobbers existing config — each step checks "does this already exist?" first. That makes recovery simple: if a tool was missing, install it, re-run `wfc init` to finish only what is left, and run `wfc doctor` to confirm.

For scripts and CI, run it non-interactively:

```bash
wfc init --dir ./my_project --yes              # accept all defaults, no prompts
wfc init --dir ./my_project --archive /data/archives/my_project --yes
```

`--yes` accepts every default (including the git-identity fallback), and `--archive PATH` sets the output archive location without prompting.

> **One door for "why won't this run?"** If a run ever refuses to start, run `wfc doctor`. It checks git, the output archive, and Docker, prints a health table, and exits non-zero if anything is broken — handy both at your terminal and as a CI gate. When a run is blocked, wfc points you at `wfc doctor` rather than dumping a raw error.

> **About the archive.** The archive stores your outputs as content-addressed blobs, indexed by the database in `.wfc/` (which is deliberately *not* tracked in git). To keep archived outputs recoverable, back up your `.wfc/` directory along with the archive folder.

> **Tip:** Run `wfc seed` to populate the project with demo modules, methods, and sample data for experimentation.

### 2. Register a module

Modules group related methods under a domain name with output contracts. You can define contracts in a `module.yaml` file or pass them via CLI:

```bash
# From module.yaml (recommended):
wfc register-module --name cell_analysis --module-dir modules/cell_analysis

# Or from CLI JSON:
wfc register-module --name cell_analysis \
  --contracts '[{"type": "output", "name": "result", "value_type": ".csv", "required": true}]'
```

### 3. Register a method

Methods are individual analysis scripts. Registration AST-scans the script for public functions, parses `method.yaml` for contracts, validates environment resolution, checks outputs against module contracts, and git-commits the source:

```bash
# Nested method under a module:
wfc register-method modules/cell_analysis/preprocess --module cell_analysis

# Flat standalone method:
wfc register-method methods/aggregate --module my_module
```

A method declares the container environment it runs in (via `env:` in its `method.yaml`), and that environment must be built and registered with `wfc register-env` before the method can run. See [[registering-an-environment]] for the details.

> **Two Pythons, by design.** The wfc engine runs in its own environment on the host machine; your method runs in its own container environment, which contains only your declared dependencies (and, optionally, the pure-stdlib `wfc-client` package). wfc never imports your method's code and your method never imports the wfc engine — they communicate only through `WFC_*` environment variables and files in the run directory. Because that contract is plain env vars and files, any recorded run can be reproduced later regardless of which client version (or none) the method was authored with.

### 4. Register a sample

Samples are your input data. Registration content-hashes the file and stores it in the DVC cache. The `data/samples/` directory is an ephemeral workspace — files are restored lazily by Snakemake at execution time, not copied at registration:

```bash
wfc register-sample --name CFPAC_ERKi --source /data/raw/cfpac_erki.csv
```

### 5. Define and run a pipeline

Pipelines are JSON files with `nodes`, `links`, and `samples`. Each node references a registered method; links wire outputs to named input slots:

```json
{
  "nodes": [
    {"id": "filter_ctrl", "method": "csv_filter", "module": "csv_tools",
     "params": {"column": "condition", "values": ["control"]}}
  ],
  "links": [
    {"source": "filter_ctrl", "target": "analyze", "target_slot": "data"}
  ],
  "samples": ["CFPAC_ERKi"]
}
```

Run it:

```bash
wfc run-pipeline --pipeline pipeline.json --cores 4
```

This parses and validates the pipeline (cycle detection, slot wiring), generates a Snakefile, and executes via Snakemake. Each step checks git state, checks the cache (skipping the step on a hit), runs in its container if needed, archives output, and records the run in the database. If a run won't start, `wfc doctor` will tell you why.


## Next Steps

Now that you have a working pipeline, explore further:

- **[[registering-an-environment]]** — Build and register the container environment your methods run in. Every method needs one, and `wfc doctor` checks that Docker is ready for it.
- **[[authoring-a-method-script]]** — Write methods with the `wfc-client` decorator (recommended) or the canonical `WFC_*` env-var + file contract, and declare contracts in `method.yaml`.
- **[[writing-contracts]]** — Declare and enforce the inputs and outputs that wire your pipeline together correctly.
- **[[project-anatomy]]** — Understand the directory structure, the database, the config file, and how modules, methods, and runs are organized.
- **[[canvas]]** — Build and inspect pipelines visually instead of by hand-editing JSON.
- **[[run-and-inspect-results]]** — Find a run's outputs and trace its lineage after it completes.

