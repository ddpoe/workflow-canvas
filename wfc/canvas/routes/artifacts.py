"""Artifact routes: a run's artifact listing and files, and the export pair."""

from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ... import layout
from ...persistence import read_config
from ...storage import ResolveOutputError
from ..models import ArtifactPreview, ImageInfo, ImageSize, ImageTooLarge, RunArtifact, TableDelimiter
from ..previews import (
    ImageTooLargeError,
    ImageUnreadableError,
    NoPreviewError,
    PreviewCache,
    extension_of,
    image_info,
    media_type,
    preview_artifact,
    preview_image,
)
from ..state import _require_provider, _server_project_root
from ..wfc_provider import ArtifactPathRefusedError

router = APIRouter()


class ExportArtifactsRequest(BaseModel):
    run_ids: list[str] | None = None
    file_types: list[str] | None = None


def _unreadable_outputs(exc: ResolveOutputError) -> HTTPException:
    """Map a run whose outputs cannot be read to a 409 carrying Storage's message.

    Outputs cannot be read when a record is malformed or when their names
    cannot be told apart.
    """
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/api/wfc/run/{run_id}/artifacts", response_model=list[RunArtifact],
            response_model_exclude_unset=True)
def list_wfc_artifacts(run_id: str):
    # Plain `def` — `list_artifacts` walks the run dir and stats every
    # descendant file to compute per-directory totals. On a big run this
    # takes long enough to freeze the history tab; under `async def` it
    # would also block every other request until it finished. Listing reads
    # the project config: an invalid value is a 409 naming the fix.
    provider = _require_provider()
    _project_config()
    try:
        return provider.list_artifacts(run_id)
    except ResolveOutputError as exc:
        raise _unreadable_outputs(exc) from exc


def _resolve_artifact(run_id: str, artifact_path: str) -> tuple[dict, Path, str | None]:
    """Read the project config once, then resolve a displayed artifact name through the provider.

    Every artifact route resolves through this, so each reads the config
    once per request and takes what it needs from the dict returned. The
    content key (the output's hash, plus the member path for a folder member)
    comes from the same resolution, never from hashing the file. An invalid
    project config is a 409 naming the fix, a refused name a 400 with its
    reason, a name the run does not hold a 404, and a run whose outputs
    cannot be read a 409.
    """
    provider = _require_provider()
    config = _project_config()
    try:
        resolved = provider.get_artifact_content(run_id, artifact_path)
    except ArtifactPathRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ResolveOutputError as exc:
        raise _unreadable_outputs(exc) from exc
    if resolved is None or not resolved[0].exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_path}")
    return config, resolved[0], resolved[1]


@router.get("/api/wfc/run/{run_id}/artifact/{artifact_path:path}")
def get_wfc_artifact(run_id: str, artifact_path: str):
    """Serve one artifact's bytes, typed and named by its displayed name.

    The media type comes from ``artifact_path``, never the resolved cache
    path (a top-level file output's cache entry has no extension). The file
    is shown inline and saved under its displayed name; an SVG is sandboxed
    so any script in it cannot run.
    """
    _, file_path, _ = _resolve_artifact(run_id, artifact_path)
    return _serve_file(file_path, artifact_path)


def _serve_file(file_path: Path, artifact_path: str) -> FileResponse:
    """Serve a resolved file as it is, typed, named and (an SVG) sandboxed by its displayed name.

    ``nosniff`` holds the browser to the declared type, so an output served
    inline is never rendered as a kind its name does not say.
    """
    kind = media_type(artifact_path)
    headers = {"X-Content-Type-Options": "nosniff"}
    if kind == "image/svg+xml":
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(file_path, media_type=kind, headers=headers,
                        filename=PurePosixPath(artifact_path).name,
                        content_disposition_type="inline")


@router.get("/api/wfc/run/{run_id}/preview/{artifact_path:path}",
            response_model=ArtifactPreview)
def get_wfc_preview(run_id: str, artifact_path: str,
                    delimiter: TableDelimiter | None = None, header: bool | None = None):
    """Preview the start of a table, JSON or text artifact, saying what was cut.

    A table shows at most 100 rows read from at most 2 MB, its delimiter and
    header row guessed unless ``delimiter`` or ``header`` is given; text shows
    at most 1 MB; JSON is parsed up to 5 MB, and a larger or invalid file
    comes back as text with its reason. Any other kind is a 415. An invalid
    project config is a 409 with ``read_config``'s message.
    """
    _, file_path, _ = _resolve_artifact(run_id, artifact_path)
    try:
        return preview_artifact(file_path, artifact_path, delimiter=delimiter, header=header)
    except NoPreviewError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc


def _project_config() -> dict:
    """Read the project's config for this request; an invalid value is a 409 naming the fix.

    Listing, resolving and exporting a run's artifacts read the same config,
    and would raise a 500 on an invalid value: each artifact route calls this
    once, first, so the user gets ``read_config``'s message instead.
    """
    try:
        return read_config(_server_project_root())
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _resolve_image(run_id: str, artifact_path: str) -> tuple[int, PreviewCache, Path, str | None]:
    """Resolve an image artifact with the request's one config read: ``memory_mb``, the cache, the file, its key.

    ``memory_mb`` and the preview cache under ``cache_mb`` come from the
    ``[previews]`` table of the config :func:`_resolve_artifact` read.
    """
    config, file_path, content = _resolve_artifact(run_id, artifact_path)
    previews = config["previews"]
    cache = PreviewCache(layout.preview_cache_dir(_server_project_root()), previews["cache_mb"])
    return previews["memory_mb"], cache, file_path, content


@router.get(
    "/api/wfc/run/{run_id}/image/{artifact_path:path}",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}, "image/svg+xml": {}},
              "description": "The preview PNG; an SVG as it is, sandboxed."},
        415: {"description": "Not a raster image or SVG, or a file Pillow cannot read."},
        422: {"model": ImageTooLarge,
              "description": "Converting the image would need more than half of "
                             "[previews] memory_mb; the body names the memory_mb that "
                             "admits it (an invalid size is FastAPI's validation error)."},
    },
)
async def get_wfc_image(run_id: str, artifact_path: str, size: ImageSize = "view"):
    """Serve an image artifact as a PNG of at most 260, 2048 or 4096 px on its longer side.

    Every raster image is converted, browser formats included, so the browser
    never gets more than 4096 px; an image that fits ``size`` keeps its own
    size. An SVG is served as it is, sandboxed. ``[previews] memory_mb`` is a
    ceiling on the memory image previews use at once: two conversions run at
    once and each may use up to half. An image whose conversion needs more,
    reckoned from its header (decoded size plus the resize, 8-bit and encode
    working images), is a 422 with its dimensions and the ``memory_mb`` that
    would admit it, never decoded. ``image-info`` tells what the preview shows.

    A converted PNG is kept in the preview cache (``[previews] cache_mb``,
    least recently used dropped first), keyed by the output's content: a
    second request for the same image and size is answered from it, without
    converting or queueing. Otherwise conversions queue for the two
    conversion threads, first come first served. The route is ``async`` so a
    queued request holds none of the server's shared worker threads; the
    config read and the path's resolution run on one, the decode on the
    conversion pool.
    """
    memory_mb, cache, file_path, content = await run_in_threadpool(
        _resolve_image, run_id, artifact_path)
    if extension_of(artifact_path) == "svg":
        return _serve_file(file_path, artifact_path)
    try:
        png = await preview_image(file_path, artifact_path, size, memory_mb,
                                  cache=cache, content=content)
    except (NoPreviewError, ImageUnreadableError) as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except ImageTooLargeError as exc:
        body = ImageTooLarge(detail=str(exc), width=exc.info.width, height=exc.info.height,
                             memory_mb_needed=exc.info.memory_mb_needed)
        return JSONResponse(status_code=422, content=body.model_dump())
    return Response(content=png, media_type="image/png")


@router.get("/api/wfc/run/{run_id}/image-info/{artifact_path:path}",
            response_model=ImageInfo)
def get_wfc_image_info(run_id: str, artifact_path: str, size: ImageSize = "view"):
    """Say what the image route's preview of a raster image shows, read from its header.

    The file's and the preview's dimensions, whether the bit depth is
    reduced, its page count, the memory converting it takes, the smallest
    ``[previews] memory_mb`` that admits it, and why the image route would
    refuse it. Never decodes the file. An SVG, any other kind and a file
    Pillow cannot read are a 415.
    """
    config, file_path, _ = _resolve_artifact(run_id, artifact_path)
    memory_mb = config["previews"]["memory_mb"]
    try:
        return image_info(file_path, artifact_path, size, memory_mb)
    except (NoPreviewError, ImageUnreadableError) as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc


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
    """Zip download of wfc run artifacts, optionally filtered by file type.

    An invalid project config is a 409 with ``read_config``'s message.
    """
    prov = _require_provider()
    _project_config()
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
    """Preview what would be exported (all file types) without downloading.

    An invalid project config is a 409 with ``read_config``'s message.
    """
    prov = _require_provider()
    _project_config()
    try:
        artifacts = prov.get_artifacts(request.run_ids)
    except ResolveOutputError as exc:
        raise _unreadable_outputs(exc) from exc
    return _build_preview_response(artifacts)
