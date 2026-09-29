"""`wfc canvas` binds its project before it serves.

The verb resolves a project root, validates it, exports it as
``WFC_PROJECT_ROOT`` and only then hands the app to uvicorn. No server
starts here: ``uvicorn.run`` is replaced, so what is asserted is what the
verb decided — the refusals, the exported root, and the arguments the
server would have been started with.

Requirement: ``docs/system/cli/catalog.json``, section ``canvas.canvas-start``.
"""
from __future__ import annotations

import os

from axiom_annotations import Step, workflow

from tests.conftest import _make_wfc_marker, make_marker_project
from tests.fixtures.fakes import stub_server_bind


@workflow(
    purpose="`wfc canvas` resolves and validates a project before serving it: "
            "a --project-root whose database file is missing is refused by "
            "name, a launch outside any project is refused with the "
            "resolver's marker-naming error, and a real project is exported "
            "as WFC_PROJECT_ROOT before uvicorn is started on the canvas app "
            "with the host, the port and the reload flag.",
    inputs="A project missing its database, a directory that is no project, "
           "and an initialized project",
    outputs="The two refusals, the exported root, and uvicorn's arguments",
)
def test_canvas_binds_its_project_before_serving(tmp_path, monkeypatch, capsys):
    from wfc.cli import cli_main
    from wfc.persistence import reset_engine

    started: list[tuple] = []
    stub_server_bind(monkeypatch, started=started)
    # The verb exports WFC_PROJECT_ROOT into the real environment. Register it
    # with monkeypatch before removing it, so teardown restores the session's
    # own value rather than leaking this test's project into the next test.
    monkeypatch.setenv("WFC_PROJECT_ROOT", "registered-for-restore")
    monkeypatch.delenv("WFC_PROJECT_ROOT")

    口 = Step(step_num=1, name="A named project whose database is missing",
             purpose="--project-root names a real project directory, but the "
                     "database the canvas must serve is not there",
             critical="The refusal names the database path that was looked "
                      "for, so the user knows which file is missing")
    no_db = (tmp_path / "no-db").resolve()
    no_db.mkdir()
    _make_wfc_marker(no_db)  # a project marker, deliberately no wfc.db
    monkeypatch.chdir(tmp_path)
    reset_engine()

    assert cli_main(["canvas", "--project-root", str(no_db)]) == 1
    assert f"ERROR: No .wfc/wfc.db found in {no_db}" in capsys.readouterr().err
    assert started == []

    口 = Step(step_num=2, name="Launched outside any project",
             purpose="No --project-root and no project above the working "
                     "directory: the resolver's error is the answer",
             critical="No server and no fallback to serving the cwd — a "
                      "started server here would be the wrong project")
    elsewhere = (tmp_path / "elsewhere").resolve()
    elsewhere.mkdir()
    monkeypatch.delenv("WFC_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(elsewhere)
    reset_engine()

    assert cli_main(["canvas"]) == 1
    refusal = capsys.readouterr().err
    assert "Could not resolve workflow-canvas project root" in refusal
    assert "wf-canvas.toml" in refusal
    assert started == []

    口 = Step(step_num=3, name="A project is bound, then served",
             purpose="The resolved project is exported as WFC_PROJECT_ROOT "
                     "and uvicorn is started on the canvas app carrying the "
                     "host, the port and the reload flag",
             outputs="The exported root and uvicorn's call")
    proj = make_marker_project(tmp_path / "proj")
    reset_engine()

    try:
        assert cli_main([
            "canvas", "--project-root", str(proj),
            "--host", "0.0.0.0", "--port", "9123", "--reload",
        ]) == 0
        assert os.environ["WFC_PROJECT_ROOT"] == str(proj)
        assert started == [(
            ("wfc.canvas.server:app",),
            {"host": "0.0.0.0", "port": 9123, "reload": True},
        )]
    finally:
        reset_engine()
