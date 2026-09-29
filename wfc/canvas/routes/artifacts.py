"""Artifact routes: a run's artifact listing and files, and the export pair."""

from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ...storage import ResolveOutputError
from ..state import _require_provider
from ..wfc_provider import ArtifactPathRefusedError

router = APIRouter()


class ExportArtifactsRequest(BaseModel):
    run_ids: Optional[List[str]] = None
    file_types: Optional[List[str]] = None


def _unreadable_outputs(exc: ResolveOutputError) -> HTTPException:
    """A run whose outputs cannot be read (a malformed record, or names that
    cannot be told apart) is a 409 carrying Storage's message."""
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/api/wfc/run/{run_id}/artifacts")
def list_wfc_artifacts(run_id: str):
    # Plain `def` — `list_artifacts` walks the run dir and stats every
    # descendant file to compute per-directory totals. On a big run this
    # takes long enough to freeze the history tab; under `async def` it
    # would also block every other request until it finished.
    try:
        return _require_provider().list_artifacts(run_id)
    except ResolveOutputError as exc:
        raise _unreadable_outputs(exc) from exc


@router.get("/api/wfc/run/{run_id}/artifact/{artifact_path:path}")
def get_wfc_artifact(run_id: str, artifact_path: str):
    try:
        file_path = _require_provider().get_artifact_path(run_id, artifact_path)
    except ArtifactPathRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ResolveOutputError as exc:
        raise _unreadable_outputs(exc) from exc
    if file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_path}")
    return FileResponse(file_path)


# =============================================================================
# Artifact export helpers
# =============================================================================


def _sanitize(name: str) -> str:
    for ch in ['<', '>', ':', '"', '|', '?', '*', '\\', '/']:
        name = name.replace(ch, '_')
    return name.strip().strip('.')


def _build_artifact_zip(artifacts: list) -> io.BytesIO:
    buf = io.BytesIO()
    seen: set = set()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in artifacts:
            method = _sanitize(item['method'])
            run_name = _sanitize(item['run_name'])
            aname = _sanitize(item['artifact_name'])
            zip_path = f"{method}/{run_name}/{aname}"
            if zip_path in seen:
                base, _, ext = aname.rpartition('.')
                zip_path = f"{method}/{run_name}/{base}_{item['run_id'][:8]}.{ext}"
            seen.add(zip_path)
            if os.path.exists(item['file_path']):
                zf.write(item['file_path'], zip_path)
    buf.seek(0)
    return buf


def _build_preview_response(artifacts: list) -> dict:
    methods: set = set()
    runs: set = set()
    total_size = 0
    by_type: dict = {}
    for item in artifacts:
        methods.add(item['method'])
        runs.add(item['run_id'])
        fp = item.get('file_path', '')
        if fp and os.path.exists(fp):
            size = item.get('size_bytes', os.path.getsize(fp))
            total_size += size
            ext = item.get('extension', item.get('artifact_name', '').rsplit('.', 1)[-1].lower())
            entry = by_type.setdefault(ext, {'count': 0, 'size_bytes': 0})
            entry['count'] += 1
            entry['size_bytes'] += size
    return {
        "total_count": len(artifacts),
        "run_count": len(runs),
        "method_count": len(methods),
        "total_size_bytes": total_size,
        "methods": sorted(methods),
        "by_type": [
            {"ext": e, "count": d["count"], "size_bytes": d["size_bytes"]}
            for e, d in sorted(by_type.items())
        ],
        "files": [
            {
                "method": a['method'],
                "run_name": a['run_name'],
                "artifact": a['artifact_name'],
                "ext": a.get('extension', a.get('artifact_name', '').rsplit('.', 1)[-1].lower()),
                "size_bytes": a.get('size_bytes', 0),
            }
            for a in artifacts
        ],
    }


@router.post("/api/wfc/export-artifacts")
def export_wfc_artifacts(request: ExportArtifactsRequest):
    """Zip download of wfc run artifacts, optionally filtered by file type."""
    prov = _require_provider()
    try:
        artifacts = prov.get_artifacts(request.run_ids, extensions=request.file_types)
    except ResolveOutputError as exc:
        raise _unreadable_outputs(exc) from exc
    if not artifacts:
        raise HTTPException(status_code=404, detail="No matching files found.")
    buf = _build_artifact_zip(artifacts)
    ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="wfc_export_{ts}.zip"'},
    )


@router.post("/api/wfc/preview-artifacts")
def preview_wfc_artifacts(request: ExportArtifactsRequest):
    """Preview what would be exported (all file types) without downloading."""
    try:
        artifacts = _require_provider().get_artifacts(request.run_ids)
    except ResolveOutputError as exc:
        raise _unreadable_outputs(exc) from exc
    return _build_preview_response(artifacts)
