<!-- generated from pm_mvp::docs.consumer.explanation.caching-and-reproducibility @ babad95db23e; do not edit -->

# Caching & Reproducibility

## Caching & Reproducibility

Workflow Canvas is built so that the same inputs, the same code, the same parameters, and the same environment always produce the same result -- and so that you can always trace any result back to exactly what produced it. Two mechanisms make that true, and this page explains both.

The first is the **cache key**. Every step records a fingerprint of everything that went into it. Re-run a pipeline and a step whose fingerprint is unchanged is skipped entirely, reusing the earlier output instead of recomputing it. Change any one of those ingredients and only the affected step (and whatever depends on it) re-runs. Understanding the four parts of the cache key tells you exactly *why* a step re-ran -- or why it didn't.

The second is **lineage**: the recorded chain of parent-child links that connects a result back through every upstream step to the original registered samples. Lineage is what lets you answer "which inputs, which code version, and which parameters produced this file?" months after the fact.

If you just want to *find and read* a run's outputs, see [[run-and-inspect-results]]. This page is the conceptual reference behind that workflow.

## Why a step re-runs (the cache key)

Before running a step, Workflow Canvas computes a **cache key** -- a single SHA-256 hash combining four independent ingredients:

1. **Code fingerprint** -- a hash of every `.py` source file in the method's registered snapshot. Editing the method's code changes this; an unrelated commit elsewhere in the repo (docs, a different method, config) does not. The Git commit is still recorded on the run as audit-only metadata, but it is deliberately *not* part of the cache key, so unrelated commits never invalidate good cached results.
2. **Inputs** -- a fingerprint built from the cache keys of the upstream runs this step consumes, plus the content hash of any root sample files this step reads directly. Because root samples are identified by content, re-registering or relocating an unchanged sample file does not invalidate the cache. If an upstream step produced a different result, this step's input fingerprint changes and it re-runs.
3. **Parameters** -- the exact parameter values for this step, serialized deterministically. Change a parameter and only the steps that use it re-run.
4. **Environment fingerprint** -- a hash of the method's *resolved* container environment: the content digest of the exact container image the method runs in. When you register an environment, Workflow Canvas builds (or references) an image and records its content digest in `.wfc/envs.json`; at run time the step looks that digest up by env name and folds it into the cache key -- a fast manifest read, no package re-scan. Because the address is the image's content, the fingerprint captures the **entire runtime closure** -- system libraries, the language runtime, and every installed package -- not just the Python packages. This is why bumping a dependency and rebuilding a method's environment (which produces a new image digest) invalidates **just that step** -- not the whole pipeline; methods whose environment is unchanged keep hitting the cache.

When you re-run a pipeline, each step recomputes its cache key. If the key matches a previously completed run, the step is **skipped** and the earlier output is reused; no method code executes. If any one of the four ingredients differs, the key differs, the step runs again, and -- because input fingerprints chain through upstream cache keys -- every downstream step re-runs too. This is the whole mechanism behind "re-running an unchanged pipeline does no duplicate work, but changing one method re-runs only what it touched."

A practical consequence: the *first* re-run after the environment fingerprint was introduced (or after the code-fingerprint change) misses the cache once, because the key formula itself changed. That one-time miss is expected and needs no migration; subsequent runs cache normally. Legacy runs from before these changes keep an empty environment fingerprint and still load fine.

For the deeper rationale and the exact formula, see the caching design spec; for how reused outputs are physically stored and retrieved, see [[storage-and-provenance]].

## Lineage: tracing how a result was produced

Every run traces back to its upstream steps through **lineage** -- a directed graph of parent-child relationships that ultimately roots at registered samples. This is the canonical explanation of how lineage works; the History view and the inspection workflow both build on it.

### How lineage is stored

Lineage is stored as a chain of `RunInput` rows. Each `RunInput` links a run to its `source_run_id` -- the run that produced the input it consumed:

```
Run A (root sample) --> RunOutput --> RunInput (source_run_id=A) --> Run B --> RunOutput --> RunInput (source_run_id=B) --> Run C
```

### Fan-in support

A single run can have **multiple parents** when it consumes outputs from more than one upstream step. Each parent is recorded as a separate `RunInput` row with a distinct `source_run_id`, so a step that merges several inputs has several lineage edges -- one per source.

### Cache-hit runs in lineage

When a step hits the cache, a cache-hit audit run is recorded with `cache_source_run_id` set to the original run whose output was reused. These audit rows appear in lineage like any other run, so even a fully cached re-run stays completely traceable -- you can always see which prior run supplied each reused result.

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

This returns every ancestor run in the DAG, from the target run back to the original sample registrations -- including all fan-in branches. From the command line, `python -m wfc.lineage --run-id <N>` prints the full ancestor tree for a run, which is the most direct way to see every parent of a fan-in step at once.

## Next steps

- To actually find a run and read its outputs in practice, see [[run-and-inspect-results]].
- To understand *where* output bytes live on disk and how they are retrieved (the content-addressed cache, the zero-byte sentinels, remote pulls, and what to back up), see [[storage-and-provenance]].
- For an end-to-end picture of how a single run is scheduled and executed, see [[how-a-run-executes]].
- New here? Start with [[getting-started]] and build [[first-pipeline]].
