<!-- generated from pm_mvp::docs.consumer.explanation.project-anatomy @ 34363c5fad11; do not edit -->

# Project Anatomy

## Directory Structure

After `wfc init`, your project has the following layout:

```
my_project/
  .wfc/
    wf-canvas.toml        # Project config (DB path, pixi root, DVC archive URL)
    wfc.db                # SQLite database — single source of truth
    envs.json             # Registered container environments (git-tracked env manifest)
  modules/                # Empty after init; populated by register-module / register-method
  methods/                # Empty after init; populated by register-method (flat standalone)
  data/
    samples/              # Ephemeral workspace for restored sample files
                          #   DVC-managed; lazily restored at execution time
  .runs/                  # Sentinels + transient staging (bytes live in the DVC archive)
    sentinels/            #   zero-byte Snakemake DAG-wiring sentinels
    {run_id}/             #   transient staging; archive pass moves bytes to DVC archive
  .dvc/                   # DVC working directory (always present after init)
    cache/files/md5/      #   local content-addressed cache
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
- **`data/samples/`** is NOT a permanent storage location. Files are restored here lazily by the Snakemake `restore_sample` rule at pipeline execution time. The actual data lives in the DVC archive.
- **`.runs/sentinels/`** holds zero-byte Snakemake DAG-wiring sentinels (no output bytes). **`.runs/{run_id}/`** is a transient *staging* directory — methods write outputs there, then the archive pass moves them to the DVC archive, which is the sole permanent store. There is no `.runs/workspace/` tree.
- **`.dvc/`** is always created by `wfc init`. It holds the local DVC cache. The long-term archive lives at the `url` configured in `wf-canvas.toml` (default: `~/.wfc/archives/<project>`), which is outside the repo so outputs survive repo re-creation.
- **`.wfc/envs.json` IS tracked in git.** It is the manifest of your registered container environments — each env's backend, build spec, and resolved image content digest. Committing it makes your environments part of the project's reproducible record (a lockfile for environments): a collaborator who checks out the repo gets the exact image digests your methods were validated against. See [Registering an Environment](../tutorials/registering-an-environment.md).
- **`.wfc/wfc.db` is NOT tracked in git** (listed in `.gitignore`). It is the index that maps every output to its content hash. Back up the `.wfc/` directory along with your archive folder to keep results recoverable.

## The Database

`.wfc/wfc.db` is a SQLite database and the single source of truth for all pipeline state. It contains 11 tables (via SQLModel) covering:

- **Registration** — `modules`, `methods`, `method_contracts`, `module_contracts`, `tracked_functions`, `param_defs`, `samples`
- **Execution** — `runs`, `run_inputs`, `run_outputs`
- **Versioning** — `method_versions`

You do not interact with the database directly. The wfc CLI manages all reads and writes. Any system that reads pipeline data (canvas UI, lineage queries, reports) consumes this schema.

Every run is recorded here with its method, parameters, sample, status, cache key, and metrics. Lineage is traced through `run_inputs.source_run_id` relationships. Cache hits create audit rows (`cache_source_run_id` set) that appear in lineage like normal runs.

The database is the provenance record: it is the index that maps each output to the content hash holding its bytes. Because it is state rather than source, `wfc init` lists `.wfc/wfc.db` in `.gitignore` — git versions your method *code*, not your run history. That also means the database is part of what you must back up to keep results recoverable; see [Storage & Provenance](../explanation/storage-and-provenance.md).

## Configuration

Project settings live in `.wfc/wf-canvas.toml` — the database connection, the environment root, a `[dvc]` block (always written by `wfc init`; only the archive URL location is configurable), and an optional registry block. This file is committed to git as project source. For the full field-by-field reference, see <a href="../reference/reference/wf-canvas-toml.html">wf-canvas.toml</a>.

## Modules vs. Methods

**Modules** are organizational containers that group related methods under a domain name (e.g., `cell_analysis`, `csv_tools`). Modules define output contracts — required outputs and metrics that every method in the module must produce. Contracts come from `module.yaml` or the CLI `--contracts` flag.

**Methods** are individual analysis scripts. Each method has:
- A Python script (`{method_name}.py`) — the analysis implementation (plain script or using `wfc-client` sugar)
- A `method.yaml` declaring input slots, output slots, parameters, and the container environment it runs in
- A parent module that it belongs to

### Nested vs. Flat

Methods can be organized two ways:

- **Nested** under `modules/` — the method directory lives inside its module directory: `modules/cell_analysis/preprocess/preprocess.py`. Register with `wfc register-method modules/cell_analysis/preprocess --module cell_analysis`.
- **Flat** under `methods/` — the method lives in a standalone directory: `methods/aggregate/aggregate.py`. Register with `wfc register-method methods/aggregate --module csv_tools`.

Both layouts are functionally equivalent. The module association is set by the `--module` flag, not by directory location.

### Registration validations

When you register a method, wfc:
1. AST-scans the script (no import, no side effects) for `@wfc.method`-decorated functions and `save_artifact` calls
2. Parses `method.yaml` for contracts and the env name
3. Validates at least one input slot is declared
4. Validates the named container environment has already been built
5. Validates method outputs satisfy the module's required output contracts
6. Git-commits the method directory

For the full walkthrough of authoring a method, declaring its contracts, and building its environment, see [Authoring a Method Script](../tutorials/authoring-a-method-script.md) and [Registering an Environment](../tutorials/registering-an-environment.md).

## The Run Workspace (.runs/)

The `.runs/` directory is where a pipeline does its transient work. It serves two purposes:

- **`sentinels/`** — Zero-byte sentinel files used only for Snakemake DAG wiring, organized by `{pipeline_id}/{node_id}/{sample}/{variant}/.complete`. They mark a step as finished; they hold no output bytes.
- **`{run_id}/`** — A transient *staging* directory. A method writes its outputs here, then the archive pass moves the bytes into the DVC content-addressed cache, which is the sole authoritative store. The former `.runs/workspace/` output tree no longer exists; outputs are reached by content-hash, not by a workspace path.

Because `.runs/` is staging and sentinels rather than durable data, `wfc init` adds it to `.gitignore` — you never commit it and you can delete it safely between runs.

### What lives where

The key idea is that **output bytes never live under `.runs/`** for long. They are hashed and moved into the content-addressed cache, and the database records which hash holds each output. Whether a step re-runs or reuses a prior result is decided by a cache key computed from the method's code, its parameters, its inputs, and its environment. Both of these — the storage model and the cache-key model — have their own pages:

- [Storage & Provenance](../explanation/storage-and-provenance.md) explains the content-addressed cache, what git does and does not track, how the cache is shared across machines, and what to back up.
- [Caching & Reproducibility](../explanation/caching-and-reproducibility.md) explains how the cache key is computed and why a given step re-runs or hits the cache.

This page stays focused on the *layout*: `.wfc/` for config and the database, `modules/` and `methods/` for your code, `data/samples/` for restored inputs, and `.runs/` plus the DVC cache for execution.

## Next Steps

With an understanding of the project structure, continue to:

- **<a href="../reference/reference/wf-canvas-toml.html">wf-canvas.toml</a>** — The full reference for every section of `.wfc/wf-canvas.toml`.
- **[Authoring a Method Script](../tutorials/authoring-a-method-script.md)** — How to write method scripts using `wfc-client` (`@wfc.method` + `ctx.save_artifact`) or the plain environment-variable contract, and declare contracts in `method.yaml`.
- **[Registering an Environment](../tutorials/registering-an-environment.md)** — How to build the container environment a method runs in.
- **[Storage & Provenance](../explanation/storage-and-provenance.md)** and **[Caching & Reproducibility](../explanation/caching-and-reproducibility.md)** — The deeper story behind where outputs live and why steps re-run.
