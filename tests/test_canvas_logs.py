"""
Tests for the SSE log-stream endpoint — GET /api/wfc/run/{run_id}/stream-logs.

Covers step 4 of the pipeline-output-visibility plan:
- Terminal runs return captured stdout/stderr as SSE events then close.
- ?full=1 returns the entire log; default tails the last N lines.
- Missing log files don't 500 — endpoint still emits the terminal event.
- Unknown run_id → 404.
- Failed runs carry error_message / error_traceback in the terminal event.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from wfc.canvas.server import app, _active_jobs
from wfc.models import Method, Module, Run


# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/test_canvas_run.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        mod = Module(name="data_preprocessing", description="Preprocessing")
        session.add(mod)
        session.flush()
        session.add(Method(
            name="preprocess", module_id=mod.id,
            script_path="methods/preprocess/preprocess.py",
        ))
        session.commit()

    yield engine


@pytest.fixture
def client(db_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))
    _active_jobs.clear()
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_run(
    engine,
    *,
    status: str = "completed",
    error_message=None,
    error_traceback=None,
) -> int:
    """Insert a Run row, return its id."""
    with Session(engine) as session:
        method = session.exec(select(Method)).first()
        run = Run(
            method_id=method.id,
            pipeline_id="pipe-1",
            status=status,
            sample="sampleA",
            error_message=error_message,
            error_traceback=error_traceback,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def _run_dir(project_root: Path, run_id: int) -> Path:
    d = project_root / ".runs" / f"{run_id:08d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_sse(body: str):
    """Parse an SSE body into a list of {type, ...} event dicts.

    Each event block is separated by a blank line and starts with 'data: '.
    The payload is JSON.
    """
    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        # Take all 'data: ' lines in the block and join
        lines = [
            line[len("data: "):]
            for line in block.splitlines()
            if line.startswith("data: ")
        ]
        if not lines:
            continue
        payload = "".join(lines)
        events.append(json.loads(payload))
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stream_logs_terminal_tails_stdout_and_stderr(client, db_engine, tmp_path):
    """Completed run: endpoint emits stdout + stderr + terminal SSE events."""
    run_id = _insert_run(db_engine, status="completed")
    rd = _run_dir(tmp_path, run_id)
    (rd / "stdout.log").write_text("line-out-1\nline-out-2\n", encoding="utf-8")
    (rd / "stderr.log").write_text("line-err-1\n", encoding="utf-8")

    resp = client.get(f"/api/wfc/run/{run_id}/stream-logs")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    stdout = [e for e in events if e["type"] == "stdout"]
    stderr = [e for e in events if e["type"] == "stderr"]
    terminal = [e for e in events if e["type"] == "terminal"]

    assert [e["data"] for e in stdout] == ["line-out-1", "line-out-2"]
    assert [e["data"] for e in stderr] == ["line-err-1"]
    assert len(terminal) == 1
    assert terminal[0]["status"] == "success"


def test_stream_logs_full_returns_all_lines(client, db_engine, tmp_path):
    """?full=1 returns the entire log regardless of size."""
    run_id = _insert_run(db_engine, status="completed")
    rd = _run_dir(tmp_path, run_id)
    lines = [f"line-{i:04d}" for i in range(600)]
    (rd / "stdout.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (rd / "stderr.log").write_text("", encoding="utf-8")

    resp = client.get(f"/api/wfc/run/{run_id}/stream-logs?full=1")
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    stdout = [e["data"] for e in events if e["type"] == "stdout"]
    assert stdout == lines


def test_stream_logs_default_tail_caps_lines(client, db_engine, tmp_path):
    """Default tail returns at most 500 lines (the cap) — no full-file slurp."""
    run_id = _insert_run(db_engine, status="completed")
    rd = _run_dir(tmp_path, run_id)
    lines = [f"line-{i:04d}" for i in range(600)]
    (rd / "stdout.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (rd / "stderr.log").write_text("", encoding="utf-8")

    resp = client.get(f"/api/wfc/run/{run_id}/stream-logs")
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    stdout = [e["data"] for e in events if e["type"] == "stdout"]
    # Last 500 lines, in order
    assert stdout == lines[-500:]


def test_stream_logs_missing_files_returns_terminal_only(client, db_engine, tmp_path):
    """Completed run with no on-disk logs → still returns 200 + terminal event."""
    run_id = _insert_run(db_engine, status="completed")
    # Note: no _run_dir(), no log files written.

    resp = client.get(f"/api/wfc/run/{run_id}/stream-logs")
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    # Exactly one event and it's the terminal one.
    assert [e["type"] for e in events] == ["terminal"]
    assert events[0]["status"] == "success"


def test_stream_logs_unknown_run_returns_404(client, db_engine):
    resp = client.get("/api/wfc/run/99999/stream-logs")
    assert resp.status_code == 404


def test_stream_logs_failed_run_terminal_carries_error(client, db_engine, tmp_path):
    run_id = _insert_run(
        db_engine,
        status="failed",
        error_message="ValueError: bad param",
        error_traceback="Traceback (most recent call last):\n  ...",
    )
    rd = _run_dir(tmp_path, run_id)
    (rd / "stdout.log").write_text("starting\n", encoding="utf-8")
    (rd / "stderr.log").write_text("boom\n", encoding="utf-8")

    resp = client.get(f"/api/wfc/run/{run_id}/stream-logs")
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    terminal = [e for e in events if e["type"] == "terminal"]
    assert len(terminal) == 1
    t = terminal[0]
    assert t["status"] == "failed"
    assert t["error_message"] == "ValueError: bad param"
    assert "Traceback" in t["error_traceback"]
