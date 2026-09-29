"""``wfc demo`` preflight safety: a failed scaffold changes NOTHING.

Every fallible check (initialised project, Docker, existing demo) runs
before the first state change — these tests assert on the ABSENCE of state
(byte-for-byte identical directory tree), not just the exit code.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import (
    redirect_home,
    stub_demo_docker_probe,
    stub_server_bind,
)
from wfc.cli import cli_main
from wfc.init import init_project


def _tree_snapshot(root: Path) -> dict[str, str]:
    """Map every file under *root* (relative path) to its sha256."""
    snap: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root))] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
        elif p.is_dir():
            snap[str(p.relative_to(root)) + "/"] = "<dir>"
    return snap


@workflow(purpose="wfc demo in an uninitialised directory exits non-zero and "
                  "creates nothing")
def test_demo_uninitialised_dir_changes_nothing(tmp_path):
    target = tmp_path / "not-a-project"
    target.mkdir()
    (target / "user-file.txt").write_text("mine")
    before = _tree_snapshot(target)

    rc = cli_main(["demo", "--dir", str(target), "--no-open"])

    assert rc != 0
    assert _tree_snapshot(target) == before


@workflow(purpose="wfc demo scaffolded via the real init path: the real DVC "
                  "gate passes, the stubbed Docker probe fails, and the "
                  "initialised project is left byte-for-byte unchanged")
def test_demo_docker_down_changes_nothing(tmp_path, monkeypatch, capsys):
    # Scaffold a genuine project through the production init path.  Redirecting
    # HOME lands the default DVC archive under tmp (outside the project tree),
    # so init leaves the project DVC-ready ([dvc] url + .dvc/config + cache)
    # and git-committed.  The demo scaffold checks DVC (scaffold order: DVC
    # gate, then Docker), so the real DVC gate passes here and the stubbed
    # Docker probe is the check that fails — exercising that real ordering
    # rather than no-opping the DVC gate.
    redirect_home(monkeypatch, tmp_path / "home")
    target = tmp_path / "proj"
    init_project(target, assume_yes=True)

    # check_docker probes the docker daemon — a true external edge — so stub
    # it to report the daemon down.  The DVC gate is left real and must pass.
    stub_demo_docker_probe(monkeypatch, "fail")

    # Snapshot AFTER init scaffolds — the "no state change" claim is about the
    # demo command, not init.  Drain init's health-summary output first.
    capsys.readouterr()
    before = _tree_snapshot(target)
    rc = cli_main(["demo", "--dir", str(target), "--no-open"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "Docker" in err
    assert _tree_snapshot(target) == before


@workflow(purpose="wfc demo refuses while DATABASE_URL names another project's "
                  "database: it exits non-zero naming both databases, before "
                  "any scaffolding work, and never starts the canvas; an "
                  "override naming the demo's own database, in another "
                  "spelling, passes the check")
def test_demo_refuses_a_database_override_naming_another_project(
    tmp_path, monkeypatch, capsys
):
    # A genuine project, as in the Docker test above: HOME is redirected so
    # init's default DVC archive lands outside the project tree.
    redirect_home(monkeypatch, tmp_path / "home")
    target = tmp_path / "proj"
    init_project(target, assume_yes=True)

    from wfc import layout

    # The launching shell names another project's database. The runs the demo
    # canvas starts would inherit it and record there, while the canvas reads
    # the demo project's database.
    monkeypatch.setenv("DATABASE_URL", layout.database_url(tmp_path / "other"))

    # Record whether scaffolding or serving began. The Docker probe is the
    # last preflight before scaffolding work; it reports the daemon down so an
    # unguarded run stops there instead of building an image.
    reached: list[str] = []
    served: list = []
    stub_demo_docker_probe(monkeypatch, "fail", reached=reached)
    stub_server_bind(monkeypatch, started=served, where="demo")

    capsys.readouterr()
    before = _tree_snapshot(target)
    rc = cli_main(["demo", "--dir", str(target), "--no-open"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "DATABASE_URL" in err
    assert str(layout.db_path(target.resolve())) in err
    assert reached == []
    assert served == []
    assert _tree_snapshot(target) == before

    # The pass branch: an override naming this project's own database, spelled
    # differently from layout.database_url (driver, a '..' segment, POSIX
    # slashes), is not refused. The run carries on to the Docker preflight.
    db = layout.db_path(target.resolve())
    own = ("sqlite+pysqlite:///"
           + (db.parent / ".." / db.parent.name / db.name).as_posix())
    assert own != layout.database_url(target)
    monkeypatch.setenv("DATABASE_URL", own)
    rc = cli_main(["demo", "--dir", str(target), "--no-open"])

    assert rc != 0
    assert "DATABASE_URL" not in capsys.readouterr().err
    assert reached == ["docker preflight"]
    assert served == []
    assert _tree_snapshot(target) == before
