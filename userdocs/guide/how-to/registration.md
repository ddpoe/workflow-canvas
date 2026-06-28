<!-- generated from pm_mvp::docs.consumer.how-to.registration @ d475fcd67b13; do not edit -->

# Registering Modules, Methods, and Samples

## Registering a Module

A **module** is a logical grouping of related methods that share a common contract (the required outputs and metrics every method in the module must provide).

### Command

```bash
wfc register-module --name my_analysis \
  [--module-dir modules/my_analysis] \
  [--description "My analysis module"] \
  [--contracts '[{"type": "output", "name": "normalized", "value_type": "csv", "required": true}]']
```

### Two ways to supply contracts

| Source | When used |
|---|---|
| `module.yaml` inside `--module-dir` | File is parsed automatically via `parse_module_yaml()` |
| `--contracts` CLI flag (JSON) | Required when no `--module-dir` or no `module.yaml` |

### What happens in the database

1. A **Module** row is upserted (matched by name).
2. One **ModuleContract** row is created for each contract entry. Module contracts declare the module's required *outputs* and *metrics* (each row has a `type` of `output` or `metric`, a `name`, an optional `value_type`, and a `required` flag). At method registration, the framework checks that each method's declared outputs include every output the module marks `required` — module contracts are an output/metric guarantee, not an input-slot spec.

Modules are idempotent to re-register -- calling the command again with the same name updates the existing row.

## Registering a Method

A **method** is a single analysis step -- a public Python function that lives in a directory under a module. (The function may optionally use the `@wfc.method` decorator from `wfc-client`, but plain public functions are discovered and tracked all the same.)

### Command

```bash
wfc register-method modules/my_analysis/preprocess --module my_analysis
```

### What the command does (step by step)

1. **Locate and scan script** -- finds `{method_name}.py` in the given directory and AST-parses it to extract function signatures.
2. **Resolve module** -- looks up the parent module in the database (raises `ValueError` if not found).
3. **Upsert Method row** -- creates or updates the method record, storing the script path and resolved environment name (from `method.yaml`; a method must name a built container env via `env:`).
4. **Sync tracked functions and parameters** -- clears existing `TrackedFunction` and `ParamDef` rows for this method, then inserts fresh rows from the AST scan results. Each tracked function gets an ordinal and its parameters are stored with name, type annotation, and default value.
5. **Store method contract** -- records `input_slots`, `output_slots`, `params_schema`, and `executor` from `method.yaml`. At least one input slot must be declared (raises `ValueError` otherwise).
6. **Validate against module contract** -- checks that the method's declared outputs satisfy the parent module's required outputs.
7. **Git commit** -- commits the method directory to version control so the code version is captured in the cache key.
8. **Copy source snapshot** -- copies source files to `methods/{method_name}/` so the code fingerprint is always computed from the registered copy.

### `save_artifact` static validation (Tier-1 only)

When a method uses the `@wfc.method` decorator, registration additionally AST-scans the decorated function body for `ctx.save_artifact("<name>", ...)` calls and cross-checks the literal output names against `method.yaml` `outputs:`. A required output with no matching `save_artifact` call, or a `save_artifact` name not declared in `method.yaml`, fails registration with a clear error -- so output-name typos are caught at registration, not at run time. Dynamic (non-literal) names produce a warning rather than an error, and the scan is function-body-only (saves inside helper functions are not followed). This check applies only to decorated Tier-1 methods; a plain env-var + file method declares its outputs purely through `method.yaml`.

### Key constraints

- Every method must declare at least one input slot.
- Method outputs must satisfy the parent module's contract (required outputs must be present).
- The `@wfc.method` decorator is optional. AST discovery picks up every public (non-underscore) function in the script automatically; `@wfc.method` (from the `wfc-client` package) is ergonomic Tier-1 sugar, not a requirement for a function to be discovered or tracked.

## Registering a Sample

A **sample** is a data file (typically CSV) that serves as the root input to a pipeline.

### Command

```bash
wfc register-sample --name CFPAC_ERKi --source /data/raw/cfpac_erki.csv
```

### Prerequisites

DVC must be configured. Your `.wfc/wf-canvas.toml` must include a `[dvc]` section with a `url` field (e.g. `url = "file:///path/to/storage"`, `s3://...`, `ssh://...`); `wfc init` creates and mirrors it automatically when `auto_init = true`. Registration raises `DvcNotConfiguredError` if the section is missing, if `url` is unset, or if `.dvc/config` declares no remotes.

### What the command does

1. **Content-hash** -- computes an MD5 hash of the source file via `hash_path()`.
2. **DVC cache store** -- copies the file into the DVC content-addressed cache (`cache_file()`).
3. **DB row** -- creates a `Sample` row storing `file_size`, `file_mtime`, `registration_mode`, and `content_hash`.
4. **Remote push** -- if a DVC remote is configured, the sample's cache object is pushed to it. In standalone CLI mode the push is synchronous (failures are logged as a warning, leaving the sample in the local cache); inside a running pipeline the push is enqueued onto the background push worker instead.

### Important: `data/samples/` is ephemeral

The `data/samples/` directory is an **ephemeral workspace**, not a permanent copy destination. The DVC cache is the sole authoritative store. Before pipeline execution, `wfc restore-sample` materializes the file from the cache into the workspace. The `registered_path` on the Sample row records where the file *will be* restored, not where it currently lives.

Only `"copy"` mode is currently implemented; `"link"` mode raises `NotImplementedError`.

### Restoring samples

```bash
wfc restore-sample --name CFPAC_ERKi
```

Looks up `Sample.content_hash`, calls `restore_from_cache()` (with integrity verification), and falls back to `pull_cache()` if the local cache is empty. The `restore_sample` Snakemake rule integrates this into the DAG for lazy, on-demand restore during pipeline execution.

## Registering from the Canvas

Everything above can also be done in the browser, without the CLI. In the Canvas Registry view, click **+ Register** and pick what you're registering -- a module, a method, or a sample.

**Browse for the path.** Use the **Browse…** button to select the directory or file from your project root instead of typing the path by hand.

**Dry Run first.** Click **Dry Run** to run the same pre-checks registration performs -- without saving anything. The modal shows each check as pass, warning, or fail (contract parsing, environment presence, the AST scan), so you can see exactly what will be validated before you commit.

**Register.** When the pre-checks look right, click **Register** to commit. If the server reports an error or a failing pre-check, it appears in a banner at the top of the modal and nothing is persisted.

This registers exactly what the CLI commands above do -- use whichever fits your workflow.

## Next Steps

- **[[run-and-inspect-results]]** -- learn how to browse runs, trace lineage, and view outputs after pipeline execution.
- **[[canvas]]** -- use the visual Pipeline Builder and Run History interface.
