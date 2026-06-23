# Workflows

Catalog of `@workflow`-decorated functions — the top-level operations an agent should know about when working with wfc. Generated from cortex.

<!-- generated 2026-06-17T22:55:45+00:00 by scripts/gen_agent_docs.py — do not edit -->

## `wfc/cli.py`

### `pre_run` — L405

Version-aware pre-run hook: git commit check, cache lookup, run registration

**Steps:**

1. Resolve git commit
2. Look up method
3. Build code fingerprint and version
4. Normalize parent entries
5. Build input fingerprint
6. Capture env content
7. Store env content in DVC cache
8. Build cache key
9. Cache lookup and registration

### `run_pipeline` — L1670

Generate a Snakefile from a pipeline JSON and execute it via Snakemake

**Steps:**

1. Load pipeline
2. Generate Snakefile
3. Write Snakefile to disk
4. Invoke Snakemake

### `run_step` — L2071

Execute a single pipeline step: pre_run, method dispatch, complete_run

**Steps:**

1. Resolve step config
2. Pre-run
3. Handle cache hit
4. Execute method subprocess
5. Collect outputs
6. Complete run
7. Enqueue outputs for async DVC push

## `wfc/init.py`

### `init_project` — L136

Scaffold a new wfc project directory with database, config, and artifact store

**Steps:**

1. Create project directories
2. Write config file
3. Initialize database
4. Update .gitignore
5. Check git repository
6. Initialize DVC provenance

## `wfc/register.py`

### `register_method` — L502

AST-scan a method script and register it with tracked functions and parameters

**Steps:**

1. Locate and scan script
2. Resolve module
3. Upsert method row
4. Sync tracked functions and parameters
5. Store method contract
6. Validate method against module contract
7. Commit method to git
8. Copy source files to registered location

## `wfc/snakemake_gen.py`

### `generate_snakefile` — L1012

Generate a wildcard-based Snakefile from a pipeline definition
