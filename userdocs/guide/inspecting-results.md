<!-- generated from pm_mvp::docs.consumer.inspecting-results @ ed5b2bccf1d1; do not edit -->

# Inspecting Results

## Runs and Outputs

A **run** is a single method execution tracked by the system. Each run has:

- **Run ID** -- a unique identifier assigned at creation.
- **Status** -- `running`, `completed`, `failed`, or `cancelled`. A run becomes `cancelled` either when the user cancels the pipeline or when an upstream step failed and this step never ran (the latter rows also carry `cancelled_due_to_run_id` pointing at the nearest failed ancestor). Cache-hit runs are stored with status `completed` and a `cache_source_run_id` field pointing to the original run whose outputs were reused (the `"cached"` label only appears in pipeline outcome sidecar JSON, not in the Run record itself). `Run.status` is a free-form string field, not an enum table.
- **Timing** -- start and end timestamps.
- **Parameters** -- the exact param values used for this execution.
- **Sample** -- which data source was processed.

### Where outputs live

The DVC content-addressed cache (`.dvc/cache/files/md5/XX/YYY...`) is the **sole authoritative on-disk location** for output bytes. There is no workspace tree. Each pipeline step writes its output to a run-archive staging path, the archive pass hashes it and moves it into the cache, and Snakemake sees only a zero-byte **sentinel** file under `.runs/` that signals "step finished". Downstream steps and the canvas reach an output by looking up its `content_hash` in the database and reading the file directly from the cache — there is no stable per-run workspace path. When a remote is configured, a background worker pushes cache entries to it asynchronously.

Archiving is **deferred** (ADR-007 Phase 2, ADR-011): pipeline steps write outputs with `content_hash = NULL`, and after the full pipeline completes, `archive_outputs` hashes and caches all un-archived `RunOutput` rows in a single batch pass. This eliminates blocking I/O between pipeline steps.

### How outputs are retrieved

When a downstream step (or the canvas) needs an output from an earlier run, `resolve_input` looks up the run's `content_hash` in the database and resolves it in two tiers:

1. **CACHE** -- the bytes are in the local DVC cache (`.dvc/cache/files/md5/...`). Return the cache path directly.
2. **REMOTE-PULL** -- not in the local cache; `pull_cache` fetches the hash from the configured DVC remote, then the cache path is returned.

If neither succeeds, resolution **FAILs** and returns `None`. (Runs predating content-hash integration, where `content_hash` is `NULL`, fall back to returning the original `artifact_path`.)

## Lineage

Every run traces back to its upstream steps through **lineage** -- a directed graph of parent-child relationships that ultimately roots at registered samples.

### How lineage is stored

Lineage is stored as a chain of `RunInput` rows. Each `RunInput` links a run to its `source_run_id` (the run that produced the input it consumed):

```
Run A (root sample) --> RunOutput --> RunInput (source_run_id=A) --> Run B --> RunOutput --> RunInput (source_run_id=B) --> Run C
```

### Fan-in support

A single run can have **multiple parents** when it consumes outputs from more than one upstream step. Each parent is recorded as a separate `RunInput` row with a distinct `source_run_id`.

### Cache audit runs

When a step hits the cache (identical inputs + params + code), a cache-hit audit run is created with `cache_source_run_id` set. These audit rows appear in lineage like normal runs, ensuring every pipeline execution is fully traceable.

### Querying lineage

`get_lineage(run_id)` uses a SQLite **recursive CTE** to walk the full ancestor chain:

```sql
WITH RECURSIVE ancestors AS (
    SELECT run_id, source_run_id FROM run_inputs WHERE run_id = :target
    UNION ALL
    SELECT ri.run_id, ri.source_run_id
    FROM run_inputs ri JOIN ancestors a ON ri.run_id = a.source_run_id
)
SELECT * FROM ancestors;
```

This returns every ancestor run in the DAG, from the target run back to the original sample registrations.

## Using Canvas for Inspection

The **Canvas** web application provides a visual interface for browsing and inspecting pipeline results.

### Loading a project

Open the Canvas History tab and enter the project path (or it auto-loads from the current working directory).

### Filtering runs

Use the filter controls to narrow down results:

- **Data source** -- filter by sample name.
- **Time range** -- restrict to a date window.
- **Module and method** -- multi-select to show only specific analysis steps.
- **Favorites** -- star runs for quick access, then filter to show only starred.
- **Text search** -- free-text search across run metadata.

### Path view (lineage chains)

Runs are grouped by sample and displayed as horizontal chains with arrows showing lineage flow. Click any run card to open the detail panel.

### Detail panel

The detail panel for a selected run shows:

- **Metadata** -- run ID, method, sample, status, and timing.
- **Parameters** -- the exact param values used.
- **Metrics** -- numeric results logged by the method.
- **Artifacts** -- output files. PNG artifacts show inline thumbnails with a lightbox viewer. Artifacts can be downloaded individually or in bulk.

### Descendants view

Select a run and click **→ View Descendants** in the detail panel to open the descendants view: the selected run shown as a root card with a connector-glyph tree of the runs derived from it (with collapse toggles). This view follows the run's descendant links in the canvas. For full fan-in lineage (where a node has multiple parents), run `python -m wfc.lineage --run-id <id>` (use `--all` to list every run) or call the `get_lineage()` library function from `wfc.lineage`, which walks the full DAG via recursive CTE.

### Bulk export

Enable checkbox mode, select multiple runs, and export filtered artifacts as a zip file (all types, or CSV-only).

## Next Steps

- **[Canvas User Guide](../features/canvas/user-guide.json)** -- explore the full Canvas interface including the Pipeline Builder and advanced History features.
- **[CLI Reference](../features/core-pipeline/interfaces/cli.json)** -- complete reference for all `wfc` commands.
