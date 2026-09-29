"""Project roots: the out-of-tree locations and the scaffolded project.

Pins / does not prove lines are on each builder's docstring.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def project_archive_dir(project_dir: Path) -> Path:
    """Return the DVC archive location for a test project, outside its tree.

    ``init_project`` defaults the archive to ``~/.wfc/archives/<project>`` and
    ``init_dvc`` pre-creates it, so a test that does not name one writes into
    the developer's home directory and never cleans up. A sibling of the
    project directory is out of the tree (which ``_validate_remote_containment``
    requires) and inside the pytest temp root, so it goes away with everything
    else pytest made.

    Pins: the location only -- a sibling of the project directory, inside
    pytest's temp root. An input, not state.
    Does not prove: anything about the archive's contents; ``init_dvc``
    creates it and the archive pass fills it.

    Args:
        project_dir: The project root the archive belongs to.

    Returns:
        The archive directory path. It is not created here — ``init_dvc`` does
        that.
    """
    project_dir = Path(project_dir).resolve()
    return project_dir.parent / f"{project_dir.name}-archive"


def sample_source_dir(project_dir: Path) -> Path:
    """Return where a test stages the *source* files it registers as samples.

    Registration materializes nothing under ``data/samples/`` — it caches the
    source's bytes and records where ``restore_sample`` will later put them.
    A fixture that registers a file already sitting at its own
    ``registered_path`` makes every restore the dest-already-valid skip, which
    is a project state no user can produce. Staging outside the project keeps
    ``registered_path != source_path`` so the restore is a real copy.

    Pins: the location only -- a sibling of the project directory, inside
    pytest's temp root. An input, not state.
    Does not prove: anything about registration; it is where a caller
    stages the file :func:`register_sample_row` then registers.

    Args:
        project_dir: The project root the sources will be registered into.

    Returns:
        A directory beside the project, outside its tree.
    """
    project_dir = Path(project_dir).resolve()
    return project_dir.parent / f"{project_dir.name}-sources"


def init_test_project(project_dir: Path, **kwargs) -> dict[str, bool]:
    """Scaffold a test project through production ``init_project``.

    The one route a test fixture should take to a project root: whatever
    ``wfc init`` lays down, a test gets — including the ``[dvc]`` section
    ``ensure_dvc_ready`` demands, without which ``register_sample`` cannot be
    called at all.

    Two things are pinned that a real ``wfc init`` resolves interactively:

    - The archive is named explicitly (:func:`project_archive_dir`) so nothing
      lands under ``~/.wfc/archives/``.
    - The readiness probes are stubbed for the duration of the call. Step 7's
      health summary shells out to ``docker info`` with a 15-second timeout,
      which a per-test fixture must not pay. The stub is scoped to this call
      and does not leak into the test.

    Idempotent, because ``init_project`` is: a project that already has a
    config keeps it verbatim and no archive is resolved.

    Pins: the archive beside the project, never under ``~``; ``assume_yes``;
    the readiness probes stubbed for the duration of the init call only, to
    skip a 15-second ``docker info``.
    Does not prove: init's health summary.

    Args:
        project_dir: Project root to scaffold (created when missing).
        **kwargs: Passed through to :func:`wfc.init.init_project`.

    Returns:
        ``init_project``'s map of resource name to created-now flag.
    """
    from wfc.init import init_project
    from tests.fixtures.fakes import stub_readiness_probes

    project_dir = Path(project_dir).resolve()
    with pytest.MonkeyPatch.context() as mp:
        stub_readiness_probes(mp)
        return init_project(
            project_dir,
            archive=str(project_archive_dir(project_dir)),
            assume_yes=True,
            **kwargs,
        )
