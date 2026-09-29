"""
Tests for the SSE log-stream endpoint — GET /api/wfc/run/{run_id}/stream-logs.

Covers:
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
from sqlmodel import Session, create_engine, select

from wfc.persistence import Method, Run
from tests.fixtures.routes import (
    build_project_snapshot,
    canvas_client,
    restore_project_snapshot,
)
from tests.harness import Scenario, node


# ---------------------------------------------------------------------------
# Fixtures (the build-once, restore-per-test shape of tests/test_canvas_run.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def logs_project(tmp_path_factory):
    """The one-method project the log-stream tests serve, built once by registration.

    Yields:
        The ``ProjectSnapshot``: the built project, its ``DATABASE_URL``,
        the live database file and the pristine copy.
    """
    from wfc.persistence import reset_engine

    root = tmp_path_factory.mktemp("canvas_logs_project")
    snapshot = build_project_snapshot(
        Scenario(nodes=[node("preprocess", module="data_preprocessing")],
                 samples=["sampleA"]),
        root,
    )
    yield snapshot
    reset_engine()


@pytest.fixture
def db_engine(logs_project, monkeypatch):
    """A pristine copy of the harness-built database, pinned for one test."""
    from wfc.persistence import reset_engine

    restore_project_snapshot(logs_project, monkeypatch)

    engine = create_engine(logs_project.database_url)
    yield engine
    engine.dispose()
    reset_engine()


@pytest.fixture
def client(db_engine, logs_project, monkeypatch):
    """FastAPI test client over the harness-built project ``db_engine`` pinned."""
    return canvas_client(logs_project.project.root, monkeypatch)


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
    # Resolve through the production run-dir helper so the test can't hand-form
    # a path that drifts from where run-step actually writes per-run logs. The
    # helper keys off database.project_root() (pinned to the built project by
    # the db_engine fixture's snapshot restore).
    from wfc.persistence import project_root as get_project_root
    from wfc.layout import run_archive_dir

    d = run_archive_dir(get_project_root(), run_id)
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
    # No _run_dir(), no log files written. The database is restored per
    # test but the run tree is not, so an earlier test's logs under the
    # same run id (ids restart with the restored database) are cleared
    # here: the state under test is a completed run with nothing on disk.
    import shutil
    from wfc.layout import run_archive_dir
    from wfc.persistence import project_root as get_project_root
    shutil.rmtree(run_archive_dir(get_project_root(), run_id), ignore_errors=True)

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
