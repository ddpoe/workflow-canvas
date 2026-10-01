"""History routes: runs, lineage, pickers, run edits, and pipeline documents."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ... import layout
from ...persistence import Run, get_session
from ..state import _require_provider

router = APIRouter()


@router.get("/api/wfc/runs")
def get_wfc_runs():
    # Plain `def` (not `async def`) so FastAPI dispatches to a threadpool.
    # The provider method is sync (DB + filesystem I/O); under `async def`
    # it would block the event loop and queue every other request behind
    # it — felt most painfully by the history tab when the user clicks
    # between runs while a slow /artifacts call is in flight.
    return _require_provider().get_all_runs()


@router.get("/api/wfc/run/{run_id}")
def get_wfc_run(run_id: str):
    run = _require_provider().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return run


@router.get("/api/wfc/modules")
def get_wfc_modules():
    return _require_provider().get_modules()



@router.get("/api/wfc/methods")
def get_wfc_methods():
    return _require_provider().get_methods()


@router.get("/api/wfc/samples")
def get_wfc_samples():
    """Return detailed info for all registered samples."""
    return _require_provider().get_samples_detail()


@router.get("/api/wfc/completed-runs")
def get_wfc_completed_runs():
    """Return completed runs with output slots for Run Reference nodes."""
    return _require_provider().get_completed_runs()


@router.get("/api/wfc/run/{run_id}/cancelled-descendants")
def get_wfc_cancelled_descendants(run_id: str):
    """Return runs cancelled because ``run_id`` (or its subtree) failed."""
    return _require_provider().get_cancelled_descendants(run_id)


# ---------------------------------------------------------------------------
# Pre-substitution editable sidecar for History "Open in canvas"
# ---------------------------------------------------------------------------


@router.get("/api/workflow/{pipeline_id}/editable")
def get_pipeline_editable(pipeline_id: str):
    """Return the pre-substitution pipeline JSON (with variables + {$var}).

    Falls back to ``pipeline.json`` (post-substitution) for a run that has
    no editable sidecar.
    """
    provider = _require_provider()
    base_dir = layout.pipeline_run_dir(provider.project_root, pipeline_id)
    editable = base_dir / "pipeline.editable.json"
    fallback = base_dir / "pipeline.json"
    src = editable if editable.exists() else fallback
    if not src.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Pipeline editable form not found for {pipeline_id}",
        )
    try:
        return json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline editable form for {pipeline_id} unreadable: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Load-in-Canvas endpoints
# ---------------------------------------------------------------------------


@router.get("/api/pipelines/{pipeline_id}/document")
def get_pipeline_document(pipeline_id: str):
    """Return the run's literal frozen ``pipeline.json``.

    Reads ``<project_root>/.runs/pipelines/<pipeline_id>/pipeline.json`` —
    written at submission time for canvas runs, frozen at run start by
    ``run_pipeline`` for CLI runs, and the same file
    ``WfcProvider._load_bundled_samples`` consumes for fan-in sample
    resolution. Returns the parsed JSON document as-is so the canvas can
    hand it to ``loadPipeline()`` without transformation.

    404 when the file does not exist (the pipeline was authored but
    never reached the snake-gen / run-generation stage).
    """
    provider = _require_provider()
    pipeline_json = layout.pipeline_doc_path(provider.project_root, pipeline_id)
    if not pipeline_json.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Pipeline document not found for {pipeline_id}",
        )
    try:
        return json.loads(pipeline_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline document for {pipeline_id} is unreadable: {exc}",
        ) from exc


@router.get("/api/pipelines/demo")
def get_demo_pipeline():
    """Return ``<project_root>/demo-pipeline.json`` (written by ``wfc demo``).

    Consumed by the ``?pipeline=demo`` URL param in the canvas, which hands
    the document to ``loadPipeline()`` so each node's real slots resolve
    against the registry. 404 when no demo is scaffolded — inert in a
    normal project.
    """
    provider = _require_provider()
    pipeline_json = Path(provider.project_root) / "demo-pipeline.json"
    if not pipeline_json.exists():
        raise HTTPException(
            status_code=404,
            detail="No demo pipeline in this project — run `wfc demo` first.",
        )
    try:
        return json.loads(pipeline_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"demo-pipeline.json is unreadable: {exc}",
        ) from exc


@router.get("/api/runs/{run_id}/lineage-pipeline")
def get_run_lineage_pipeline(run_id: str):
    """Return a synthesized literal-only lineage pipeline for ``run_id``.

    Walks ``parentRunIds`` from the clicked run back to roots — through
    pipeline boundaries — and synthesizes a flat literal-only pipeline
    JSON suitable for the canvas's ``loadPipeline()``. See
    ``wfc.lineage.synthesis`` for the algorithm.

    Status codes:
      - 200 with synthesized JSON on success
      - 404 if the run id is unknown
      - 422 if synthesis fails (cycle defense, malformed ancestor chain)
    """
    from ...lineage import LineageSynthesisError, synthesize_lineage_pipeline

    provider = _require_provider()
    if provider.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    try:
        return synthesize_lineage_pipeline(provider.run_records(), run_id)
    except LineageSynthesisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class RunPatchRequest(BaseModel):
    """Partial update for a Run's user-editable metadata.

    All fields are optional; missing fields are left unchanged. `nid`
    writes through to `Run.nid` (provenance table); the rest upsert a
    `RunAnnotation` row.

    `archived` is a bool at the API boundary and maps to the nullable
    `archived_at` timestamp column: ``true`` sets it to now, ``false``
    clears it.
    """
    nid: str | None = None
    favorite: bool | None = None
    tags: list[str] | None = None
    archived: bool | None = None


@router.patch("/api/wfc/run/{run_id}")
def patch_wfc_run(run_id: str, patch: RunPatchRequest):
    """Partial-update a run's user-editable metadata."""
    from wfc.persistence import RunAnnotation

    try:
        rid_int = int(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run id: {run_id}") from exc

    with get_session() as session:
        run = session.get(Run, rid_int)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if patch.nid is not None:
            run.nid = patch.nid or None  # empty string clears the override
            session.add(run)

        if (
            patch.favorite is not None
            or patch.tags is not None
            or patch.archived is not None
        ):
            ann = session.exec(
                select(RunAnnotation).where(RunAnnotation.run_id == rid_int)
            ).first()
            if ann is None:
                ann = RunAnnotation(run_id=rid_int)
            if patch.favorite is not None:
                ann.favorite = patch.favorite
            if patch.tags is not None:
                ann.tags = list(patch.tags)
            if patch.archived is not None:
                ann.archived_at = datetime.now(UTC) if patch.archived else None
            ann.updated_at = datetime.now(UTC)
            session.add(ann)

        session.commit()

    # Invalidate the in-memory cache so the next /api/wfc/runs call reflects
    # the change. Skip silently if no provider is configured (e.g. during
    # tests or before a project is loaded) — the DB write already landed.
    try:
        _require_provider()._loaded = False
    except HTTPException:
        pass
    return {"ok": True}
