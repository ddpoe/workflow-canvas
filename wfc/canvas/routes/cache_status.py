"""Cache-status route: what the posted pipeline would do if it ran now.

A thin caller of ``wfc.execution.cache_status``. It accepts the document
the run route accepts and returns one row per target with its status, the
reason on a blocked row, the source run on a cached or outputs-missing row,
and each output's location. A refused document is data in the response
(``blocked_reason``), never an error status.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..models import PipelineInput

router = APIRouter()


class CacheStatusOutput(BaseModel):
    """Where one output of the source run can be read from."""

    slot: str
    location: Literal["local", "remote", "missing"]


class CacheStatusRowModel(BaseModel):
    """One target's predicted status.

    ``key`` is ``<node id>::<sample>::<variant>``, the key the canvas
    projection gives the same row.
    """

    key: str
    node_id: str
    sample: str
    variant: str
    status: Literal[
        "cached_local", "cached_remote", "outputs_missing",
        "new_step_changed", "new_upstream_reruns", "blocked",
    ]
    reason: Optional[str] = None
    cache_key: Optional[str] = None
    source_run_id: Optional[int] = None
    source_nid: Optional[str] = None
    outputs: List[CacheStatusOutput] = []


class CacheStatusResponse(BaseModel):
    """Every target's row, or the load's refusal that blocks them all."""

    rows: List[CacheStatusRowModel]
    blocked_reason: Optional[str] = None


@router.post("/api/wfc/cache-status", response_model=CacheStatusResponse)
def post_cache_status(pipeline: PipelineInput) -> CacheStatusResponse:
    """Predict each target's cache status for the posted pipeline document.

    Read-only: nothing is written, no env is captured, nothing is restored
    or pulled.
    """
    from ...execution.cache_status import cache_status
    from ..submission import submitted_document

    report = cache_status(submitted_document(pipeline))
    return CacheStatusResponse(
        rows=[
            CacheStatusRowModel(
                key=row.key,
                node_id=row.node_id,
                sample=row.sample,
                variant=row.variant,
                status=row.status,
                reason=row.reason,
                cache_key=row.cache_key,
                source_run_id=row.source_run_id,
                source_nid=row.source_nid,
                outputs=[CacheStatusOutput(slot=slot, location=location)
                         for slot, location in row.outputs],
            )
            for row in report.rows
        ],
        blocked_reason=report.blocked_reason,
    )
