"""Ctrl-C during a real ``docker build`` ends the build, not only the client.

Case 15: the build's history entry leaves ``Running`` within seconds. A
killed client would leave it running (the plugin holds the build on
Windows); the interrupt passed on to the client's process group makes
Docker cancel it.

The Ctrl-C is the test's own: a helper thread waits until the build's
history entry is running, then raises ``KeyboardInterrupt`` in the main
thread (``_thread.interrupt_main``), which lands in the build's poll loop as
a console Ctrl-C does. Nothing is faked.
"""

from __future__ import annotations

import _thread
import json
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest
from axiom_annotations import workflow

from tests.conftest import requires_docker
from wfc.environments import docker as docker_runner
from wfc.environments.docker import BuildInterruptedError

pytestmark = [pytest.mark.integration, requires_docker]

#: How long the build's history entry may take to leave ``Running`` once the
#: build has raised. Docker's history reads lag under load, so this is wider
#: than the second or two the cancel itself takes.
_ENDS_WITHIN_S = 30.0


def _history() -> list[dict] | None:
    """Return ``docker buildx history ls`` rows, or None when it is unavailable."""
    run = subprocess.run(
        ["docker", "buildx", "history", "ls", "--format", "json", "--no-trunc"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    if run.returncode != 0:
        return None
    return [json.loads(row) for row in run.stdout.splitlines() if row.strip()]


def _context(ref: str) -> str:
    """Return the build context path a history entry records."""
    short = ref.rsplit("/", 1)[-1]
    run = subprocess.run(
        ["docker", "buildx", "history", "inspect", short, "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    if run.returncode != 0:
        return ""
    return json.loads(run.stdout).get("Context", "")


def _running_entry_for(context: Path) -> dict | None:
    """Return the running history entry whose build context is *context*."""
    for row in _history() or []:
        if row.get("status") != "Running":
            continue
        recorded = _context(row["ref"])
        if recorded and Path(recorded).resolve() == context.resolve():
            return row
    return None


@workflow(
    purpose="Ctrl-C during a real sleeping docker build: the build raises "
            "BuildInterruptedError with the client ended on the passed-on "
            "interrupt, and the build's history entry leaves Running within "
            "seconds (case 15)"
)
def test_ctrl_c_ends_a_real_build_within_seconds(tmp_path):
    """Case 15: the passed-on interrupt cancels the real build."""
    if _history() is None:
        pytest.skip("docker buildx history is not available on this daemon")
    nonce = uuid.uuid4().hex[:12]
    context = tmp_path / f"wfc-interrupt-{nonce}"
    context.mkdir()
    (context / "Dockerfile").write_text(
        f"FROM alpine:3.20\nRUN echo {nonce} && sleep 150\n", newline="\n")

    done = threading.Event()
    found: dict = {}

    def press_ctrl_c() -> None:
        deadline = time.monotonic() + 120
        while not done.is_set() and time.monotonic() < deadline:
            entry = _running_entry_for(context)
            if entry is not None:
                found["ref"] = entry["ref"]
                time.sleep(3)  # let the RUN step start sleeping
                if not done.is_set():
                    _thread.interrupt_main()
                return
            time.sleep(0.5)

    presser = threading.Thread(target=press_ctrl_c, daemon=True)
    presser.start()
    caught: BuildInterruptedError | None = None
    try:
        try:
            docker_runner.build(context, f"local/wfc-interrupt-{nonce}:_wfc-build")
        finally:
            done.set()
            presser.join(timeout=10)
    except BuildInterruptedError as exc:
        caught = exc
    except KeyboardInterrupt:
        pytest.fail("the test's Ctrl-C arrived outside the build")

    assert caught is not None, "the build finished before the Ctrl-C"
    assert caught.client_ended is True, "the client did not end on the interrupt"
    short = found["ref"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + _ENDS_WITHIN_S
    status = "Running"
    while time.monotonic() < deadline:
        rows = {row["ref"].rsplit("/", 1)[-1]: row for row in _history() or []}
        status = rows[short]["status"] if short in rows else "<no record>"
        if status != "Running":
            break
        time.sleep(1)
    assert status != "Running", (
        f"the build's history entry is still Running {_ENDS_WITHIN_S:.0f} s "
        "after the interrupt: the build was orphaned")
