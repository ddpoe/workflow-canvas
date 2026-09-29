"""Process state of the canvas server.

The job table, the archive job and its progress writer, and the provider
binding with its refusal. Route modules read these; this module imports
neither the app module nor any route module.

``_wfc_provider`` is rebound by ``lifespan`` and ``POST /api/wfc/load``,
so readers go through this module (``state._wfc_provider``) or
``_require_provider``, never a from-import copy.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from ..persistence import get_session, project_root as _resolve_project_root
from ..storage import unarchived_outputs
from .wfc_provider import WfcProvider


# ---------------------------------------------------------------------------
# Provider state
# ---------------------------------------------------------------------------

_wfc_provider: Optional[WfcProvider] = None

# ---------------------------------------------------------------------------
# Active job tracking (one entry per submitted pipeline)
# ---------------------------------------------------------------------------

_active_jobs: Dict[str, Dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Archive job tracking (single archive pass at a time)
# ---------------------------------------------------------------------------

_archive_job: Dict[str, Any] = {"thread": None, "progress": None}
_archive_lock = threading.RLock()


def _pipeline_running() -> bool:
    """True while any submitted pipeline's run thread is alive."""
    return any(
        j.get("thread") is not None and j["thread"].is_alive()
        for j in _active_jobs.values()
    )


def _server_project_root() -> Path:
    """The one project this server serves — the canonical resolver's answer.

    ``WFC_PROJECT_ROOT`` (set by ``wfc canvas``) validated against the
    marker, else the upward walk from the process cwd, else the resolver's
    ``RuntimeError``. Never the cwd as a fallback: a handler that cannot
    resolve a project errors instead of serving whatever directory the
    server process happens to sit in.
    """
    return _resolve_project_root()


def _pending_archive_snapshot() -> Dict[str, Any]:
    """Group unarchived outputs by run — the initial progress payload.

    Reads Storage's ``unarchived_outputs``, the selection ``archive_outputs``
    archives: RunOutput rows with NULL content_hash on completed runs.
    """
    per_run: Dict[int, Dict[str, Any]] = {}
    with get_session() as session:
        for ro, run in unarchived_outputs(session):
            entry = per_run.get(ro.run_id)
            if entry is None:
                try:
                    method_name = run.method.name if run.method else None
                except Exception:
                    method_name = None
                if method_name:
                    label = f"{method_name}:{run.sample}" if run.sample else method_name
                else:
                    label = f"run {ro.run_id}"
                entry = {
                    "run_id": ro.run_id,
                    "label": label,
                    "done": 0,
                    "total": 0,
                    "outputs": [],
                }
                per_run[ro.run_id] = entry
            entry["outputs"].append(
                {"name": ro.output_name or "<unknown>", "status": "pending"}
            )
            entry["total"] += 1
    return {
        "runs_done": 0,
        "runs_total": len(per_run),
        "current_output": None,
        "per_run": list(per_run.values()),
    }


def _make_archive_progress_writer():
    """Build a (run_id, output_name, status) callback feeding ``_archive_job``.

    Lazily snapshots pending outputs on first event, so the same writer
    serves both the manual archive endpoint and run_pipeline's end-of-run
    pass (where pending rows only exist once the pipeline completes).
    Call ``writer.ensure()`` to snapshot eagerly.  When every output has
    reached a terminal status the live progress handle is cleared and
    archive-status falls back to plain DB counts.
    """
    state: Dict[str, Any] = {"progress": None, "index": None}

    def ensure() -> None:
        if state["progress"] is None:
            snap = _pending_archive_snapshot()
            state["progress"] = snap
            state["index"] = {r["run_id"]: r for r in snap["per_run"]}
            with _archive_lock:
                _archive_job["progress"] = snap

    def writer(run_id, output_name, status) -> None:
        ensure()
        progress = state["progress"]
        run = state["index"].get(run_id)
        if run is None:
            # Row appeared after the snapshot; track it defensively.
            run = {
                "run_id": run_id,
                "label": f"run {run_id}",
                "done": 0,
                "total": 0,
                "outputs": [],
            }
            state["index"][run_id] = run
            progress["per_run"].append(run)
            progress["runs_total"] += 1
        out = next(
            (
                o
                for o in run["outputs"]
                if o["name"] == output_name and o["status"] in ("pending", "hashing")
            ),
            None,
        )
        if out is None:
            out = {"name": output_name, "status": "pending"}
            run["outputs"].append(out)
            run["total"] += 1
        if status == "hashing":
            out["status"] = "hashing"
            progress["current_output"] = output_name
            return
        was_pending = out["status"] in ("pending", "hashing")
        out["status"] = status
        if was_pending:
            run["done"] += 1
            if run["done"] >= run["total"]:
                progress["runs_done"] += 1
        if progress["runs_done"] >= progress["runs_total"]:
            with _archive_lock:
                if _archive_job.get("progress") is progress:
                    _archive_job["progress"] = None

    writer.ensure = ensure
    return writer


def _require_provider() -> WfcProvider:
    if _wfc_provider is None:
        raise HTTPException(
            status_code=400,
            detail="No wfc project configured. Call POST /api/wfc/load first.",
        )
    return _wfc_provider
