<!-- generated from pm_mvp::docs.consumer.getting-started @ e58c2aa8dfe5; do not edit -->

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

- **Python 3.11+** — Required. The config parser uses `tomllib` from the standard library (added in 3.11).
- **Git** — Required. wfc auto-commits method source on registration and checks for dirty repositories before runs.
- **Snakemake 8+** — Required for pipeline execution. wfc generates Snakefiles that Snakemake runs.
- **Poetry** — For dependency management. Used to install wfc and its dependencies.
- **pixi** — For method environment isolation. Methods can declare a named shared pixi environment (e.g., `env: image-io` in `method.yaml`), which is resolved at registration time. If all your methods use `env: inherit`, pixi is not needed.
- **DVC** — Content-addressed storage for samples and outputs. DVC is a hard dependency and is installed automatically via `poetry install`. When a `[dvc]` section is present in `wf-canvas.toml`, wfc uses DVC's cache directory layout (`.dvc/cache/files/md5/`) for provenance storage. DVC is auto-initialized by `wfc init` if configured.

## Installation

```bash
# Clone the repository
git clone <repo-url> workflow-canvas
cd workflow-canvas

# Install dependencies (includes wfc CLI)
poetry install

# Verify the installation
wfc --help
```

You should see the wfc CLI help output listing available commands: `init`, `register-module`, `register-method`, `register-sample`, `run-pipeline`, and others.

## Your First Pipeline

This walkthrough covers the happy path from project initialization to pipeline execution.

### 1. Initialize a project

```bash
wfc init --dir ./my_project
cd my_project
```

This creates the project structure: `.wfc/` (config + database), `modules/`, `methods/`, `data/samples/`, `.runs/`, and a `.gitignore`. The `modules/` and `methods/` directories start empty -- you register your own modules and methods in the next steps. If you have a `[dvc]` section in `.wfc/wf-canvas.toml`, DVC is auto-initialized.

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

Methods are individual analysis scripts. Registration AST-scans the script for `@wfc_method` functions, parses `method.yaml` for contracts, validates environment resolution, checks outputs against module contracts, and git-commits the source:

```bash
# Nested method under a module:
wfc register-method modules/cell_analysis/preprocess --module cell_analysis

# Flat standalone method:
wfc register-method methods/aggregate --module my_module
```

### 4. Register a sample

Samples are your input data. Registration content-hashes the file and stores it in the DVC cache. The `data/samples/` directory is an ephemeral workspace -- files are restored lazily by Snakemake at execution time, not copied at registration:

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

This parses and validates the pipeline (cycle detection, slot wiring), generates a Snakefile, and executes via Snakemake. Each step: checks git state, checks cache (skip if HIT), runs if needed, archives output, and records the run in the database.

## Next Steps

Now that you have a working pipeline, explore further:

- **Project Anatomy** — Understand the directory structure, database, config file, and how modules, methods, and runs are organized.
- **Writing Methods** — Learn how to write `@wfc_method` scripts, declare contracts in `method.yaml`, and configure environment isolation.
