"""The runners: one in-process ``wfc`` invocation and the canvas test client.

Pins / does not prove lines are on each builder's docstring.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


class CliResult:
    """The exit code and captured streams of one in-process CLI invocation."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        return (f"CliResult(rc={self.returncode}, out={self.stdout!r}, "
                f"err={self.stderr!r})")


def run_cli(*args: str) -> CliResult:
    """Run one ``wfc`` invocation in-process.

    ``cli_main`` is the whole body of the ``wfc`` entry point, so calling
    it with an argv list exercises argument parsing, project-root
    resolution and the exit code a shell would see -- everything a
    subprocess would, without the interpreter start-up.

    Pins: the process -- ``cli_main`` runs in this interpreter with stdout
    and stderr redirected, so no console script, no interpreter start-up
    and no shell is involved.
    Does not prove: that the ``wfc`` console script is installed, or how a
    real shell's encoding and signals treat the invocation.

    Args:
        *args: argv after the program name, e.g. ``"register-sample",
            "--name", "s"``.

    Returns:
        The exit code and captured stdout/stderr.
    """
    from wfc.cli import cli_main

    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_main(list(args))
    except SystemExit as exc:
        rc = exc.code if exc.code is not None else 0
    return CliResult(rc, out.getvalue(), err.getvalue())


def canvas_client(project_root: Path, monkeypatch):
    """A test client over the canvas app, serving one initialised project.

    Pins the canonical resolver at ``project_root`` (``WFC_PROJECT_ROOT``
    and ``DATABASE_URL`` to the project's own database), drops the
    per-process engine and root cache so the next resolution sees the pin,
    clears the server's job state -- the active-job table and the archive
    job's thread and progress slots -- and returns a client that surfaces
    server exceptions as responses so a test reads the status code a
    browser would see.

    Refuses a directory that is not an initialised project: it lays no
    marker and creates no database. A caller builds the project first --
    ``tmp_project``, :func:`init_test_project`, or the harness's
    ``build_project`` -- so what the client serves is a project a user
    could have.

    Binds no provider: a test that needs ``canvas_state._wfc_provider``
    bound keeps that beside the client, as its own declared shortcut.

    Pins: the served project (env and engine cache), the cleared job
    state, and exceptions-as-responses.
    Does not prove: the server's start-up (``wfc canvas`` binding the
    project before serving), or the load endpoint that binds a provider.

    Args:
        project_root: An initialised project (marker and database present).
        monkeypatch: The test's ``MonkeyPatch``.

    Returns:
        A ``fastapi.testclient.TestClient`` over ``wfc.canvas.server.app``.

    Raises:
        AssertionError: When ``project_root`` has no marker or no database.
    """
    from fastapi.testclient import TestClient

    from wfc import layout
    from wfc.persistence import reset_engine

    root = Path(project_root).resolve()
    missing = [str(p) for p in (layout.marker_path(root), layout.db_path(root))
               if not p.exists()]
    if missing:
        raise AssertionError(
            f"canvas_client refuses {root}: not an initialised project "
            f"(missing {missing}). Build it first -- tmp_project, "
            "init_test_project or the harness's build_project -- the client "
            "lays no marker and creates no database."
        )

    monkeypatch.setenv(layout.ROOT_ENV_VAR, str(root))
    monkeypatch.setenv("DATABASE_URL", layout.database_url(root))
    reset_engine()

    from wfc.canvas import server, state

    state._active_jobs.clear()
    state._archive_job["thread"] = None
    state._archive_job["progress"] = None
    return TestClient(server.app, raise_server_exceptions=False)
