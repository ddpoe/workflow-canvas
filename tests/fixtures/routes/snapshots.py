"""The module-scoped project snapshot: build once, restore per test.

Pins / does not prove lines are on each builder's docstring.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:  # pragma: no cover - typing only; the harness imports this module
    from tests.harness import Project, Scenario


@dataclass
class ProjectSnapshot:
    """A project built once, with a pristine copy of its database aside.

    Attributes:
        project: The built project handle.
        database_url: The ``DATABASE_URL`` the build pinned.
        db_path: The live database file.
        snapshot: The pristine copy taken right after the build.
        commit: The project's ``HEAD`` right after the build -- the commit
            the database's registered versions point at.
    """

    project: "Project"
    database_url: str
    db_path: Path
    snapshot: Path
    commit: str


def build_project_snapshot(scenario: "Scenario", root: Path) -> ProjectSnapshot:
    """Build a scenario's project once and copy its database aside.

    The module-scoped half of the build-once, restore-per-test shape: a
    module that pays the build once calls this from a ``scope="module"``
    fixture and hands the result to :func:`restore_project_snapshot` in a
    per-test fixture. The build runs under its own ``MonkeyPatch``
    context, which closes before returning -- building the project is what
    needs the environment pinned, not every test in the module.

    Pins: everything ``build_project`` pins, for the duration of the build
    only; the snapshot is a byte copy of the SQLite file plus the ``HEAD``
    commit the build left, so a restore puts the database and the committed
    tree (the registered snapshots under ``.wfc/registry/``) back together.
    Does not prove: anything about untracked state outside ``.wfc/registry/``
    between tests -- a test that writes under ``.wfc/runs/`` leaves that for
    the next test in the module.

    Args:
        scenario: The declaration to build.
        root: Directory to build into (typically ``tmp_path_factory.mktemp``).

    Returns:
        The built project and where its pristine database copy is.
    """
    from tests.harness import build_project
    from wfc import layout
    from wfc.persistence import reset_engine

    root = Path(root).resolve()
    with pytest.MonkeyPatch.context() as mp:
        project = build_project(scenario, root=root, monkeypatch=mp)
        url = os.environ["DATABASE_URL"]
        db_path = layout.db_path(root)
        reset_engine()
        snapshot = root.parent / f"{root.name}.wfc.db.snapshot"
        shutil.copyfile(db_path, snapshot)
    commit = _git(root, "rev-parse", "HEAD").strip()
    return ProjectSnapshot(project=project, database_url=url, db_path=db_path,
                           snapshot=snapshot, commit=commit)


def _git(root: Path, *args: str) -> str:
    """Run one git command in ``root`` and return its stdout.

    Args:
        root: The project root (a git repository).
        *args: The git arguments.

    Returns:
        The command's stdout.
    """
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def restore_project_snapshot(snapshot: ProjectSnapshot, monkeypatch) -> "Project":
    """Restore the pristine database and pin the built project for one test.

    Per-test isolation is snapshot-restore of the SQLite file and of the
    commit it describes: the cached production engine is dropped first (its
    pooled connection would otherwise hold the file open on Windows), the
    tree is reset to the build's ``HEAD`` and untracked files under
    ``.wfc/registry/`` are removed (an earlier test that re-registered
    changed code moved ``HEAD`` to a registration commit the restored
    database has never heard of), the post-build copy is written back over
    the live file, and cwd, ``WFC_PROJECT_ROOT`` and ``DATABASE_URL`` are
    re-pinned to the built project through this test's ``monkeypatch``.

    Pins: cwd and the ``WFC_*`` environment; the database's contents and
    the committed tree as the build left them.
    Does not prove: see :func:`build_project_snapshot`.

    Args:
        snapshot: What :func:`build_project_snapshot` returned.
        monkeypatch: The test's ``MonkeyPatch``.

    Returns:
        The built project handle, pinned.
    """
    from wfc import layout
    from wfc.persistence import reset_engine

    reset_engine()
    root = snapshot.project.root
    _git(root, "reset", "--hard", "-q", snapshot.commit)
    registry = layout.registry_dir(root).relative_to(root).as_posix()
    _git(root, "clean", "-fdq", "--", registry)
    shutil.copyfile(snapshot.snapshot, snapshot.db_path)
    monkeypatch.setenv("DATABASE_URL", snapshot.database_url)
    monkeypatch.setenv(layout.ROOT_ENV_VAR, str(snapshot.project.root))
    monkeypatch.chdir(snapshot.project.root)
    reset_engine()
    return snapshot.project
