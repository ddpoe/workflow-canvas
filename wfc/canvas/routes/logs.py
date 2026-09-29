"""Log route: a run's stdout and stderr as a server-sent event stream."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import select

from ... import layout
from ...persistence import get_session
from ...persistence import Run
from ..state import _server_project_root

router = APIRouter()


# ---------------------------------------------------------------------------
# SSE log streaming — /api/wfc/run/{run_id}/stream-logs
# ---------------------------------------------------------------------------

_LOG_TERMINAL_STATUSES = {"completed", "success", "failed", "cancelled"}
_LOG_LIVE_POLL_SECONDS = 0.2
_LOG_LIVE_MAX_WALL_SECONDS = 60 * 60


def _log_tail_lines(path: Path, n: int) -> List[str]:
    """Return the last ``n`` newline-separated lines from ``path`` via seek-from-end.

    Reads backward in 8 KiB chunks; never slurps the full file.
    """
    if not path.exists() or n <= 0:
        return []
    chunk_size = 8192
    buf = bytearray()
    newlines = 0
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        while pos > 0 and newlines <= n:
            read = min(chunk_size, pos)
            pos -= read
            f.seek(pos)
            chunk = f.read(read)
            newlines += chunk.count(b"\n")
            buf[:0] = chunk
    text = bytes(buf).decode("utf-8", errors="replace")
    return text.splitlines()[-n:]


def _log_read_full_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _log_map_terminal_status(db_status: str) -> str:
    return "success" if db_status == "completed" else db_status


def _log_sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/api/wfc/run/{run_id}/stream-logs")
async def stream_run_logs(run_id: str, full: int = 0, tail: int = 500):
    """Stream a run's stdout/stderr as SSE events, ending with a `terminal` event.

    - Terminal runs (status in {completed, success, failed, cancelled}):
      read the on-disk log files, emit each line as a `stdout`/`stderr`
      event, then emit the `terminal` event and close. ``?full=1`` returns
      the whole file; default tails the last ``tail`` lines (default 500).
    - Running runs: poll the log files every ~200 ms, emit new lines as they
      appear, stop when the DB status transitions off ``running`` and emit a
      final `terminal` event.
    """
    try:
        rid = int(run_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    with get_session() as session:
        row = session.exec(select(Run).where(Run.id == rid)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    status = row.status or "unknown"
    error_message = row.error_message
    error_traceback = row.error_traceback

    run_dir = layout.run_archive_dir(_server_project_root(), rid)
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"

    is_terminal = status in _LOG_TERMINAL_STATUSES

    async def stream():
        if is_terminal:
            out_lines = (
                _log_read_full_lines(stdout_path)
                if full
                else _log_tail_lines(stdout_path, tail)
            )
            for line in out_lines:
                yield _log_sse({"type": "stdout", "data": line})
            err_lines = (
                _log_read_full_lines(stderr_path)
                if full
                else _log_tail_lines(stderr_path, tail)
            )
            for line in err_lines:
                yield _log_sse({"type": "stderr", "data": line})
            terminal_payload: Dict[str, Any] = {
                "type": "terminal",
                "status": _log_map_terminal_status(status),
            }
            if error_message is not None:
                terminal_payload["error_message"] = error_message
            if error_traceback is not None:
                terminal_payload["error_traceback"] = error_traceback
            yield _log_sse(terminal_payload)
            return

        # Live run: emit a tail snapshot first, then incremental new bytes.
        for line in _log_tail_lines(stdout_path, tail):
            yield _log_sse({"type": "stdout", "data": line})
        for line in _log_tail_lines(stderr_path, tail):
            yield _log_sse({"type": "stderr", "data": line})
        stdout_offset = stdout_path.stat().st_size if stdout_path.exists() else 0
        stderr_offset = stderr_path.stat().st_size if stderr_path.exists() else 0

        elapsed = 0.0
        while True:
            with get_session() as session:
                cur = session.exec(select(Run).where(Run.id == rid)).first()
            cur_status = cur.status if cur is not None else "unknown"

            for path, kind in (
                (stdout_path, "stdout"),
                (stderr_path, "stderr"),
            ):
                if not path.exists():
                    continue
                size = path.stat().st_size
                offset = stdout_offset if kind == "stdout" else stderr_offset
                if size <= offset:
                    continue
                with path.open("rb") as f:
                    f.seek(offset)
                    chunk = f.read(size - offset)
                if kind == "stdout":
                    stdout_offset = size
                else:
                    stderr_offset = size
                for line in chunk.decode("utf-8", errors="replace").splitlines():
                    if line:
                        yield _log_sse({"type": kind, "data": line})

            if cur_status != "running":
                payload: Dict[str, Any] = {
                    "type": "terminal",
                    "status": _log_map_terminal_status(cur_status),
                }
                if cur is not None and cur.error_message is not None:
                    payload["error_message"] = cur.error_message
                if cur is not None and cur.error_traceback is not None:
                    payload["error_traceback"] = cur.error_traceback
                yield _log_sse(payload)
                return

            await asyncio.sleep(_LOG_LIVE_POLL_SECONDS)
            elapsed += _LOG_LIVE_POLL_SECONDS
            if elapsed >= _LOG_LIVE_MAX_WALL_SECONDS:
                yield _log_sse({"type": "terminal", "status": cur_status})
                return

    return StreamingResponse(stream(), media_type="text/event-stream")
