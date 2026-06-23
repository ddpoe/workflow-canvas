<!-- generated from pm_mvp::docs.consumer.explanation.storage-and-provenance @ 5f6783c3c90c; do not edit -->

# Storage & Provenance

## Outline — scaffold stub

_Phase-4 scaffold stub (2026-06-17 tutorial-restructure audit). Focus topic 4. NOTE for authoring: source from caching.design + ADR-018 + current source — NOT provenance.design, which is stale (pending dev-side reconciliation; see handoff)._

## The DVC cache is the authoritative store

_Stub._ Output bytes live ONLY in the DVC content-addressed cache (`.dvc/cache/files/md5/...`); Snakemake's per-step output is a zero-byte sentinel; `.runs/{run_id}/` is transient staging; outputs are reached by content-hash, never by a workspace path. **To absorb:** ADR-018; discovery adrs-3.

## What git tracks (and what it doesn't)

_Stub._ Git auto-commits method/script changes at register-method; pipeline RUNS never produce git commits — wfc's SQLite DB is the run/provenance record. **To absorb:** ADR-007; discovery adrs-4.

## Sharing results across machines

_Stub._ A background worker pushes cache to the configured remote (PushStatus: pending/in_flight/pushed/failed/deferred); collaborators pull + use the DB to map runs → content hashes. Configure via `[dvc] url` in `.wfc/wf-canvas.toml`; you never run `dvc` directly. **To absorb:** ADR-018; discovery cycles-3, cycles-7, adrs-4; caching.design.
