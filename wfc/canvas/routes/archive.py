"""Archive routes: unarchived-output counts and the manual archive pass."""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException

from ...persistence import get_session
from ...storage import unarchived_outputs
from ..state import (
    _archive_job,
    _archive_lock,
    _make_archive_progress_writer,
    _pipeline_running,
    _server_project_root,
)

router = APIRouter()


@router.get("/api/wfc/archive-status")
def get_archive_status():
    """Unarchived-output counts + live progress of any running archive pass.

    Read-only.  Counts come straight from the DB (progress truth is the DB);
    ``progress`` is non-null only while an archive pass runs in this server
    process — a manual job or a pipeline's end-of-run auto-archive.
    """
    try:
        with get_session() as session:
            run_ids = [ro.run_id for ro, _run in unarchived_outputs(session)]
    except RuntimeError:
        # No project configured yet — report an empty, idle state.
        run_ids = []
    pipeline_running = _pipeline_running()
    with _archive_lock:
        progress = _archive_job.get("progress")
        thread = _archive_job.get("thread")
        archive_thread_alive = thread is not None and thread.is_alive()
        if progress is not None and not archive_thread_alive and not pipeline_running:
            # Stale handle from an interrupted pass — drop it.
            _archive_job["progress"] = None
            progress = None
    return {
        "state": "archiving" if progress is not None else "idle",
        "unarchived_runs": len(set(run_ids)),
        "unarchived_outputs": len(run_ids),
        "pipeline_running": pipeline_running,
        "progress": progress,
    }


@router.post("/api/wfc/cache/archive")
def start_cache_archive():
    """Start a background archive pass over all unarchived outputs.

    409 when an archive job is already running or a pipeline is in flight
    (the pipeline's end-of-run pass archives on its own).
    """
    with _archive_lock:
        thread = _archive_job.get("thread")
        if thread is not None and thread.is_alive():
            raise HTTPException(status_code=409, detail="Archive job already running")
        if _pipeline_running():
            raise HTTPException(
                status_code=409,
                detail="Pipeline in flight; outputs archive automatically when it completes",
            )
        writer = _make_archive_progress_writer()
        writer.ensure()  # eager snapshot so progress shows immediately (RLock-safe)
        project_dir = str(_server_project_root())

        def _run_archive() -> None:
            from ...storage import archive_outputs
            try:
                archive_outputs(project_dir, progress_fn=writer)
            finally:
                with _archive_lock:
                    _archive_job["progress"] = None

        t = threading.Thread(target=_run_archive, daemon=True)
        _archive_job["thread"] = t
        t.start()
    return {"status": "started"}
