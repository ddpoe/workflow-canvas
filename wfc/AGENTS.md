# wfc — Agent Onboarding

You are helping a user build or debug a computational pipeline with **wfc** (the CLI of the `workflow-canvas` package). Read this file before touching their project. It is the minimum you need to be useful; deeper docs are listed at the bottom.

## What wfc is

wfc is a reproducible pipeline manager. It tracks **methods**, **contracts**, **samples**, **runs**, and **lineage** in a local SQLite DB (`.wfc/wfc.db`), then generates **Snakefiles** so Snakemake handles execution.

Mental model:
```
user code (methods/)  →  wfc register-*  →  SQLite DB  →  wfc run-pipeline
                                             ↓
                                      generated Snakefile
                                             ↓
                                         Snakemake
                                             ↓
                                   .runs/{id}/ + DVC cache
```

Three things wfc guarantees:
- **Contracts** — methods declare I/O and params in `method.yaml`; modules declare required outputs in `module.yaml`. Mis-wired pipelines fail before execution.
- **Caching** — steps are fingerprinted by `sha256(code + params + upstream_cache_keys)`. Unchanged steps are skipped. wfc **refuses to run on a dirty git repo** (`DirtyRepositoryError`).
- **Lineage** — every run is recorded; `get_lineage(run_id)` walks the full DAG via recursive CTE. Cache hits are recorded as audit rows, so lineage is always complete.

## Project layout

```
my_project/
  .wfc/wf-canvas.toml      # config (db url, pixi root, [dvc])
  .wfc/wfc.db               # the SQLite DB — single source of truth
  modules/{mod}/{method}/ # nested methods (typical)
    {method}.py           #   @wfc_method script (must match dir name)
    method.yaml           #   contract
  methods/{method}/       # flat standalone methods (also valid)
  data/samples/           # EPHEMERAL — files restored lazily from DVC cache
  .runs/workspace/        # active pipeline outputs (wiped next run)
  .runs/{run_id}/         # permanent archived outputs
  .dvc/cache/files/md5/   # content-addressed storage
```

Git is **required**. `wfc register-method` auto-commits the method directory.

## The core pattern: @wfc_method

Every method is a Python file with at least one `@wfc_method` function and a `wfc_method_main()` entrypoint.

```python
import pandas as pd
from wfc.method import wfc_method, wfc_method_main

@wfc_method
def filter_data(inputs, params):
    # inputs: dict[str, list[Path]]  (keys = input slot names in method.yaml)
    # params: dict (from method.yaml defaults + pipeline overrides)
    df = pd.read_csv(inputs["data"][0])
    return df[df["score"] > params["threshold"]]

if __name__ == "__main__":
    wfc_method_main()
```

**Return-value normalization:**

| Return | Interpretation |
|---|---|
| `DataFrame` | single output named `"output"` |
| `(DataFrame, dict)` | `"output"` + metrics |
| `dict` | named outputs (keys must match `outputs:` in `method.yaml`) |
| `(dict, dict)` | named outputs + metrics |
| `None` | no outputs |

Returning an output name not declared in `method.yaml` raises `ContractViolation`.

## method.yaml (the method contract)

```yaml
inputs:
  data:
    type: csv          # csv | parquet | pickle | png | directory | model | any
    required: true
    multiple: false    # true = fan-in, accepts list of files
    columns:           # optional — hard gate for csv/parquet
      strict: [cell_index, condition]
      patterns: ["*_intensity"]

outputs:
  predictions:
    type: csv
  model:
    type: model

params:
  threshold:
    type: float        # str | int | float | bool | list | dict
    default: 0.5
    required: false

executor: python
env: inherit           # inherit | pixi:<name> | pixi:<proj>:<env> | conda:<name>
```

Bare env names (e.g. `image-io` without `pixi:` prefix) are **rejected at registration**.

## module.yaml (the module contract)

```yaml
description: "Binary classification of cell data"
contracts:
  - type: output       # output | metric
    name: model
    value_type: model
    required: true
  - type: metric
    name: mcc
    value_type: float
    required: true
```

Every method in the module must declare these required outputs, or `register-method` fails.

## Pipeline JSON

```json
{
  "nodes": [
    {"id": "filt", "method": "csv_filter", "module": "csv_tools",
     "params": {"column": "condition", "values": ["control"]}}
  ],
  "links": [
    {"source": "filt", "target": "analyze", "target_slot": "data"}
  ],
  "samples": ["CFPAC_ERKi"]
}
```

**Roots must be system nodes**, not method nodes. System node types:
- `input_selector` — picks a registered sample
- `run_reference` — picks an output from a previous completed run

A method node with no upstream edge is rejected at validate/load time.

## CLI commands the agent will use most

| Command | What it does |
|---|---|
| `wfc init --dir ./proj` | Scaffold project (`.wfc/`, `modules/`, `methods/`, etc.) |
| `wfc register-module --name M --module-dir modules/M` | Create module from `module.yaml` |
| `wfc register-method modules/M/method --module M` | AST-scan, validate, git-commit, snapshot source |
| `wfc register-sample --name S --source /path/to.csv` | DVC-hash + store |
| `wfc run-pipeline --pipeline p.json --cores 4` | Generate Snakefile + run |
| `wfc canvas` | Launch browser UI (port 8500) |
| `wfc cache archive [--run-id ID]` | Hash + cache un-archived outputs |
| `wfc cache prune [--dry-run]` | Reclaim disk |
| `wfc seed` | Populate demo modules + samples (good for exploration) |

`python -m wfc <cmd>` works identically.

## Rules for the agent

1. **Don't skip `method.yaml`.** Missing contracts = no slot metadata, canvas widgets break, cross-step column checks disabled. Always write one.
2. **Don't import `wfc` inside a method script at module scope beyond `from wfc.method import wfc_method, wfc_method_main`.** AST-scan reads signatures; heavy imports slow registration.
3. **Don't write to `data/samples/` directly** — it's ephemeral. Use `wfc register-sample`. The DVC cache is authoritative.
4. **Don't tell the user to commit manually before `register-method`** — registration auto-commits. But `run-pipeline` requires a **clean** repo; uncommitted tracked changes raise `DirtyRepositoryError`.
5. **Snakefiles are generated, not hand-edited.** If execution is broken, regenerate via `run-pipeline`, don't patch the Snakefile.
6. **Method script filename must match the directory name.** `methods/foo/foo.py`, not `methods/foo/run.py`.
7. **Every method needs ≥1 input slot.** Root-like methods take input from a system node (Input Selector), not from nothing.
8. **Cache key is code + params + upstream keys.** Changing an unrelated method's code does NOT invalidate this method's cache. Changing *this* method's source does.

## Where to look for more

Everything authoritative lives in this package's source. Read these files directly:

- `method.py` — `@wfc_method`, `wfc_method_main`, `RunContext` API, return-value parsing
- `register.py` — registration logic (AST scan, contract validation, git commit)
- `snakemake_gen.py` — **the only** pipeline execution path (`generate_snakefile`)
- `lineage.py` — `get_lineage` recursive CTE
- `contracts.py` — contract parsing + validation, column checks
- `models.py` — SQLModel table definitions (the full DB schema)
- `cli.py` — CLI dispatch and argument parsing
- `provenance.py` — DVC cache manipulation (archive/restore/push/pull)
- `database.py` — engine + session setup
- `wfc_context.py` — `wf-canvas.toml` parsing
- `canvas/` — FastAPI backend + Svelte frontend for the web UI

`wfc --help` and `wfc <subcommand> --help` are authoritative for CLI flags. For DB schema questions, grep `models.py`. For "how does a pipeline actually run", trace `run_pipeline` in `cli.py` → `generate_snakefile` in `snakemake_gen.py`.
