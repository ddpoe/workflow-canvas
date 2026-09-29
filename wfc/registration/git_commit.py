"""The registration git commit: commit what a method registration wrote, by
pathspec, so the method's code version is in git before a run's claim reads it
and nothing the user staged is swept in."""

from __future__ import annotations

from pathlib import Path

from axiom_annotations import task

from ..persistence import project_root as get_project_root
from ..version import commit_paths


@task(
    purpose="Commit the method's source snapshot, and its source directory when "
            "that is inside the repository, by pathspec, refusing a project "
            "that is not a git repository",
    inputs="method directory, snapshot directory, method name, module name, "
           "optional project root",
    outputs="the new commit's SHA, or None when there is nothing new to commit",
    critical="Runs after the snapshot is written, so the commit holds it. "
             "Only the named paths are committed; other staged entries stay "
             "staged and uncommitted.",
)
def _git_commit_registration(
    method_dir: Path,
    snapshot_dir: Path,
    method_name: str,
    module_name: str,
    project_root: Path | None = None,
) -> str | None:
    """Commit the registered method to the project git repo.

    Commits the snapshot under ``methods/<name>/`` and, when the source
    directory is inside the repository, the source directory too. A source
    outside the repository is not committed: the snapshot is the registered
    copy. Uses :func:`wfc.version.commit_paths`, which names its paths and
    never sweeps in anything else the user staged.

    Args:
        method_dir: The directory the method was registered from.
        snapshot_dir: The method's snapshot directory, ``methods/<name>/``.
        method_name: Method name (used in the commit message).
        module_name: Module name (used in the commit message).
        project_root: Root of the project git repo. Defaults to the resolved
            project root.

    Returns:
        The new commit's SHA, or ``None`` when there is nothing new to
        commit.

    Raises:
        RuntimeError: If the project is not a git repository, or if
            ``git add`` or ``git commit`` fails.
    """
    root = Path(project_root) if project_root is not None else get_project_root()
    sha = commit_paths(
        root,
        [snapshot_dir, method_dir],
        f"Register method {module_name}/{method_name}",
    )
    if sha is None:
        print(f"  git: no changes to commit for '{method_name}'")
    else:
        print(f"  git: committed as {sha[:12]}")
    return sha
