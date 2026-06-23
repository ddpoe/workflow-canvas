# Workflow Canvas
A decorator-and-database approach to reproducible computational pipelines.

Workflow Canvas tracks methods, contracts, data lineage, and caching in a local SQLite database, then generates Snakefiles so [Snakemake](https://snakemake.readthedocs.io/) handles execution, parallelism, and environment isolation.

## Key Features

| Feature | What it does |
|---|---|
| **Core Pipeline** | Register methods & samples, define pipelines as JSON, generate & execute Snakefiles |
| **↳ Contracts** | Two-tier validation (module + method) via `method.yaml` — errors caught before execution |
| **↳ Caching** | Git-commit discipline, SHA256 cache keys, automatic skip of unchanged steps |
| **↳ Lineage** | Recursive ancestry tracing through the full run DAG |
| **Canvas** | Browser-based visual pipeline builder + run history (FastAPI + LiteGraph.js) |
| **Report Compositor** | Multi-panel figure rendering from pipeline data *(design spec complete, implementation planned)* |

## Quick Start

```bash
# 1. Initialize a project
workflow-canvas init my_project
cd my_project

# 2. Register a module and method
workflow-canvas register-module my_module methods/my_module
workflow-canvas register-method my_module my_method

# 3. Register sample data
workflow-canvas register-sample sample_01 data/samples/sample_01.csv

# 4. Define a pipeline (pipeline.json) and run it
workflow-canvas run-pipeline pipeline.json
```

## Project Structure

```
my_project/
  .wfc/                  ← SQLite database (wfc.db)
  .runs/                ← Archived outputs + workspace hardlinks
  methods/
    my_module/
      my_method/
        run.py          ← Method implementation
        method.yaml     ← Contract (inputs, outputs, params)
        environment.yml ← Conda environment
  data/samples/         ← Registered sample files
  pipeline.json         ← Pipeline definition
```

## Requirements

- Python 3.11+
- Git
- Snakemake 8+
- SQLite (bundled with Python)