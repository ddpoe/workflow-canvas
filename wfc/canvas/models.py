"""The pipeline document the canvas posts to the validate and run routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# =============================================================================
# Pydantic models
# =============================================================================


class PipelineNode(BaseModel):
    id: str
    type: str | None = "method"  # "method" | "input_selector" | "run_reference"
    method: str = ""
    module: str | None = None
    params: dict[str, Any] = {}
    position: dict[str, float] | None = None
    label: str | None = None  # custom NID from canvas node
    samples: list[str] | None = None  # input_selector: selected samples
    run_id: str | None = None  # run_reference: selected run ID
    source: str | None = None  # input_selector: source type
    fan_mode: str | None = None  # input_selector: "out" (default, per-sample) | "in" (bundle)


class PipelineLink(BaseModel):
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class PipelineInput(BaseModel):
    name: str | None = None
    nodes: list[PipelineNode]
    links: list[PipelineLink] = []
    samples: list[str] = []
    # Parameter-sweep fields carried through to the emitter (wfc/orchestration/snakemake.py).
    # The canvas compiles its richer authoring state (sample_overrides, etc.)
    # into these two fields before POSTing — see frontend pipeline.ts.
    param_sets: dict[str, dict[str, dict[str, Any]]] | None = None
    explicit_combos: list[dict[str, Any]] | None = None
    # When true, pass --keep-going to Snakemake so a failure in one job
    # doesn't cancel independent jobs. Useful for fan-out pipelines where
    # one bad sample shouldn't block the rest. Default off (fail-fast).
    keep_going: bool = False
    # Pipeline variables shelf. Maps variable name -> {type, value}
    # (or bare value for back-compat). Substituted server-side at /run via
    # Graph's resolve_variables before _enrich_pipeline. The pre-
    # substitution form is also persisted as pipeline.editable.json so
    # History "Open in canvas" can rehydrate variables and bind chips.
    variables: dict[str, Any] | None = None
