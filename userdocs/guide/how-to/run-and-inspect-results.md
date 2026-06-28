<!-- generated from pm_mvp::docs.consumer.how-to.run-and-inspect-results @ 66f09d80faa3; do not edit -->

# Run & Inspect Results

## Run & inspect results

After you run a pipeline, every method execution is recorded as a **run** you can inspect. This guide is a practical recipe: it covers what a run is, how to find the output bytes a run produced, and how to browse and export runs visually in the Canvas.

This is a how-to. If you want the *concepts* behind caching, lineage, and on-disk storage, follow the cross-references to the explanation docs as you go.

## Runs and outputs

A **run** is a single method execution tracked by the system. Each run records:

- **Run ID** -- a unique identifier assigned when the run is created.
- **Status** -- one of `running`, `completed`, `failed`, or `cancelled`. A run is `cancelled` either because you cancelled the pipeline, or because an upstream step failed and this step never got to run. In the second case the row also carries `cancelled_due_to_run_id`, pointing at the nearest failed ancestor so you can jump straight to the cause. A run that was skipped because its inputs and parameters matched an earlier run is stored as `completed` with a `cache_source_run_id` field pointing at the original run whose outputs were reused.
- **Timing** -- start and end timestamps.
- **Parameters** -- the exact parameter values used for this execution.
- **Sample** -- which data source was processed.

### Where a run's outputs live

There is no per-run "output folder" on disk to browse. The output bytes for every run live in one place: the DVC content-addressed cache (`.dvc/cache/files/md5/...`). Each step's output is hashed and moved into that cache, and the run record stores the resulting `content_hash`. To get from a run to its bytes you go through the hash, not through a path.

Why it is organized this way -- the sentinel / cache-authoritative storage model, and why archiving happens *after* the pipeline finishes rather than between steps -- is covered in [[storage-and-provenance]]. For inspecting results you only need the operational view below.

### How a run's outputs are retrieved

When a downstream step -- or the Canvas, or you, via the library -- needs an output from an earlier run, the system looks up that run's `content_hash` and resolves it in two tiers:

1. **Cache** -- the bytes are already in the local DVC cache. The local cache path is returned directly.
2. **Remote pull** -- the bytes are not local, so they are pulled from the configured DVC remote first, then the cache path is returned.

If neither tier succeeds, retrieval **fails** and no path is returned. (Very old runs created before content hashing fall back to their original recorded artifact path.)

The practical takeaway: if an output cannot be found locally and there is no remote configured (or the hash is not on the remote), you cannot reconstruct that run's bytes. This is why backing up the cache and the project database matters -- see [[storage-and-provenance]] for the recoverability story.

## Using the Canvas to inspect runs

The **Canvas** web application is the visual way to browse, inspect, and export runs.

### Launch the Canvas

From your project directory, run:

```bash
wfc canvas
```

This starts the Canvas server (default `http://127.0.0.1:8500`) and serves the project in the current directory -- the directory must contain `.wfc/wfc.db`. Use `--port` to bind a different port, `--host` to change the bind address, and `--project-root <path>` to point at a project other than the current directory. Open the printed URL in your browser. For a tour of the Canvas itself, see [[canvas]].

### History tab

Open the **History** tab to review past and in-flight runs. It loads the project automatically from the directory the server was started in.

### Filter runs

Use the filter controls to narrow what you see:

- **Data source** -- filter by sample name.
- **Time range** -- restrict to a date window.
- **Module and method** -- multi-select to show only specific analysis steps.
- **Favorites** -- star runs for quick access, then filter to show only starred runs.
- **Text search** -- free-text search across run metadata.

### Browse lineage chains

Runs are grouped by sample and shown as horizontal chains, with arrows showing how each run feeds the next. Each pipeline row has an **Open pipeline in Canvas ↗** button that loads that pipeline into the builder. Click any run card to open its detail panel.

### Detail panel

The detail panel for a selected run shows its metadata (run ID, method, sample, status, timing), the exact parameters used, any numeric **metrics** the method logged, and its **artifacts** (output files). PNG artifacts show inline thumbnails with a lightbox viewer, and artifacts can be downloaded individually. From the panel you can also use **Open lineage in Canvas** to visualize the run's ancestry, or **Reference in Canvas** to graft the run into a new pipeline as an input.

### Descendants view

Select a run and click **→ View Descendants** in the detail panel to open the descendants view: the selected run as a root card with a tree of every run derived from it (with collapse toggles).

### Full fan-in lineage from the command line

The descendants view follows runs *forward*. To trace a run's full *ancestry* -- including fan-in, where a run consumed outputs from several upstream steps at once -- use the lineage command:

```bash
python -m wfc.lineage --run-id <id>
```

Add `--all` to list every run instead. This walks the complete ancestor DAG, so a fan-in run shows all of its parents, not just one chain. The same data is available programmatically via `get_lineage(run_id)` in `wfc.lineage`. For what lineage *means* and how the parent-child chain is recorded, see [[caching-and-reproducibility]].

### Bulk export

To export many runs at once, enable checkbox mode, select the runs you want, and export their filtered artifacts as a zip file (all artifact types, or CSV-only).

## Next steps

- To understand *why* outputs are stored the way they are and how to keep them recoverable, read [[storage-and-provenance]].
- To understand caching and lineage -- why a step re-runs or hits the cache, and how a run traces back to its samples -- read [[caching-and-reproducibility]].
- For a full tour of the Canvas builder beyond the History tab, see [[canvas]].
