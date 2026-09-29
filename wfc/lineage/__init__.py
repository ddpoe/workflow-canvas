"""Lineage: realized run ancestry, answered over run records.

The package holds the one relation every lineage question reads: a run's
upstreams, its ancestors, its descendants, and the runs a failure cancelled.
Synthesis turns a run's ancestry into a literal-only pipeline document for the
canvas. Every rule here is pure: a caller hands in run records and reads the
answer back.
"""
from wfc.lineage.relation import (
    RunRecord,
    ancestors,
    cancelled_descendants,
    descendants,
    upstreams,
)
from wfc.lineage.synthesis import (
    LineageSynthesisError,
    SynthesisRecord,
    synthesize_lineage_pipeline,
)

__all__ = [
    "LineageSynthesisError",
    "RunRecord",
    "SynthesisRecord",
    "ancestors",
    "cancelled_descendants",
    "descendants",
    "synthesize_lineage_pipeline",
    "upstreams",
]
