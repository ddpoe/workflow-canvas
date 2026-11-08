"""The container bind check: does a step's container see the project and the cache?

A project on a network drive (or any host path Docker Desktop cannot share)
is bound into a step's container as an empty folder, and the step then fails
on a missing script. Before a launch records anything, Execution asks this
module to start one short container from the image the launch will run,
with the binds a step gets, and look:

- the project row: is the project's config file (``.wfc/wf-canvas.toml``,
  through Layout, translated to its container path) there, at its host
  size?
- the cache row: does ``/dvc-cache`` hold entries when the host's cache
  does (both empty passes)?

The binds are Layout's mount table rendered through
:func:`wfc.environments.argv._rendered_mounts` and
:func:`~wfc.environments.argv.docker_bind_flags`, exactly as a step's
``docker run`` renders them, with no code-copy row. A row whose host path
does not exist is left out (Docker Desktop would create it), so the check
never creates or writes anything: the probe only reads, the root
filesystem is read-only and the container has no network. The run is
bounded by :data:`BIND_CHECK_TIMEOUT_S`; a container the timeout leaves
behind is removed by its name.

The verdict is one :class:`BindCheck`: ``visible``, ``empty`` (naming the
bind and its host path) or ``not-run`` (the image has no shell, Docker
refused the run, the timeout passed). The host-side values the container's
view is compared with are measured by :func:`host_bind_expectations` and
passed in.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from axiom_annotations import task

from .. import layout
from .argv import _rendered_mounts, docker_bind_flags

#: Both binds show what the host has.
BIND_VISIBLE: Final[str] = "visible"
#: A bind came up empty (or stale) inside the container.
BIND_EMPTY: Final[str] = "empty"
#: The check could not run; nothing is known about the binds.
BIND_NOT_RUN: Final[str] = "not-run"

#: Seconds the check's container may take, start to exit.
BIND_CHECK_TIMEOUT_S: Final[float] = 60.0

#: The name prefix of the check's container, so a timed-out one can be removed.
BIND_CHECK_NAME_PREFIX: Final[str] = "wfc-bind-check-"

#: The shell the probe runs under; an image without it cannot be checked.
_PROBE_SHELL: Final[str] = "/bin/sh"

#: The probe: ``$1`` is the config file's container path, ``$2`` the
#: cache's; an empty argument is a row not bound, and prints nothing.
#: Reads only: ``wc -c`` of one file and ``ls -A`` of one directory.
_PROBE_SCRIPT: Final[str] = (
    'if [ -n "$1" ]; then '
    'if [ -f "$1" ]; then echo "config $(wc -c < "$1")"; '
    'else echo "config none"; fi; fi; '
    'if [ -n "$2" ]; then '
    'if [ -d "$2" ]; then echo "cache $(ls -A "$2" | wc -l)"; '
    'else echo "cache none"; fi; fi'
)


@dataclass(frozen=True)
class BindExpectations:
    """What the host has, for the container's view to be compared with.

    Attributes:
        config_size: The project's config file's size in bytes, or ``None``
            when the host has none (the project row is then not probed).
        cache_entries: The cache directory's top-level entry count, or
            ``None`` when the host has no cache directory (the cache row is
            then not bound).
    """

    config_size: int | None
    cache_entries: int | None


@dataclass(frozen=True)
class BindCheck:
    """The bind check's verdict.

    Attributes:
        outcome: :data:`BIND_VISIBLE`, :data:`BIND_EMPTY` or
            :data:`BIND_NOT_RUN`.
        container: The container path of the bind that came up empty
            (``/work`` or ``/dvc-cache``); ``None`` otherwise.
        host: That bind's host path, POSIX; ``None`` otherwise.
        detail: For ``empty``, what the container saw there; for
            ``not-run``, why the check could not run; empty for ``visible``.
    """

    outcome: str
    container: str | None = None
    host: str | None = None
    detail: str = ""


def host_bind_expectations(project_root: str | Path,
                           dvc_cache_dir: str | Path) -> BindExpectations:
    """Measure the host side the check compares the container's view with.

    Args:
        project_root: The project root.
        dvc_cache_dir: The project's DVC cache directory.

    Returns:
        The config file's size and the cache's top-level entry count, each
        ``None`` when the host has none.
    """
    config = layout.marker_path(Path(project_root))
    try:
        config_size: int | None = config.stat().st_size if config.is_file() else None
    except OSError:
        config_size = None
    cache = Path(dvc_cache_dir)
    try:
        cache_entries: int | None = len(os.listdir(cache)) if cache.is_dir() else None
    except OSError:
        cache_entries = None
    return BindExpectations(config_size=config_size, cache_entries=cache_entries)


def _check_rows(project_root: str | Path, dvc_cache_dir: str | Path
                ) -> tuple[layout.Mount, ...]:
    """The rows a step binds, with no code copy, less any whose host path is missing.

    Args:
        project_root: The project root.
        dvc_cache_dir: The DVC cache directory.

    Returns:
        The rows to bind, in table order.
    """
    return tuple(m for m in _rendered_mounts("docker", project_root, dvc_cache_dir, None)
                 if Path(m.host).exists())


def bind_check_command(image_ref: str, project_root: str | Path,
                       dvc_cache_dir: str | Path, expected: BindExpectations, *,
                       uid: int, gid: int, name: str) -> list[str]:
    """Assemble the check's ``docker run`` argv.

    ``docker run --rm --pull never --name <name> --network none --read-only
    --user <uid>:<gid> <binds> --entrypoint /bin/sh <image> -c <probe> probe
    <config path> <cache path>``, the binds rendered as a step's are. A row
    the host lacks, or whose expectation is ``None``, is not probed (its
    path argument is empty).

    Args:
        image_ref: The image the launch will run, as Docker is handed it.
        project_root: The project root.
        dvc_cache_dir: The DVC cache directory.
        expected: The host-side values; a ``None`` one is not probed.
        uid: Host UID the container runs as, as a step's does.
        gid: Host GID, likewise.
        name: The container's name.

    Returns:
        The argv.
    """
    rows = _check_rows(project_root, dvc_cache_dir)
    bound = {m.container for m in rows}
    config = ""
    if expected.config_size is not None and layout.WORK_MOUNT in bound:
        config = layout.translate_host_path(
            str(layout.marker_path(Path(project_root))), rows)
    cache = ""
    if expected.cache_entries is not None and layout.DVC_CACHE_MOUNT in bound:
        cache = layout.DVC_CACHE_MOUNT
    return ["docker", "run", "--rm", "--pull", "never", "--name", name,
            "--network", "none", "--read-only", "--user", f"{uid}:{gid}",
            *docker_bind_flags(rows),
            "--entrypoint", _PROBE_SHELL, image_ref,
            "-c", _PROBE_SCRIPT, "probe", config, cache]


def _run_probe(argv: Sequence[str], *, timeout: float):
    """Run the check's container through Environments' docker runner.

    Args:
        argv: The full ``docker run ...`` argv.
        timeout: Seconds before the docker client is stopped.

    Returns:
        The completed process, whatever its exit status.

    Raises:
        DockerTimeoutError: The run did not finish within *timeout*.
        OSError: The docker client could not be started.
    """
    from .docker import _run_docker

    return _run_docker(list(argv[1:]), timeout=timeout, what="the bind check")


def _remove_check_container(name: str) -> None:
    """Remove a check container the timeout left behind; best-effort.

    Args:
        name: The container's name.
    """
    from .docker import DOCKER_QUERY_TIMEOUT_S, _run_docker

    try:
        _run_docker(["rm", "--force", name], timeout=DOCKER_QUERY_TIMEOUT_S)
    except (RuntimeError, OSError):
        pass


def _first_line(text: str | None) -> str:
    """The first non-blank line of *text*, stripped (docker's own words, no help hint)."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _parse_probe(stdout: str) -> dict[str, str]:
    """Read the probe's ``config <n|none>`` and ``cache <n|none>`` lines.

    Args:
        stdout: The probe's output.

    Returns:
        ``{"config": ..., "cache": ...}`` for the lines present.
    """
    seen: dict[str, str] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("config", "cache"):
            seen[parts[0]] = parts[1]
    return seen


def _verdict(seen: dict[str, str], argv: Sequence[str],
             project_root: str | Path, dvc_cache_dir: str | Path,
             expected: BindExpectations) -> BindCheck:
    """Compare the probe's view with the host's.

    Args:
        seen: The parsed probe lines.
        argv: The argv that ran (its last two arguments say what was probed).
        project_root: The project root.
        dvc_cache_dir: The DVC cache directory.
        expected: The host-side values.

    Returns:
        The verdict.
    """
    config_path, cache_path = argv[-2], argv[-1]
    project_host = Path(project_root).resolve().as_posix()
    cache_host = Path(dvc_cache_dir).resolve().as_posix()
    if config_path:
        found = seen.get("config")
        if found is None:
            return BindCheck(BIND_NOT_RUN, detail="the check printed no answer "
                                                  "for the project folder")
        if found == "none":
            return BindCheck(BIND_EMPTY, layout.WORK_MOUNT, project_host,
                             f"the container does not see {layout.marker_relpath()} "
                             f"there")
        if not found.isdigit() or int(found) != expected.config_size:
            return BindCheck(BIND_EMPTY, layout.WORK_MOUNT, project_host,
                             f"the container sees {layout.marker_relpath()} at "
                             f"{found} bytes, not the {expected.config_size} "
                             f"bytes it has on this machine")
    if cache_path:
        found = seen.get("cache")
        if found is None:
            return BindCheck(BIND_NOT_RUN, detail="the check printed no answer "
                                                  "for the DVC cache")
        if found == "none" or (found.isdigit() and int(found) == 0
                               and (expected.cache_entries or 0) > 0):
            return BindCheck(BIND_EMPTY, layout.DVC_CACHE_MOUNT, cache_host,
                             f"the container sees it empty, where it holds "
                             f"{expected.cache_entries} entries on this machine")
    if not config_path and not cache_path:
        return BindCheck(BIND_NOT_RUN, detail="neither the project's config file "
                                              "nor the DVC cache is on this machine")
    return BindCheck(BIND_VISIBLE)


@task(purpose="Start one short container from a launch's image with the binds "
              "a step gets (Layout's table through _rendered_mounts, no code "
              "copy, no row whose host path is missing) and compare what it "
              "sees with the host: the project's config file at its host size, "
              "the cache's top-level entries",
      inputs="the image ref, the project root, the DVC cache dir, the host-side "
             "expectations",
      outputs="BindCheck: visible, empty (naming the bind and its host path) "
              "or not-run",
      critical="never writes and never binds a host path that does not exist; "
               "bounded by BIND_CHECK_TIMEOUT_S; never raises")
def check_binds(image_ref: str, project_root: str | Path,
                dvc_cache_dir: str | Path, expected: BindExpectations, *,
                timeout: float = BIND_CHECK_TIMEOUT_S) -> BindCheck:
    """Check that a step's container would see the project and the cache.

    Args:
        image_ref: The image the launch will run first, as Docker is handed
            it; it must already be on this machine (``--pull never``).
        project_root: The project root.
        dvc_cache_dir: The project's DVC cache directory.
        expected: The host side, from :func:`host_bind_expectations`.
        timeout: Seconds the container may take.

    Returns:
        The verdict; ``not-run`` for anything that kept the check from
        answering.
    """
    uid = getattr(os, "getuid", lambda: 0)()
    gid = getattr(os, "getgid", lambda: 0)()
    name = BIND_CHECK_NAME_PREFIX + uuid.uuid4().hex[:12]
    argv = bind_check_command(image_ref, project_root, dvc_cache_dir, expected,
                              uid=uid, gid=gid, name=name)
    if not argv[-2] and not argv[-1]:
        return _verdict({}, argv, project_root, dvc_cache_dir, expected)
    from .docker import DockerTimeoutError

    try:
        proc = _run_probe(argv, timeout=timeout)
    except DockerTimeoutError as exc:
        _remove_check_container(name)
        return BindCheck(BIND_NOT_RUN, detail=str(exc))
    except OSError as exc:
        return BindCheck(BIND_NOT_RUN, detail=f"Docker could not be started: {exc}")
    except Exception as exc:  # noqa: BLE001 - the check never stops a launch itself
        return BindCheck(BIND_NOT_RUN, detail=f"the check failed: {exc}")
    if proc.returncode != 0:
        why = _first_line(proc.stderr) or f"exit status {proc.returncode}"
        return BindCheck(BIND_NOT_RUN, detail=why)
    return _verdict(_parse_probe(proc.stdout or ""), argv, project_root,
                    dvc_cache_dir, expected)
