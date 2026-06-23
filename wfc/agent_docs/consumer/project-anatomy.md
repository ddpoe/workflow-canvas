# Project Anatomy

## Directory Structure

After `wfc init`, your project has the following layout:

```
my_project/
  .wfc/
    wf-canvas.toml        # Project config (DB path, pixi root, DVC settings)
    wfc.db                # SQLite database — single source of truth
  modules/                # Empty after init; populated by register-module / register-method
  methods/                # Empty after init; populated by register-method (flat standalone)
  data/
    samples/              # Ephemeral workspace for restored sample files
                          #   DVC-managed; lazily restored at execution time
  .runs/                  # Sentinels + transient staging (bytes live in .dvc/cache)
    sentinels/            #   zero-byte Snakemake DAG-wiring sentinels
    {run_id}/             #   transient staging; archive pass moves bytes to DVC cache
  .dvc/                   # DVC cache (when [dvc] configured)
    cache/files/md5/      #   content-addressed storage ({hash[:2]}/{hash[2:]})
  .gitignore              # Pre-configured for wfc artifacts
```

After registering modules and methods, the tree fills in:

```
modules/
  cell_analysis/          # Created by register-module
    module.yaml           #   module-level output contracts
    preprocess/           # Created by register-method
      preprocess.py
      method.yaml
methods/
  aggregate/              # Created by register-method (flat standalone)
    aggregate.py
    method.yaml
```

Key points:
- **`modules/`** holds nested module/method hierarchies. Each module directory can contain a `module.yaml` for output contracts and subdirectories for each method.
- **`methods/`** holds flat standalone methods that belong to a module but live outside the module directory.
- **`data/samples/`** is NOT a permanent storage location. Files are restored here lazily by the Snakemake `restore_sample` rule at pipeline execution time. The actual data lives in the DVC cache.
- **`.runs/sentinels/`** holds zero-byte Snakemake DAG-wiring sentinels (no output bytes). **`.runs/{run_id}/`** is a transient *staging* directory — methods write outputs there, then the archive pass moves them into the DVC content-addressed cache (`.dvc/cache/files/md5/`), which is the sole permanent store. There is no `.runs/workspace/` tree.

## The Database

`.wfc/wfc.db` is a SQLite database and the single source of truth for all pipeline state. It contains 11 tables (via SQLModel) covering:

- **Registration** — `modules`, `methods`, `method_contracts`, `module_contracts`, `tracked_functions`, `param_defs`, `samples`
- **Execution** — `runs`, `run_inputs`, `run_outputs`
- **Versioning** — `method_versions`

You do not interact with the database directly. The wfc CLI manages all reads and writes. Any system that reads pipeline data (canvas UI, lineage queries, reports) consumes this schema.

Every run is recorded here with its method, parameters, sample, status, cache key, and metrics. Lineage is traced through `run_inputs.source_run_id` relationships. Cache hits create audit rows (`cache_source_run_id` set) that appear in lineage like normal runs.

## Configuration: wf-canvas.toml

The project config lives at `.wfc/wf-canvas.toml` and is parsed with `tomllib` (Python 3.11+). Sections:

```toml
[database]
url = "sqlite:///.wfc/wfc.db"

[project]
name = "my-project"

[pixi]
root = ".pixi"   # Relative to project root; absolute paths also accepted

[conda]                        # Optional — named conda env resolution
root = "/path/to/conda/envs"

[dvc]                          # Optional — enables DVC provenance
url = "file:///path/to/storage"  # Any DVC-native scheme: file://, s3://, ssh://, gs://, azure://, …
auto_init = true               # Auto-run dvc init if .dvc/ missing (default true)
```

The `read_config()` function returns a dict with keys: `database_url`, `project_name`, `pixi_root`, `conda_root` (empty string when `[conda]` absent), and `dvc` (dict with `url`, `auto_init`, plus legacy `remote_type`/`remote_path` fallbacks when present; otherwise `None`).

- **`[pixi]`** — Defines the environment root directory. Methods that declare a named env (e.g., `env: image-io`) are resolved via glob at `{pixi_root}/{env_name}-*/envs/default/`.
- **`[conda]`** — Optional alternative for named conda environment resolution.
- **`[dvc]`** — When present, `wfc init` mirrors this block to `.dvc/config` and DVC dispatches on the `url` scheme. Any DVC-native backend is supported (local filesystem, S3, SSH, GCS, Azure). The legacy `remote_type` field is no longer used.

## Modules vs. Methods

**Modules** are organizational containers that group related methods under a domain name (e.g., `cell_analysis`, `csv_tools`). Modules define output contracts — required outputs and metrics that every method in the module must produce. Contracts come from `module.yaml` or the CLI `--contracts` flag.

**Methods** are individual analysis scripts. Each method has:
- A Python script (`{method_name}.py`) with `@wfc_method` decorated functions
- A `method.yaml` declaring input slots, output slots, parameters, and optionally an environment name
- A parent module that it belongs to

### Nested vs. Flat

Methods can be organized two ways:

- **Nested** under `modules/` — the method directory lives inside its module directory: `modules/cell_analysis/preprocess/preprocess.py`. Register with `wfc register-method modules/cell_analysis/preprocess --module cell_analysis`.
- **Flat** under `methods/` — the method lives in a standalone directory: `methods/aggregate/aggregate.py`. Register with `wfc register-method methods/aggregate --module csv_tools`.

Both layouts are functionally equivalent. The module association is set by the `--module` flag, not by directory location.

### Registration validations

When you register a method, wfc:
1. AST-scans the script (no import, no side effects) for `@wfc_method` functions and parameters
2. Parses `method.yaml` for contracts and env name
3. Validates at least one input slot is declared
4. If env is named (not `inherit`): validates the python executable exists via pixi/conda root glob
5. Validates method outputs satisfy the module's required output contracts
6. Git-commits the method directory

## Run Workspace and Caching

### How `.runs/` works

The `.runs/` directory serves two purposes:

- **`sentinels/`** — Zero-byte sentinel files used only for Snakemake DAG wiring, organized by `{pipeline_id}/{node_id}/{sample}/{variant}/.complete`. They mark a step as finished; they hold no output bytes.
- **`{run_id}/`** — A transient *staging* directory. A method writes its outputs here, then the archive pass moves the bytes into the DVC content-addressed cache (`.dvc/cache/files/md5/`), which is the sole authoritative store. The former `.runs/workspace/` output tree no longer exists; outputs are reached by content-hash, not by a workspace path.

### Cache key computation

Every step's cacheability is determined by a composite key:

```
cache_key = SHA256(
    code_fingerprint                     # SHA256 of method source .py files (sorted by path)
    + json.dumps(params, sort_keys=True) # Deterministic param serialization
    + input_fingerprint                  # SHA256 of upstream cache keys (cache key chaining)
    + env_fingerprint                    # 32-char MD5 of the resolved environment (pixi.lock / conda list / interpreter identity, plus pip freeze)
)
```

- **Code fingerprint** — SHA256 of all `.py` files in the method's registered snapshot directory (`methods/{method_name}/`), sorted by relative path for cross-platform determinism. Based on file content, not git commit — so unrelated commits to other methods do not invalidate the cache.
- **Input fingerprint** — Uses upstream `Run.cache_key` values (cache key chaining). For root nodes with no upstream runs, the fingerprint covers the registered sample identity (`path:size:mtime`). Legacy runs without a cache key use a sentinel value.
- **Environment fingerprint** — A 32-char MD5 of the method's resolved environment (its `pixi.lock` section / conda package list / interpreter identity, plus `pip freeze`). Changing a method's environment invalidates its cache; it is captured per run as `Run.env_fingerprint`.
- The `git_commit` is captured as **audit-only metadata** on the `MethodVersion` row. It is not part of the cache key.

### Pre-run sequence

Before each step executes:
1. Check for uncommitted tracked changes (`DirtyRepositoryError` if dirty)
2. Compute code fingerprint from registered source copy
3. Compute input fingerprint from upstream cache keys
4. Build cache key
5. Look up existing completed run with matching cache key
6. **Cache HIT** — insert an audit run row (with `cache_source_run_id` set), restore output from DVC cache
7. **Cache MISS** — insert a new run row and execute the method

### DVC provenance storage

The `wfc.provenance` module provides content-addressed storage using DVC's cache directory layout (`.dvc/cache/files/md5/{hash[:2]}/{hash[2:]}`). The local hash/move/restore path manipulates the cache directory directly — no DVC import is needed and no `.dvc` pointer files are created; wfc's SQLite database holds the `content_hash` references. The remote push/pull path, however, uses DVC's Python API (`DataCloud.push`/`pull`, via the `wfc.remote` module), so a single code path supports any DVC-native backend (local filesystem, S3, SSH, GCS, Azure).

Key operations:
- **`archive_outputs`** — Hashes and caches all un-archived run outputs, updating DB rows with computed MD5 hashes
- **`push_cache` / `pull_cache`** — Sync cache objects to/from the configured remote (any DVC-native backend) for cross-machine sharing
- **`wfc cache prune`** — Remove unreferenced archive directories and DVC cache entries

## Next Steps

With an understanding of the project structure, continue to:

- **Writing Methods** — How to write `@wfc_method` scripts, declare contracts in `method.yaml`, configure environment isolation, and test methods in isolation with `wfc run-step`.
- **Registration** — Deep dive into module and method registration, contract validation, and environment resolution.
