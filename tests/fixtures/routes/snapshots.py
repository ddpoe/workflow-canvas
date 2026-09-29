"""The module-scoped project snapshot: build once, restore per test.

Pins / does not prove lines are on each builder's docstring.
"""

from __future__ import annotations

import os
import shutil
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
    """

    project: "Project"
    database_url: str
    db_path: Path
    snapshot: Path


def build_project_snapshot(scenario: "Scenario", root: Path) -> ProjectSnapshot:
    """Build a scenario's project once and copy its database aside.

    The module-scoped half of the build-once, restore-per-test shape: a
    module that pays the build once calls this from a ``scope="module"``
    fixture and hands the result to :func:`restore_project_snapshot` in a
    per-test fixture. The build runs under its own ``MonkeyPatch``
    context, which closes before returning -- building the project is what
    needs the environment pinned, not every test in the module.

    Pins: everything ``build_project`` pins, for the duration of the build
    only; the snapshot is a byte copy of the SQLite file.
    Does not prove: anything about the working tree between tests -- only
    the database is restored, so a test that writes under ``.runs/`` leaves
    that for the next test in the module.

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
    return ProjectSnapshot(project=project, database_url=url, db_path=db_path,
                           snapshot=snapshot)


def restore_project_snapshot(snapshot: ProjectSnapshot, monkeypatch) -> "Project":
    """Restore the pristine database and pin the built project for one test.

    Per-test isolation is snapshot-restore of the SQLite file: the cached
    production engine is dropped first (its pooled connection would
    otherwise hold the file open on Windows), the post-build copy is
    written back over the live file, and cwd, ``WFC_PROJECT_ROOT`` and
    ``DATABASE_URL`` are re-pinned to the built project through this
    test's ``monkeypatch``.

    Pins: cwd and the ``WFC_*`` environment; the database's contents as
    the build left them.
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
    shutil.copyfile(snapshot.snapshot, snapshot.db_path)
    monkeypatch.setenv("DATABASE_URL", snapshot.database_url)
    monkeypatch.setenv(layout.ROOT_ENV_VAR, str(snapshot.project.root))
    monkeypatch.chdir(snapshot.project.root)
    reset_engine()
    return snapshot.project
