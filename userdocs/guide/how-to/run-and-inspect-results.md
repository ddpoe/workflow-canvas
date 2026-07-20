<!-- generated from pm_mvp::docs.consumer.how-to.run-and-inspect-results @ ff34cba4065c; do not edit -->

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

Why it is organized this way -- the sentinel / cache-authoritative storage model, and why archiving happens *after* the pipeline finishes rather than between steps -- is covered in [Storage & Provenance](../explanation/storage-and-provenance.md). For inspecting results you only need the operational view below.

### How a run's outputs are retrieved

When a downstream step -- or the Canvas, or you, via the library -- needs an output from an earlier run, the system looks up that run's `content_hash` and resolves it in two tiers:

1. **Cache** -- the bytes are already in the local DVC cache. The local cache path is returned directly.
2. **Remote pull** -- the bytes are not local, so they are pulled from the configured DVC remote first, then the cache path is returned.

If neither tier succeeds, retrieval **fails** and no path is returned. (Very old runs created before content hashing fall back to their original recorded artifact path.)

The practical takeaway: if an output cannot be found locally and there is no remote configured (or the hash is not on the remote), you cannot reconstruct that run's bytes. This is why backing up the cache and the project database matters -- see [Storage & Provenance](../explanation/storage-and-provenance.md) for the recoverability story.

## Exporting a run's outputs

The cache is authoritative and **read-only** — you never edit result bytes where they live. `wfc export` is the supported way to get a run's outputs out of it, either as a copy you own or as an in-place path.

### Get a copy you own

```bash
wfc export <run-id> <output-name> <dest>
```

This copies the output's bytes out of the cache into a normal, writable file — open it, edit it, save over it; the archived original is untouched. If `<dest>` is an existing directory, the file lands inside it under its original name. An existing destination *file* is refused unless you pass `--force`, so the command never silently overwrites your data.

To export every output of a run at once, pass `--all` with a directory:

```bash
wfc export <run-id> --all results/
```

Each output is written into the directory under a predictable per-output name.

Cache-hit runs export like any other run. A run that was skipped on a cache hit stores no bytes of its own — its record points at the original run whose outputs were reused. `wfc export` follows that pointer automatically, so exporting from a cache-hit run (or listing its output names) gives you the original run's outputs. Any run ID you pick from the History view is exportable, cached or not.

### Huge outputs: read in place with `--path`

Copying a multi-GB output just to read it is wasteful. `--path` prints the resolved cache path instead of copying:

```bash
p=$(wfc export 412 masks --path)
```

Exactly the path goes to stdout (script-friendly, as above); a warning on stderr reminds you it points into read-only cache storage. Reading it is fine. *Writing* to it fails with a permission error by design — that protection is what keeps a careless `df.to_csv(p)` from corrupting the archived bytes that every future cache hit depends on. If the bytes are not in the local cache yet, they are pulled from the DVC remote before the path is printed.

### When you don't know the output name

Run export with just the run ID:

```bash
wfc export 412
```

This — or a mistyped output name — exits nonzero and lists the run's actual output names, so you never receive the wrong file silently. An output from a run that predates archiving errors with instructions to run `wfc cache archive` first.

For the full flag table see the [CLI Reference](../reference/cli-reference.md). For why outputs live in a read-only content-addressed cache in the first place, see [Storage & Provenance](../explanation/storage-and-provenance.md).

## Using the Canvas to inspect runs

The **Canvas** web application is the visual way to browse, inspect, and export runs.

### Launch the Canvas

From your project directory, run:

```bash
wfc canvas
```

This starts the Canvas server (default `http://127.0.0.1:8500`) and serves the project in the current directory -- the directory must contain `.wfc/wfc.db`. Use `--port` to bind a different port, `--host` to change the bind address, and `--project-root <path>` to point at a project other than the current directory. Open the printed URL in your browser. For a tour of the Canvas itself, see [Canvas Visual Builder](../how-to/canvas.md).

### History tab

Open the **History** tab to review past and in-flight runs. It loads the project automatically from the directory the server was started in.

### Filter runs

Use the filter controls to narrow what you see:

- **Data source** -- filter by sample name.
- **Time range** -- restrict to a date window.
- **Module and method** -- multi-select to show only specific analysis steps. The dropdowns cascade: picking a module narrows the method list to that module's methods, and the sample list narrows to samples with matching runs; selections a narrowed dropdown no longer offers are cleared automatically.
- **Favorites** -- star runs for quick access, then filter to show only starred runs.
- **Text search** -- free-text search across run metadata.

### Executed-run trees (Descendants)

The default **Descendants** view shows, per sample, a collapsible tree of the runs that actually executed. Cache-hit runs are hidden here -- their executed children attach to the nearest non-cached ancestor -- so after a re-run the tree contains only real work. Every filter applies to the tree, and Collapse all / Expand all buttons sit at the right end of the filter bar.

### Browse lineage chains (Lineages)

Switch to **Lineages** to see runs as horizontal chains, with arrows showing how each run feeds the next. Cache-hit runs stay visible in this view, marked with a thin amber border on three sides and a "⟳ CACHED" pill; the status summary counts them separately. Click any run card to open its detail panel. To reload a whole submitted pipeline into the builder, switch to the **Pipelines** view: each pipeline card has an **Open pipeline in Canvas ↗** button.

### Detail panel

The detail panel for a selected run shows its metadata (run ID, method, sample, status, timing), the exact parameters used, any numeric **metrics** the method logged, and its **artifacts** (output files). PNG artifacts show inline thumbnails with a lightbox viewer, and artifacts can be downloaded individually. From the panel you can also use **Open lineage in Canvas** to visualize the run's ancestry, or **Reference in Canvas** to graft the run into a new pipeline as an input.

### Per-run descendant drill-down

Select a run and click **→ View Descendants** in the detail panel to open a drill-down for that run: the selected run as a root card with a tree of every run derived from it (with collapse toggles). Unlike the Descendants tab, this drill-down ignores filters and includes cache-hit runs.

### Full fan-in lineage from the command line

The descendant drill-down follows runs *forward*. To trace a run's full *ancestry* -- including fan-in, where a run consumed outputs from several upstream steps at once -- use the lineage command:

```bash
python -m wfc.lineage --run-id <id>
```

Add `--all` to list every run instead. This walks the complete ancestor DAG, so a fan-in run shows all of its parents, not just one chain. The same data is available programmatically via `get_lineage(run_id)` in `wfc.lineage`. For what lineage *means* and how the parent-child chain is recorded, see [Caching & Reproducibility](../explanation/caching-and-reproducibility.md).

### Bulk export

To export many runs at once, enable checkbox mode, select the runs you want, and export their filtered artifacts as a zip file (all artifact types, or CSV-only).

## Next steps

- To understand *why* outputs are stored the way they are and how to keep them recoverable, read [Storage & Provenance](../explanation/storage-and-provenance.md).
- To understand caching and lineage -- why a step re-runs or hits the cache, and how a run traces back to its samples -- read [Caching & Reproducibility](../explanation/caching-and-reproducibility.md).
- For a full tour of the Canvas builder beyond the History tab, see [Canvas Visual Builder](../how-to/canvas.md).
