"""The pipeline document the canvas posts to the validate and run routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# =============================================================================
# Pydantic models
# =============================================================================


class PipelineNode(BaseModel):
    id: str
    type: Optional[str] = "method"  # "method" | "input_selector" | "run_reference"
    method: str = ""
    module: Optional[str] = None
    params: Dict[str, Any] = {}
    position: Optional[Dict[str, float]] = None
    label: Optional[str] = None  # custom NID from canvas node
    samples: Optional[List[str]] = None  # input_selector: selected samples
    run_id: Optional[str] = None  # run_reference: selected run ID
    source: Optional[str] = None  # input_selector: source type
    fan_mode: Optional[str] = None  # input_selector: "out" (default, per-sample) | "in" (bundle)


class PipelineLink(BaseModel):
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None


class PipelineInput(BaseModel):
    name: Optional[str] = None
    nodes: List[PipelineNode]
    links: List[PipelineLink] = []
    samples: List[str] = []
    # Parameter-sweep fields carried through to the emitter (wfc/orchestration/snakemake.py).
    # The canvas compiles its richer authoring state (sample_overrides, etc.)
    # into these two fields before POSTing — see frontend pipeline.ts.
    param_sets: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None
    explicit_combos: Optional[List[Dict[str, Any]]] = None
    # When true, pass --keep-going to Snakemake so a failure in one job
    # doesn't cancel independent jobs. Useful for fan-out pipelines where
    # one bad sample shouldn't block the rest. Default off (fail-fast).
    keep_going: bool = False
    # Pipeline variables shelf. Maps variable name -> {type, value}
    # (or bare value for back-compat). Substituted server-side at /run via
    # Graph's resolve_variables before _enrich_pipeline. The pre-
    # substitution form is also persisted as pipeline.editable.json so
    # History "Open in canvas" can rehydrate variables and bind chips.
    variables: Optional[Dict[str, Any]] = None
