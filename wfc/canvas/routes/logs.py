"""Log route: a run's stdout and stderr as a server-sent event stream."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import select

from ... import layout
from ...persistence import Run, get_session
from ..state import _server_project_root

router = APIRouter()


# ---------------------------------------------------------------------------
# SSE log streaming — /api/wfc/run/{run_id}/stream-logs
# ---------------------------------------------------------------------------

_LOG_TERMINAL_STATUSES = {"completed", "success", "failed", "cancelled"}
_LOG_LIVE_POLL_SECONDS = 0.2
_LOG_LIVE_MAX_WALL_SECONDS = 60 * 60


def _log_tail_lines(path: Path, n: int, end: int | None = None) -> list[str]:
    """Return the last ``n`` newline-separated lines from ``path`` via seek-from-end.

    Reads backward in 8 KiB chunks; never slurps the full file.

    Args:
        path: The log file.
        n: The most lines to return.
        end: Read only the file's first ``end`` bytes (the size the caller
            took), or the whole file when None.
    """
    if not path.exists() or n <= 0:
        return []
    chunk_size = 8192
    buf = bytearray()
    newlines = 0
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell() if end is None else min(f.tell(), end)
        while pos > 0 and newlines <= n:
            read = min(chunk_size, pos)
            pos -= read
            f.seek(pos)
            chunk = f.read(read)
            newlines += chunk.count(b"\n")
            buf[:0] = chunk
    text = bytes(buf).decode("utf-8", errors="replace")
    return text.splitlines()[-n:]


def _log_read_full_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _log_map_terminal_status(db_status: str) -> str:
    return "success" if db_status == "completed" else db_status


def _log_sse(payload: dict[str, Any], event_id: str | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}data: {json.dumps(payload)}\n\n"


def _log_event_id(stdout_offset: int, stderr_offset: int) -> str:
    """The SSE id naming how far a live stream has read: ``<stdout>-<stderr>`` bytes."""
    return f"{stdout_offset}-{stderr_offset}"


def _log_resume_offsets(last_event_id: str | None) -> tuple[int, int] | None:
    """The stdout and stderr byte offsets a reconnect's ``Last-Event-ID`` names.

    Args:
        last_event_id: The header's value, as the browser sent it.

    Returns:
        ``(stdout_offset, stderr_offset)``, or None when the header is absent
        or is not an id this route wrote.
    """
    if not last_event_id:
        return None
    parts = last_event_id.strip().split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def _log_file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


@router.get("/api/wfc/run/{run_id}/stream-logs")
async def stream_run_logs(run_id: str, full: int = 0, tail: int = 500,
                          last_event_id: str | None = Header(default=None)):
    """Stream a run's stdout/stderr as SSE events, ending with a `terminal` event.

    - Terminal runs (status in {completed, success, failed, cancelled})
      reached with no resume id: read the on-disk log files, emit each line
      as a `stdout`/`stderr` event, then emit the `terminal` event and
      close. ``?full=1`` returns the whole file; default tails the last
      ``tail`` lines (default 500).
    - Running runs: emit a tail snapshot, then poll the log files every
      ~200 ms, emit new lines as they appear, stop when the DB status
      transitions off ``running`` and emit a final `terminal` event. Each
      live line's event carries an SSE ``id`` naming the stdout and stderr
      byte offsets read through it (the snapshot's end is marked by an
      id-only frame). A reconnect sending that id as ``Last-Event-ID`` (the
      browser's auto-reconnect does) opens with an id-only frame naming
      the same offsets, then resumes from them with no snapshot, so no line
      repeats and none is missed across a server restart. A run that ended
      while the connection was down gets only the bytes after the offsets,
      then its `terminal` event. An absent or malformed id gets the
      snapshot, as a first connection does.
    """
    try:
        rid = int(run_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc

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
    resume = _log_resume_offsets(last_event_id)

    async def stream():
        if is_terminal and resume is None:
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
            terminal_payload: dict[str, Any] = {
                "type": "terminal",
                "status": _log_map_terminal_status(status),
            }
            if error_message is not None:
                terminal_payload["error_message"] = error_message
            if error_traceback is not None:
                terminal_payload["error_traceback"] = error_traceback
            yield _log_sse(terminal_payload)
            return

        if resume is not None:
            # A reconnect: carry on from where the last connection's id says.
            # One frame first, so the browser sees the reconnect answered
            # even while the step prints nothing. A run that ended meanwhile
            # gets the rest of its bytes from the loop, then its terminal.
            stdout_offset, stderr_offset = resume
            yield f"id: {_log_event_id(stdout_offset, stderr_offset)}\n\n"
        else:
            # Live run: emit a tail snapshot up to the sizes taken now, mark
            # its end with an id, then the bytes written after it.
            stdout_offset = _log_file_size(stdout_path)
            stderr_offset = _log_file_size(stderr_path)
            for line in _log_tail_lines(stdout_path, tail, end=stdout_offset):
                yield _log_sse({"type": "stdout", "data": line})
            for line in _log_tail_lines(stderr_path, tail, end=stderr_offset):
                yield _log_sse({"type": "stderr", "data": line})
            yield f"id: {_log_event_id(stdout_offset, stderr_offset)}\n\n"

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
                # One event per line, its id the offsets through that line.
                for raw in chunk.splitlines(keepends=True):
                    offset += len(raw)
                    if kind == "stdout":
                        stdout_offset = offset
                    else:
                        stderr_offset = offset
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line:
                        yield _log_sse({"type": kind, "data": line},
                                       _log_event_id(stdout_offset, stderr_offset))

            if cur_status != "running":
                payload: dict[str, Any] = {
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
