"""The canvas serves the project it was launched in — never the cwd.

The canvas resolves its project root through the resolver the CLI uses,
never by rules of its own such as falling back to the server process's
working directory. A wrong-but-plausible root is the Layout unit's worst
failure mode, and one of the canvas's root sites is the file-browser's
path-traversal guard.

This story drives the server the way `wfc canvas` does — from a nested
directory inside a project with no override set — and then from outside
any project, where the only acceptable answer is a refusal naming the
marker.

Requirement: ``docs/system/layout.json``, section ``model`` ("never a
silent fallback to the current directory").
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from fastapi.testclient import TestClient

from tests.conftest import make_marker_project
from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import completed_run
from wfc.canvas import server as canvas_server
from wfc.canvas import state as canvas_state
from wfc.persistence import project_root, reset_engine


@workflow(purpose="A canvas launched from a nested directory inside a project "
                  "serves that project's root — the file browser and its "
                  "traversal guard included — and a canvas launched outside "
                  "any project refuses to start rather than serve the cwd",
          inputs="An initialized project with a nested working directory, "
                 "and a bare directory that is no project at all",
          outputs="The served root, the browser's anchoring, and the refusal")
def test_canvas_serves_the_launched_project(tmp_path, monkeypatch):
    """The canvas serves exactly the project it was launched in."""
    口 = Step(step_num=1, name="Initialize a project and step into a nested dir",
             purpose="The hostile launch shape: no WFC_PROJECT_ROOT, the cwd "
                     "two levels below the root, and a directory that exists "
                     "only at the root so the browser's answer names which "
                     "directory it served",
             outputs="cwd at <root>/a/b with the resolver cache cleared")
    root = (tmp_path / "proj").resolve()
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    make_marker_project(root)
    (root / "only_at_root").mkdir()
    (nested / "only_in_nested").mkdir()
    monkeypatch.delenv("WFC_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{root / '.wfc' / 'wfc.db'}")
    monkeypatch.chdir(nested)
    reset_engine()

    口 = Step(step_num=2, name="The server's root is the resolved project root",
             purpose="The server has one accessor and it is the canonical "
                     "resolver's answer — the same one the CLI gets",
             critical="Compared against the canonical resolver directly, "
                      "so a second canvas rule would show as disagreement")
    assert canvas_state._server_project_root() == project_root() == root

    口 = Step(step_num=3, name="Startup auto-loads that project",
             purpose="Entering the lifespan is what `wfc canvas` does before "
                     "serving; the provider it loads is rooted where the "
                     "resolver pointed, not where the process sits")
    canvas_state._active_jobs.clear()
    with TestClient(canvas_server.app) as client:
        assert Path(canvas_state._wfc_provider.project_root).resolve() == root

        口 = Step(step_num=4, name="The browser is anchored at the root",
                 purpose="fs_browse lists the resolved root by default and "
                         "its traversal guard is rooted there too — the site "
                         "with the security reading",
                 outputs="The root's listing from a nested cwd; a 400 for a "
                         "path that escapes the root")
        listing = client.get("/api/fs/browse")
        assert listing.status_code == 200, listing.text
        assert "only_at_root" in listing.text
        assert "only_in_nested" not in listing.text, (
            "the browser listed the cwd instead of the resolved project root"
        )
        escaped = client.get("/api/fs/browse", params={"path": ".."})
        assert escaped.status_code == 400, escaped.text

    口 = Step(step_num=5, name="Outside any project the canvas refuses to start",
             purpose="No resolvable project means no server: startup raises "
                     "the resolver's marker-naming error, and a handler "
                     "reached anyway errors instead of serving the cwd",
             critical="The canonical resolver is asserted first so a marker "
                      "above pytest's temp dir shows as pollution, not as a "
                      "canvas that served the cwd")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    reset_engine()
    with pytest.raises(RuntimeError, match="wf-canvas.toml"):
        project_root()  # pollution guard: an ancestor marker would resolve here
    with pytest.raises(RuntimeError, match="wf-canvas.toml"):
        with TestClient(canvas_server.app):
            pass
    unstarted = TestClient(canvas_server.app, raise_server_exceptions=False)
    assert unstarted.get("/api/fs/browse").status_code == 500
    assert not (elsewhere / ".wfc").exists() and not (elsewhere / ".runs").exists()


def _nid_in(root: Path):
    """Read run 1's label straight from ``root``'s database file."""
    conn = sqlite3.connect(str(root / ".wfc" / "wfc.db"))
    try:
        return conn.execute("SELECT nid FROM runs WHERE id = 1").fetchone()[0]
    finally:
        conn.close()


@workflow(purpose="A canvas serves one database from startup: a write through a "
                  "server session and a read through the history provider land "
                  "in the launch project's database file; loading another "
                  "project is refused and changes nothing, while the served "
                  "project reloads by any spelling",
          inputs="Two initialized projects with one run each, and a canvas "
                 "launched on the first the way `wfc canvas` launches it, from "
                 "inside the second",
          outputs="The database file each operation touched, the refusal, "
                  "and the reload")
def test_canvas_reads_and_writes_one_database(tmp_path, monkeypatch):
    """What the canvas reads and what it writes go to one database file."""
    口 = Step(step_num=1, name="Initialize two projects",
             purpose="Each project's run carries a sample naming its project, "
                     "so a read shows which database answered",
             outputs="Projects A and B, one run each, and B's file as bytes")
    a = (tmp_path / "a").resolve()
    b = (tmp_path / "b").resolve()
    for root, sample in ((a, "sample-in-a"), (b, "sample-in-b")):
        root.mkdir()
        completed_run(root, monkeypatch=monkeypatch, method="load", module="m",
                      sample=sample, pipeline_id="p1")
    b_before = (b / ".wfc" / "wfc.db").read_bytes()

    口 = Step(step_num=2, name="Launch a canvas on A from inside B",
             purpose="`wfc canvas` sets the root variable and no database "
                     "override; the process sits in the other project",
             critical="DATABASE_URL is registered with monkeypatch before it "
                      "is removed, so anything the server writes to it is "
                      "restored at teardown")
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(a))
    monkeypatch.setenv("DATABASE_URL", "registered-for-restore")
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.chdir(b)
    bind_provider(monkeypatch, None)
    canvas_state._active_jobs.clear()
    reset_engine()
    try:
        with TestClient(canvas_server.app) as client:
            口 = Step(step_num=3, name="Write through a server session",
                     purpose="Relabeling a run writes through the server's "
                             "own session, not through the provider")
            written = client.patch("/api/wfc/run/1", json={"nid": "written-by-server"})
            assert written.status_code == 200, written.text

            口 = Step(step_num=4, name="Read through the history provider",
                     purpose="The runs payload is the provider's read; it "
                             "shows A's run carrying the label just written")
            runs = client.get("/api/wfc/runs").json()
            assert [(r["dataSource"], r["nid"]) for r in runs] == [
                ("sample-in-a", "written-by-server")
            ]

            口 = Step(step_num=5, name="Loading another project is refused",
                     purpose="POST /api/wfc/load naming B gets a client error "
                             "that names the served project and says to start "
                             "a canvas in B",
                     outputs="409 with a message naming A")
            refused = client.post("/api/wfc/load", json={"project_root": str(b)})
            assert refused.status_code == 409, refused.text
            detail = refused.json()["detail"]
            assert str(a) in detail and "start a canvas there" in detail

            口 = Step(step_num=6, name="The refused call changed nothing",
                     purpose="The provider still reads A, and a second server "
                             "write still lands in A")
            after = client.get("/api/wfc/runs").json()
            assert [r["dataSource"] for r in after] == ["sample-in-a"]
            again = client.patch("/api/wfc/run/1", json={"nid": "written-after-refusal"})
            assert again.status_code == 200, again.text

            口 = Step(step_num=7, name="The served project reloads by any spelling",
                     purpose="A relative path with a trailing separator, in "
                             "another case on Windows, names A and reloads it",
                     critical="Compared as resolved paths, so a spelling of the "
                              "served project is never refused as another one")
            spelled = os.path.relpath(a, b) + os.sep
            if os.name == "nt":
                spelled = spelled.swapcase()
            reloaded = client.post("/api/wfc/load", json={"project_root": spelled})
            assert reloaded.status_code == 200, reloaded.text
            assert reloaded.json()["runs"] == 1
    finally:
        reset_engine()

    口 = Step(step_num=8, name="Every write landed in A's database file",
             purpose="Read the files directly: A holds the last write and B's "
                     "file is byte for byte what it was before the launch",
             critical="Compared on disk, so two engines that agreed with each "
                      "other on the wrong file would still fail")
    assert _nid_in(a) == "written-after-refusal"
    assert (b / ".wfc" / "wfc.db").read_bytes() == b_before
