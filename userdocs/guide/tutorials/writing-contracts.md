<!-- generated from pm_mvp::docs.consumer.tutorials.writing-contracts @ 9a0ec8852838; do not edit -->

# Writing Contracts

## Outline — scaffold stub

_Phase-4 scaffold stub (2026-06-17 tutorial-restructure audit). Focus topic 3: writing contracts._

## Module vs. method contracts (two tiers)

_Stub._ module.yaml declares default I/O types + param schema for all its methods; method.yaml declares/overrides its own and is the source of truth. Enforced at registration, pipeline-load (wiring/type checks), and runtime. **To absorb:** ADR-002; discovery adrs-6; writing-methods/module-contracts; registration/register-module.

## Content-level (column) validation

_Stub._ Declare CSV-column requirements via `columns:` using strict / from_params / patterns; INPUT validation is a hard gate, OUTPUT validation is soft (warns). Worked example for the `from_params` `{}` pattern. **To absorb:** ADR-005; discovery adrs-7; writing-methods/column-of-input.

## Where contracts are enforced

_Stub._ The three enforcement points (register / load / runtime) and what each catches — the mental model for why a pipeline is rejected at load vs. runtime.
