"""The containers wfc starts: their labels, and listing and stopping them by label.

Every container wfc starts carries labels that say whose it is:

- ``wfc.project`` (:data:`PROJECT_LABEL`): a short hash of the resolved
  project root, computed in one place, :func:`project_label`. Every step
  container and every dev-loop container (``wfc shell``, ``wfc exec``,
  ``wfc jupyter``) carries it.
- ``wfc.pipeline`` (:data:`PIPELINE_LABEL`) and ``wfc.run``
  (:data:`RUN_LABEL`): the pipeline and the run a step container belongs
  to, recorded or draft. A dev-loop container carries neither.
- ``wfc.creator`` (:data:`CREATOR_LABEL`): the wfc process that created a
  container it never starts (the solve build's copy-out), as
  ``<pid>:<creation time>``. Only that container carries it, with the
  project label (:func:`copy_out_labels`).

:func:`container_labels` builds the label set a launch hands
:func:`wfc.environments.argv.build_docker_command`. The labels are what a
stop finds a container by, and what ``wfc prune-envs`` places a container
that is not running by; they never enter a cache key, a fingerprint or a
run's invocation record.

:func:`list_containers` names the running containers that carry every label
asked for, and :func:`stop_containers` kills them: bounded (each Docker call
by :data:`CONTAINER_QUERY_TIMEOUT_S`) and best-effort (a failure is reported
in the result, never raised). A stop always names the project label, so a
run or pipeline id another project shares never reaches that project's
containers. Messages never name a Docker command.

This module owns the labels, the one Docker edge of a listing or a stop
(``_docker``) and the stop by label. What an earlier crash left belongs to
:mod:`wfc.environments.leftovers`, and the build clients it finds to
:mod:`wfc.environments.build_clients`. Neither is imported here.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from axiom_annotations import task

#: The label naming the project a container was started for.
PROJECT_LABEL: Final[str] = "wfc.project"
#: The label naming the pipeline a step container belongs to.
PIPELINE_LABEL: Final[str] = "wfc.pipeline"
#: The label naming the run (recorded or draft) a step container runs.
RUN_LABEL: Final[str] = "wfc.run"
#: The label naming the wfc process that created a never-started container.
CREATOR_LABEL: Final[str] = "wfc.creator"

#: Hex characters of the project root's hash the project label keeps.
_PROJECT_HASH_CHARS: Final[int] = 12

#: Seconds each Docker call of a listing or a stop may take.
CONTAINER_QUERY_TIMEOUT_S: Final[float] = 15.0


class ContainerQueryError(RuntimeError):
    """Docker could not say which containers carry a label set."""


@dataclass(frozen=True)
class ContainerStop:
    """What a stop by label did.

    Attributes:
        stopped: The ids of the containers killed.
        error: Why the stop could not finish, in words that name no Docker
            command; ``None`` when it did.
    """

    stopped: tuple[str, ...] = ()
    error: str | None = None


def project_label(project_root: str | Path) -> str:
    """Return the project label's value: a short hash of the resolved project root.

    The one place the hash is computed. The root is resolved and its case
    folded where the file system folds it, so two spellings of one project
    give one label.

    Args:
        project_root: The project root.

    Returns:
        The first :data:`_PROJECT_HASH_CHARS` hex characters of the SHA-256
        of the resolved root.
    """
    resolved = os.path.normcase(str(Path(project_root).resolve()))
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:_PROJECT_HASH_CHARS]


def container_labels(project_root: str | Path, *, pipeline_id: str | None = None,
                     run_id: str | None = None) -> dict[str, str]:
    """Return the labels a container wfc starts carries.

    Args:
        project_root: The project root (the project label).
        pipeline_id: The step's pipeline, or ``None`` for a container no
            pipeline owns (the dev loop).
        run_id: The step's run (a run id or a draft ref), or ``None``.

    Returns:
        ``{label: value}``: the project label, then the pipeline and run
        labels given.
    """
    labels = {PROJECT_LABEL: project_label(project_root)}
    if pipeline_id:
        labels[PIPELINE_LABEL] = str(pipeline_id)
    if run_id:
        labels[RUN_LABEL] = str(run_id)
    return labels


def creator_label(pid: int, created: float) -> str:
    """Return the creator label's value for the process *pid* created at *created*.

    Args:
        pid: The process id.
        created: Its creation time (seconds since the epoch).

    Returns:
        ``<pid>:<creation time>``, the time to the millisecond.
    """
    return f"{int(pid)}:{created:.3f}"


def parse_creator_label(value: str) -> tuple[int, float] | None:
    """Read a creator label's value back as ``(pid, creation time)``.

    Args:
        value: The label's value.

    Returns:
        The pid and creation time, or ``None`` when *value* is not one.
    """
    pid, sep, created = (value or "").strip().partition(":")
    if not sep:
        return None
    try:
        return int(pid), float(created)
    except ValueError:
        return None


def copy_out_labels(project_root: str | Path) -> dict[str, str]:
    """Return the labels of a never-started container this wfc process creates.

    The project label and the creator label naming this process (its pid
    and creation time), so ``wfc prune-envs`` can tell a container this
    process still holds from one a closed terminal or a crash left.

    Args:
        project_root: The project root (the project label).

    Returns:
        ``{label: value}``.
    """
    import psutil

    created = psutil.Process(os.getpid()).create_time()
    return {PROJECT_LABEL: project_label(project_root),
            CREATOR_LABEL: creator_label(os.getpid(), created)}


def _docker(args: Sequence[str], *, what: str):
    """Run one Docker call of a listing or a stop: the docker edge.

    Args:
        args: The arguments after ``docker``.
        what: How the call is named if it times out.

    Returns:
        The completed process, whatever its exit status.

    Raises:
        DockerTimeoutError: The call did not finish within
            :data:`CONTAINER_QUERY_TIMEOUT_S`.
        OSError: The Docker client could not be started.
    """
    from .docker import _run_docker

    return _run_docker(list(args), timeout=CONTAINER_QUERY_TIMEOUT_S, what=what)


def _first_line(text: str | None) -> str:
    """The first non-blank line of *text*, stripped."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def list_containers(labels: Mapping[str, str]) -> list[str]:
    """Return the ids of the running containers carrying every label in *labels*.

    Args:
        labels: ``{label: value}``; a container must carry all of them.

    Returns:
        The container ids, in Docker's order.

    Raises:
        ContainerQueryError: Docker did not answer, could not be started or
            refused the listing.
    """
    from .docker import DockerTimeoutError

    args = ["ps", "--quiet", "--no-trunc"]
    for name, value in labels.items():
        args.extend(["--filter", f"label={name}={value}"])
    try:
        proc = _docker(args, what="listing wfc's containers")
    except DockerTimeoutError as exc:
        raise ContainerQueryError(str(exc)) from exc
    except OSError as exc:
        raise ContainerQueryError(f"Docker could not be started: {exc}") from exc
    if proc.returncode != 0:
        why = _first_line(proc.stderr) or f"exit status {proc.returncode}"
        raise ContainerQueryError(f"Docker could not list wfc's containers: {why}")
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


@task(purpose="Kill every running container that carries all the labels given "
              "(always including the project label): list them by label, kill "
              "them, and report what was stopped or why the stop could not "
              "finish",
      inputs="the label set (project label, plus a pipeline or run label)",
      outputs="ContainerStop: the ids killed and an error or None",
      critical="best-effort and bounded: never raises, each Docker call is "
               "bounded by CONTAINER_QUERY_TIMEOUT_S; never stops a container "
               "without this project's label; its error names no Docker command")
def stop_containers(labels: Mapping[str, str]) -> ContainerStop:
    """Kill the running containers carrying every label in *labels*.

    A container that exits on its own between the listing and the kill
    counts as stopped.

    Args:
        labels: ``{label: value}``; must include :data:`PROJECT_LABEL`.

    Returns:
        The ids killed, and an error when the stop could not finish.
    """
    if PROJECT_LABEL not in labels:
        return ContainerStop(error="a stop must name the project's label")
    try:
        running = list_containers(labels)
    except ContainerQueryError as exc:
        return ContainerStop(error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a stop never raises
        return ContainerStop(error=f"listing wfc's containers failed: {exc}")
    if not running:
        return ContainerStop()
    from .docker import DockerTimeoutError

    try:
        proc = _docker(["kill", *running], what="stopping wfc's containers")
    except DockerTimeoutError as exc:
        return ContainerStop(error=str(exc))
    except OSError as exc:
        return ContainerStop(error=f"Docker could not be started: {exc}")
    except Exception as exc:  # noqa: BLE001 - a stop never raises
        return ContainerStop(error=f"stopping wfc's containers failed: {exc}")
    if proc.returncode == 0:
        return ContainerStop(stopped=tuple(running))
    # Some may have exited on their own; only one still running is a failure.
    try:
        left = set(list_containers(labels)) & set(running)
    except Exception as exc:  # noqa: BLE001 - a stop never raises
        return ContainerStop(error=str(exc))
    if not left:
        return ContainerStop(stopped=tuple(running))
    why = _first_line(proc.stderr) or f"exit status {proc.returncode}"
    return ContainerStop(stopped=tuple(c for c in running if c not in left),
                         error=f"Docker could not stop {len(left)} of wfc's "
                               f"containers: {why}")
