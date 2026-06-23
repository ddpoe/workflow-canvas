<!-- generated from pm_mvp::docs.consumer.reference.method-yaml-schema @ 958c2aff2f90; do not edit -->

# method.yaml Schema

## Outline — scaffold stub

_Phase-4 scaffold stub (2026-06-17 tutorial-restructure audit). Reference table; consolidates the schema currently embedded in writing-methods/method-yaml._

## Fields

_Stub._ One row per method.yaml key: `inputs` / `outputs` (slots, types), `params` (+ kinds incl. `column_of_input`, `new_column`), `columns:` (strict/from_params/patterns), `executor`, `env:` (inherit/pixi/conda/container:<name>/container:docker://...@sha256), `gpus:`. **To absorb:** writing-methods/method-yaml; ADR-002; ADR-005; ADR-019.

## Worked examples

_Stub._ A minimal method.yaml and a full one exercising columns + container env + gpus.
