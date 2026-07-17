<!-- generated from pm_mvp::docs.consumer.explanation.storage-and-provenance @ a3e16e45dfd4; do not edit -->

# Storage & Provenance

## Storage & Provenance

When a pipeline runs, two very different kinds of records are produced: the **output bytes** your methods write, and the **provenance trail** that says which method, parameters, and inputs produced them. Workflow Canvas stores these in two separate places, and understanding the split makes everything else about results, caching, and sharing fall into place.

The short version:

- **Output bytes** live in one place only: a content-addressed cache keyed by the hash of the data itself. They are never referenced by a folder path you would browse.
- **The provenance trail** lives in a SQLite database (`.wfc/wfc.db`). It records every run and maps each output to the content hash that holds its bytes.
- **Git** tracks your *method source* (so a method's code is versioned), but it never records pipeline *runs*.

This page explains why outputs are addressed by content instead of by path, what git does and does not capture, how the cache is shared across machines, and what you must back up to keep results recoverable. For the operational "how do I find a run's output" recipe, see [Run & Inspect Results](../how-to/run-and-inspect-results.md); for why a step re-runs or hits the cache, see [Caching & Reproducibility](../explanation/caching-and-reproducibility.md).

## The content-addressed cache is the only authoritative store

Output bytes live in exactly one place: the DVC content-addressed cache, on disk at `.dvc/cache/files/md5/XX/YYY...`, where the path is derived from the MD5 hash of the file's contents. Two runs that produce byte-identical output share a single cache entry; an output is located by its hash, never by a per-run folder you could navigate to.

There is **no workspace tree**. A method writes its output into a transient staging directory under `.runs/{run_id}/` while it runs, and once the data is safe in the cache that staging copy is gone. The file Snakemake actually tracks as a step's output is a **zero-byte sentinel** under `.runs/sentinels/...` — it carries no data; it exists only so Snakemake can wire the dependency graph and know a step finished. The real bytes are in the cache, addressed by hash.

This is why you should never go looking for outputs by browsing folders. A downstream step, the History view, and the Canvas all reach an output the same way: they read its `content_hash` from the database and resolve that hash to a cache path. Because the address is the content, an output is immutable and de-duplicated for free — the same result is stored once no matter how many runs reference it.

Archived cache entries are marked read-only at the filesystem level. Because a cache file's path is the hash of its contents, editing the file in place would corrupt every run that references that hash — so the store refuses writes outright. If you get a path from `wfc export --path` and write back to it, the write fails:

```python
path = "…"  # from: wfc export 412 masks --path
df.to_csv(path)
# PermissionError: [Errno 13] Permission denied: '.dvc/cache/files/md5/1f/9c…'
```

To change an output, export a copy you own instead (see [Run & Inspect Results](../how-to/run-and-inspect-results.md)). `wfc cache prune` still deletes protected entries when you reclaim space.

This model was adopted in ADR-018, which eliminated the older copy-everything workspace in favor of the cache being the sole on-disk store and a move (not a copy) into it.

## Archiving is deferred, and indexed by the database

Hashing a file and moving it into the cache is called **archiving**, and it does not happen inline between pipeline steps. While a pipeline runs, each step records its output row with `content_hash = NULL` and leaves the bytes in staging. After the whole pipeline finishes, a single archive pass hashes every un-archived output and moves it into the cache in one batch. This keeps slow hashing I/O off the critical path between steps, which matters a lot for large-output pipelines.

Archiving runs automatically when you pass the archive option to a pipeline run, and you can also trigger it on demand with `wfc cache archive`, which finds every output still carrying a `NULL` hash and archives it.

The honest caveat to understand here: the cache is a flat pile of content-addressed blobs with no human-readable names. The **only** thing that maps a meaningful run and output back to the right blob is the SQLite database at `.wfc/wfc.db` — and that database is **not tracked in git**. If you delete or lose `.wfc/`, the blobs in the cache become anonymous and unrecoverable even though the bytes are still on disk. **Backing up `.wfc/` is required** to keep archived outputs usable. The cache pruning command refuses to remove blobs for runs whose outputs have not yet been archived, so you cannot accidentally prune away data that exists only in staging — but it cannot protect you from losing the database index itself.

## What git tracks (and what it doesn't)

Git's role here is narrow and deliberate. When you register a method, Workflow Canvas snapshots that method's source files into the project and **auto-commits** that snapshot to git. Your method code is therefore versioned, and the commit SHA is captured as audit metadata on the method version row. (Cache validity itself is driven by a content fingerprint of the source files, not by the commit, so an unrelated commit elsewhere in the repo does not invalidate cached results — see [Caching & Reproducibility](../explanation/caching-and-reproducibility.md).)

What git does **not** do: pipeline runs never produce git commits. Running a pipeline does not stage, commit, or touch your working tree. There is intentionally no `--allow-dirty` style escape hatch layered on top of runs — the commit-then-run discipline applies to *registering methods*, not to executing pipelines.

So the division of labor is: **git versions method source**, the **SQLite database (`.wfc/wfc.db`) is the run and provenance record**, and the **cache holds output bytes**. ADR-007 established this split — git for code, a database for provenance, content-addressed storage for data.

## Sharing results across machines

Because outputs are addressed by content hash, sharing them is just a matter of getting the right blobs onto another machine — no paths to rewrite. When you configure a remote, a **background push worker** drains newly archived outputs to it asynchronously, so your runs are never blocked waiting on uploads. Each output row carries a `push_status` that walks `pending` → `in_flight` → `pushed` on the happy path, drops to `failed` (and retries with backoff) on error, and sits at `deferred` when no remote is configured.

A collaborator who pulls your database can then reproduce your results: the database tells them which `content_hash` belongs to which run, and the cache resolver fetches any missing blob from the remote on demand. When something reads an output, resolution happens in tiers — **CACHE** if the hash is already in the local cache, otherwise **REMOTE-PULL** to fetch it from the configured remote, and only then **FAIL** if neither has it.

You configure all of this through the `[dvc]` block in `.wfc/wf-canvas.toml` — set `url` to any DVC-native scheme (`file://`, `s3://`, `ssh://`, `gs://`, `azure://`, and so on). Workflow Canvas mirrors that config to DVC and dispatches on the URL scheme for you; you **never run `dvc` directly**. See [Project Anatomy](../explanation/project-anatomy.md) for the full config block.

## Where to go next

- [Run & Inspect Results](../how-to/run-and-inspect-results.md) — the operational recipe: find a run, read its outputs, and trace lineage.
- [Caching & Reproducibility](../explanation/caching-and-reproducibility.md) — why a step re-runs versus hits the cache, and how the lineage chain is recorded.
- [Project Anatomy](../explanation/project-anatomy.md) — the `.wfc/` layout and the `wf-canvas.toml` config, including the `[dvc]` remote block.
- [Registering Modules, Methods, and Samples](../how-to/registration.md) — how registering a method snapshots and commits its source.
