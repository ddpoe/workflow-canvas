"""The one root-resolution rule.

Catalog rows ``root-env-override``, ``root-upward-walk`` and
``root-no-marker`` of ``docs/system/layout.json``: an environment override
validated against the config marker, else an upward walk from the
invocation directory to the nearest marker, else a hard error naming the
marker. Never a silent fallback to the current directory — a wrong-but-
plausible root is this unit's worst failure mode.

The environment value and the working directory are handed in by the
caller; checking whether the marker exists is the one read this module
makes. Writes never happen here: a caller that must migrate an older tree
before the marker check can see it does so through ``on_candidate``.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

from .tree import marker_path, marker_relpath

#: The environment variable that overrides the walk.
ROOT_ENV_VAR = "WFC_PROJECT_ROOT"


def root_candidates(start: Path) -> Iterator[Path]:
    """Yield the upward walk: ``start`` itself, then each parent to the top.

    Args:
        start: The resolved directory the walk begins at.

    Yields:
        Each candidate root in walk order.
    """
    start = Path(start)
    yield start
    yield from start.parents


def is_project_root(candidate: Path) -> bool:
    """Return whether ``candidate`` carries the config marker."""
    return marker_path(candidate).exists()


def resolve_project_root(
    env_override: str | None,
    cwd: Path,
    *,
    on_candidate: Callable[[Path], None] | None = None,
) -> Path:
    """Resolve the project root from an override and a working directory.

    Args:
        env_override: The value of :data:`ROOT_ENV_VAR`, or ``None`` / empty
            when unset. When set it must carry the marker; no walking.
        cwd: The working directory the walk starts from when no override
            is set.
        on_candidate: Optional hook called with each directory just before
            its marker check — the override itself, or each walk candidate
            in order. The caller's place for a migration that must run
            before the marker can be seen; Layout itself writes nothing.

    Returns:
        The resolved root.

    Raises:
        RuntimeError: When the override lacks the marker, or the walk
            reaches the top without finding one. The message names the
            marker and the override variable.
    """
    if env_override:
        root = Path(env_override).resolve()
        if on_candidate is not None:
            on_candidate(root)
        if not is_project_root(root):
            raise RuntimeError(
                f"{ROOT_ENV_VAR}={root} does not contain {marker_relpath()} — "
                f"not a workflow-canvas project"
            )
        return root

    start = Path(cwd).resolve()
    for candidate in root_candidates(start):
        if on_candidate is not None:
            on_candidate(candidate)
        if is_project_root(candidate):
            return candidate

    raise RuntimeError(
        f"Could not resolve workflow-canvas project root from cwd={start}. "
        f"Set {ROOT_ENV_VAR} or run wfc from a directory inside a project "
        f"(one containing {marker_relpath()})."
    )
