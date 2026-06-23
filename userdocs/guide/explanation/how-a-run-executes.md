<!-- generated from pm_mvp::docs.consumer.explanation.how-a-run-executes @ 470fe36dca67; do not edit -->

# How a Run Executes

## Outline — scaffold stub

_Phase-4 scaffold stub (2026-06-17 tutorial-restructure audit). The ANCHOR of the 'how it works' track — the synthesis no single doc currently provides. Focus topic 4._

## Snakemake is the orchestrator

_Stub._ wfc compiles your pipeline JSON into a Snakefile and invokes Snakemake; the generated Snakefile is an intermediate artifact and is NOT meant to be hand-edited. Snakemake must be installed. **To absorb:** ADR-001; discovery adrs-1.

## The per-step unit: wfc run-step

_Stub._ Each step runs as `wfc run-step ...`: cache-check → resolve env → set WFC_* env vars → run the method script as a subprocess in its isolated env → archive outputs. **To absorb:** ADR-008; discovery adrs-2, cycles-8.

## The wfc ↔ method contract (env vars + files)

_Stub._ The interface is purely env vars + files: WFC_RUN_DIR / WFC_INPUT_PATHS / WFC_PARAMS in; declared output files out; exit code = success — so methods can be any language. Cross-ref: authoring-a-method-script. **To absorb:** ADR-008 (+ ADR-020 Tier-2, currently Proposed — do not present pm-client as shipped).
