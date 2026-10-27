"""The mount table and host-to-container translation.

Catalog rows 13 (mount table), ``host-container-translation`` and
``script-in-container`` of ``docs/system/layout.json``. The table is data:
the complete host-to-container association, in the order the engines bind
it. Translation is one total function over the table — resolved-prefix
match on path segments against each mounted root, identity for everything
outside it — and both argv builders render their bind flags from the same
rows, so a mount added here is picked up everywhere without edits of its
own.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The project root's mount root inside the container — also its workdir.
WORK_MOUNT = "/work"
#: The DVC cache's mount root inside the container.
DVC_CACHE_MOUNT = "/dvc-cache"
#: The container's working directory: the mounted project root.
CONTAINER_WORKDIR = WORK_MOUNT


@dataclass(frozen=True)
class Mount:
    """One row of the mount table: a host directory bound at a container path.

    Attributes:
        host: The host directory, as given by the caller (not yet resolved).
        container: The fixed in-container path it is bound to.
        read_only: Whether the bind is read-only (a run's code copy is).
    """

    host: Path
    container: str
    read_only: bool = False

    @property
    def host_posix(self) -> str:
        """The host side resolved and rendered POSIX — the form bind specs carry."""
        return Path(self.host).resolve().as_posix()


#: The bind-spec option both engines read as a read-only bind.
READ_ONLY_BIND_OPTION = "ro"


def mount_table(project_root: str | Path, dvc_cache_dir: str | Path,
                code_copy: str | Path | None = None) -> tuple[Mount, ...]:
    """Return the complete mount table for a run.

    With a code copy the table gains one last row: the copy, bound
    read-only at the container path translation already gives it beneath
    ``/work``. It is a nested bind over part of the project row, so the
    script path the container sees keeps its form and translation stays the
    one function over the table; only what the run may write there changes.

    Args:
        project_root: Host path of the project tree, bound at ``/work``.
        dvc_cache_dir: Host path of the DVC cache, bound at ``/dvc-cache``.
        code_copy: Host path of the run's extracted code copy, which lies
            under the project root; ``None`` for a table without one.

    Returns:
        The rows in bind order: the project root, the cache, then the code
        copy when one is given.

    Raises:
        ValueError: The code copy is not under the project root, so it has
            no container path beneath ``/work``.
    """
    base = (
        Mount(Path(project_root), WORK_MOUNT),
        Mount(Path(dvc_cache_dir), DVC_CACHE_MOUNT),
    )
    if code_copy is None:
        return base
    container = translate_host_path(str(code_copy), base[:1])
    if container == str(code_copy):
        raise ValueError(
            f"The code copy {code_copy} is not under the project root "
            f"{project_root}, so it has no container path beneath {WORK_MOUNT}."
        )
    return base + (Mount(Path(code_copy), container, read_only=True),)


def bind_spec(mount: Mount) -> str:
    """Render one row as the ``<host>:<container>[:ro]`` spec both engines accept.

    The host side is POSIX-normalized: Docker Desktop on Windows and stock
    Docker on Linux both accept forward-slash paths in bind specs, and raw
    backslashes confuse the parser. Apptainer follows the same convention,
    including the trailing ``:ro`` option of a read-only row.

    Args:
        mount: The table row.

    Returns:
        The bind spec string.
    """
    spec = f"{mount.host_posix}:{mount.container}"
    return f"{spec}:{READ_ONLY_BIND_OPTION}" if mount.read_only else spec


def translate_host_path(host_path: str, mounts: tuple[Mount, ...]) -> str:
    """Rewrite a host path to its in-container equivalent over the table.

    The path is considered on its resolved POSIX form. Equal to a mounted
    root maps to that root's container path; under it (on a segment
    boundary — a sibling whose name merely starts with the root's name does
    not match) maps to ``<container>/<rel>``; anything else, including an
    input that cannot be resolved, is returned unchanged. Output is always
    POSIX, even on a Windows host.

    Args:
        host_path: A host path, absolute or otherwise.
        mounts: The mount table.

    Returns:
        The container path, or ``host_path`` itself when the table does not
        cover it.
    """
    try:
        p = Path(host_path).resolve().as_posix()
    except (OSError, ValueError):
        return host_path
    for mount in mounts:
        root = mount.host_posix
        if p == root:
            return mount.container
        if p.startswith(root + "/"):
            return mount.container + "/" + p[len(root) + 1:]
    return host_path
