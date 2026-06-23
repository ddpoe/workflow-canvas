<!-- generated from pm_mvp::docs.consumer.explanation.caching-and-reproducibility @ 855bd9c5fd1a; do not edit -->

# Caching & Reproducibility

## Outline — scaffold stub

_Phase-4 scaffold stub (2026-06-17 tutorial-restructure audit). Focus topic 4._

## Why a step re-runs (the cache key)

_Stub._ The cache key has four components — code fingerprint, inputs, params, and `env_fingerprint` — so changing a method's environment invalidates just that step's cache. **To absorb:** discovery cycles-4, features-4; env-fingerprint-provenance cycle; caching.design.

## Lineage: tracing how a result was produced

_Stub._ Lineage is reconstructed by recursive query over RunInput rows (incl. fan-in and cache-audit rows); how to read a result back to its inputs and code version. Reuse the already-correct inspecting-results/lineage prose. **To absorb:** ADR-007; inspecting-results/lineage.
