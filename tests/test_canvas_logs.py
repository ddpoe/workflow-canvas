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


def _parse_sse_with_ids(body: str) -> list[tuple[str | None, dict | None]]:
    """Parse an SSE body into (id, payload) frames; an id-only frame has no payload."""
    frames = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event_id, data = None, []
        for line in block.splitlines():
            if line.startswith("id: "):
                event_id = line[len("id: "):]
            elif line.startswith("data: "):
                data.append(line[len("data: "):])
        frames.append((event_id, json.loads("".join(data)) if data else None))
    return frames


def _last_id(frames) -> str:
    ids = [event_id for event_id, _ in frames if event_id is not None]
    assert ids, "the stream named no id"
    return ids[-1]


def _lines(frames) -> list[tuple[str, str]]:
    return [(p["type"], p["data"]) for _, p in frames
            if p is not None and p["type"] in ("stdout", "stderr")]


def test_a_reconnect_with_last_event_id_resumes_without_repeating_or_missing_lines(
        client, db_engine, tmp_path, monkeypatch):
    """A live stream names how far it has read; a reconnect sending that id as
    Last-Event-ID (the browser's auto-reconnect after a server restart) gets
    exactly the lines written since, then the terminal event when the run
    ends. A malformed id gets the snapshot, as a first connection does."""
    import threading

    from tests.fixtures.fakes import short_log_stream_wall

    run_id = _insert_run(db_engine, status="running")
    rd = _run_dir(tmp_path, run_id)
    out, err = rd / "stdout.log", rd / "stderr.log"
    out.write_text("out-1\nout-2\n", encoding="utf-8")
    err.write_text("err-1\n", encoding="utf-8")
    url = f"/api/wfc/run/{run_id}/stream-logs"
    # The test client reads a response whole: the live stream ends at its
    # wall-time guard instead of being dropped mid-way.
    short_log_stream_wall(monkeypatch, 0.4)

    # First connection: the snapshot, then a line written while it streams.
    appender = threading.Timer(0.1, lambda: out.open("a", encoding="utf-8").write("out-3\n"))
    appender.start()
    first = _parse_sse_with_ids(client.get(url).text)
    appender.join()
    assert _lines(first) == [("stdout", "out-1"), ("stdout", "out-2"),
                             ("stderr", "err-1"), ("stdout", "out-3")]
    resume_from = _last_id(first)

    # The connection is lost; more lines are written meanwhile.
    with out.open("a", encoding="utf-8") as f:
        f.write("out-4\n")
    with err.open("a", encoding="utf-8") as f:
        f.write("err-2\n")

    # A malformed id is a first connection: the snapshot again.
    garbled = _parse_sse_with_ids(client.get(url, headers={"Last-Event-ID": "x-y"}).text)
    assert _lines(garbled)[:2] == [("stdout", "out-1"), ("stdout", "out-2")]

    # The reconnect resumes: only the new lines, then the run ends.
    short_log_stream_wall(monkeypatch, 30)

    def end_the_run():
        with Session(db_engine) as session:
            run = session.get(Run, run_id)
            run.status = "completed"
            session.add(run)
            session.commit()

    ender = threading.Timer(0.5, end_the_run)
    ender.start()
    resumed = _parse_sse_with_ids(
        client.get(url, headers={"Last-Event-ID": resume_from}).text)
    ender.join()
    assert _lines(resumed) == [("stdout", "out-4"), ("stderr", "err-2")]
    payloads = [p for _, p in resumed if p is not None]
    assert payloads[-1] == {"type": "terminal", "status": "success"}
    assert [p["type"] for p in payloads].count("terminal") == 1


def _running_run_with_logs(db_engine, tmp_path) -> tuple[int, Path, Path, str]:
    """A running run with two stdout lines and one stderr line on disk."""
    run_id = _insert_run(db_engine, status="running")
    rd = _run_dir(tmp_path, run_id)
    out, err = rd / "stdout.log", rd / "stderr.log"
    out.write_text("out-1\nout-2\n", encoding="utf-8")
    err.write_text("err-1\n", encoding="utf-8")
    return run_id, out, err, f"/api/wfc/run/{run_id}/stream-logs"


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def _end_run(db_engine, run_id: int, status: str = "completed") -> None:
    with Session(db_engine) as session:
        run = session.get(Run, run_id)
        run.status = status
        session.add(run)
        session.commit()


def test_a_resumed_stream_sends_a_frame_before_any_new_output(
        client, db_engine, tmp_path, monkeypatch):
    """A reconnect with a valid Last-Event-ID opens with one frame, so the
    browser sees the reconnect answered even while the running step prints
    nothing; new output follows that frame."""
    import threading

    from tests.fixtures.fakes import short_log_stream_wall

    run_id, out, _err, url = _running_run_with_logs(db_engine, tmp_path)
    short_log_stream_wall(monkeypatch, 0.4)
    resume_from = _last_id(_parse_sse_with_ids(client.get(url).text))

    # A quiet step: the resumed stream still opens with a frame.
    quiet = _parse_sse_with_ids(client.get(url, headers={"Last-Event-ID": resume_from}).text)
    assert quiet[0] == (resume_from, None)
    assert _lines(quiet) == []

    # Output written while the resumed stream is open comes after that frame.
    appender = threading.Timer(0.1, _append, args=(out, "out-3\n"))
    appender.start()
    resumed = _parse_sse_with_ids(client.get(url, headers={"Last-Event-ID": resume_from}).text)
    appender.join()
    assert resumed[0] == (resume_from, None)
    assert _lines(resumed) == [("stdout", "out-3")]


def test_resuming_from_the_end_of_snapshot_frame_yields_exactly_the_lines_written_after_it(
        client, db_engine, tmp_path, monkeypatch):
    """The id-only frame that ends the snapshot is a resume point: a reconnect
    sending it gets every line written after the snapshot and none before."""
    from tests.fixtures.fakes import short_log_stream_wall

    _run_id, out, err, url = _running_run_with_logs(db_engine, tmp_path)
    short_log_stream_wall(monkeypatch, 0.4)

    # Nothing is written while the first connection streams, so the only
    # id it names is the end-of-snapshot frame's.
    first = _parse_sse_with_ids(client.get(url).text)
    assert _lines(first) == [("stdout", "out-1"), ("stdout", "out-2"), ("stderr", "err-1")]
    id_frames = [frame for frame in first if frame[0] is not None]
    assert len(id_frames) == 1 and id_frames[0][1] is None
    snapshot_end = id_frames[0][0]

    _append(out, "out-3\nout-4\n")
    _append(err, "err-2\n")

    resumed = _parse_sse_with_ids(client.get(url, headers={"Last-Event-ID": snapshot_end}).text)
    assert _lines(resumed) == [("stdout", "out-3"), ("stdout", "out-4"), ("stderr", "err-2")]


def test_a_reconnect_to_a_run_that_has_since_finished_sends_only_the_rest_then_the_terminal(
        client, db_engine, tmp_path, monkeypatch):
    """A run that ended while the connection was down: a reconnect with the
    last id gets only the lines after it, then one terminal event. With no id
    the finished run's tail snapshot comes back, as before."""
    from tests.fixtures.fakes import short_log_stream_wall

    run_id, out, err, url = _running_run_with_logs(db_engine, tmp_path)
    short_log_stream_wall(monkeypatch, 0.4)
    resume_from = _last_id(_parse_sse_with_ids(client.get(url).text))

    # The connection is lost; the run writes its last lines and fails.
    _append(out, "out-3\n")
    _append(err, "err-2\n")
    _end_run(db_engine, run_id, status="failed")

    resumed = _parse_sse_with_ids(client.get(url, headers={"Last-Event-ID": resume_from}).text)
    assert _lines(resumed) == [("stdout", "out-3"), ("stderr", "err-2")]
    payloads = [p for _, p in resumed if p is not None]
    assert [p["type"] for p in payloads].count("terminal") == 1
    assert payloads[-1]["type"] == "terminal" and payloads[-1]["status"] == "failed"

    fresh = _parse_sse_with_ids(client.get(url).text)
    assert _lines(fresh) == [("stdout", "out-1"), ("stdout", "out-2"), ("stdout", "out-3"),
                             ("stderr", "err-1"), ("stderr", "err-2")]
    assert [p["type"] for _, p in fresh if p is not None].count("terminal") == 1


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
