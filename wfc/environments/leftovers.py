"""What an earlier crash left: ``wfc prune-envs``' plan and apply, and the counts.

For one project only:

- **Leftover run containers**: running containers carrying this project's
  label and a run label whose run the caller says is no longer running, and
  running unlabelled containers whose ``WFC_PIPELINE_ID`` the caller says is
  this project's and whose run is no longer running. A dev-loop container
  (project label, no run label) is never one. The caller may also say which
  pipelines have crashed (the wfc that ran them is gone while their record
  still says running) and which runs a gone ``wfc try`` left: the plan lists
  their containers under lines of their own, a crashed pipeline's naming
  ``wfc cancel <id>``, and the apply takes the caller's second look before
  it re-checks a crashed pipeline's container.
- **Unfinished containers to remove** (:class:`UnfinishedContainer`):
  containers carrying this project's label that are not running (created
  and never started, exited or dead) when what made them is gone: a run or
  pipeline label whose run the caller says has ended, a creator label
  (``wfc.creator``, the solve build's copy-out) whose process is gone or
  reused, or the project label alone. An unreadable creator label counts as
  alive.
- **Orphaned env builds**: found from processes by
  :func:`wfc.environments.build_clients.orphaned_builds`.
- **Counted, never removed**: the ``local/<name>`` images of this project's
  envs that neither an env's record nor a method version names, and the
  build cache's total size.

:func:`plan_prune` finds them and writes nothing (:class:`PrunePlan`, which
words itself); :func:`apply_prune` re-checks each item and stops it: a
container by a kill (by label when it carries one, through
:func:`wfc.environments.containers.stop_containers`), an unfinished
container by a plain remove, never a forced one (one that started since the
plan is refused and reported, left alone), an orphaned build
through :func:`wfc.environments.build_clients.stop_orphaned_build` (an
interrupt to its client, a bounded wait, and only then its whole process
tree). Nothing ever kills ``docker.exe`` alone, and nothing removes an image
or the build cache. :func:`leftover_counts` gives the same numbers;
stop-only (``list-envs``' footer, the doctor's row) it counts just what a
prune would stop or remove, with no image listing and no disk-usage query.
The containers, running or not, are placed by their labels from one listing;
only a running container with no wfc label is inspected, for its variables.
Whatever Environments may not read itself (which
runs are still running, which pipelines are this project's or have crashed,
which runs a gone try left, which images method versions record) the caller
passes in.

This module owns the plan, the apply, the counts, their wording
(:func:`format_size`, :func:`parse_size`) and the Docker queries of the
search (the containers' listing, the inspect, the plain remove, the env
images, the build cache's size), each bounded as
:mod:`wfc.environments.containers`' ``_docker`` bounds it. It
imports :mod:`wfc.environments.containers` and
:mod:`wfc.environments.build_clients`; neither imports it. Their edges are
called through the module (``_containers._docker``,
``_build_clients._process_table``), so a fake replacing an edge in its own
module reaches these calls too.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from axiom_annotations import AutoStep, Step, task

from . import build_clients as _build_clients
from . import containers as _containers
from .build_clients import (
    _CREATED_TOLERANCE_S,
    BuildStop,
    OrphanedBuild,
    ProcessInfo,
    orphaned_builds,
    stop_orphaned_build,
)
from .containers import (
    CREATOR_LABEL,
    PIPELINE_LABEL,
    PROJECT_LABEL,
    RUN_LABEL,
    ContainerQueryError,
    _first_line,
    container_labels,
    parse_creator_label,
    project_label,
    stop_containers,
)

#: What a running step container's variables name it by.
_PIPELINE_VARIABLE: Final[str] = "WFC_PIPELINE_ID"
_RUN_VARIABLE: Final[str] = "WFC_RUN_ID"

#: Says whether a run is still running: ``(pipeline id or None, run id or
#: None) -> bool``. The caller's liveness (Environments never reads
#: Execution's records).
RunRunning = Callable[[str | None, str | None], bool]

#: The method versions recording each env, as ``plan_push`` takes them:
#: ``env -> [(module/method vN, env_fingerprint)]``.
RecordedImages = Mapping[str, Sequence[tuple[str, str | None]]]

#: The caller's second look: returns the liveness the apply re-checks with,
#: taken as late as the caller's own rule needs (it may wait).
SecondLook = Callable[[], RunRunning]


def crashed_pipeline_line(pipeline_id: str) -> str:
    """The line naming a crashed pipeline and the command that closes its record.

    Args:
        pipeline_id: The pipeline the caller says has crashed.

    Returns:
        The line; it names ``wfc cancel <id>`` and no Docker command.
    """
    return (f"The wfc that ran pipeline {pipeline_id} is gone and its record still "
            f"says running; `wfc cancel {pipeline_id}` closes its record.")


@dataclass(frozen=True)
class LeftoverContainer:
    """A running container of this project whose run is no longer running.

    Attributes:
        id: The container id.
        name: The container's name.
        pipeline_id: The pipeline it belongs to, if it says.
        run_id: The run it ran, if it says.
        labelled: True when it carries wfc's labels (it is killed by them);
            False for one found by its variables (killed by its id).
    """

    id: str
    name: str
    pipeline_id: str | None
    run_id: str | None
    labelled: bool

    def describe(self) -> str:
        """The container in one line: its name, pipeline and run."""
        owner = ", ".join(part for part in (
            f"pipeline {self.pipeline_id}" if self.pipeline_id else "",
            f"run {self.run_id}" if self.run_id else "",
            "" if self.labelled else "started without wfc's labels") if part)
        return f"{self.name or self.id[:12]} ({owner})" if owner else (self.name or self.id[:12])


@dataclass(frozen=True)
class UnfinishedContainer:
    """A container of this project, not running, whose maker is gone.

    Created and never started (a copy-out a crash or a closed terminal
    left), or ended and never removed. Removed by a plain remove, never a
    forced one.

    Attributes:
        id: The container id.
        name: The container's name.
        state: Docker's state for it: ``created``, ``exited`` or ``dead``.
        pipeline_id: Its pipeline label, if it carries one.
        run_id: Its run label, if it carries one.
        creator: Its creator label (``<pid>:<creation time>``), if it
            carries one.
    """

    id: str
    name: str
    state: str
    pipeline_id: str | None = None
    run_id: str | None = None
    creator: str | None = None

    def describe(self) -> str:
        """The container in one line: its name, what made it, and its state."""
        parts = [f"pipeline {self.pipeline_id}" if self.pipeline_id else "",
                 f"run {self.run_id}" if self.run_id else ""]
        parsed = parse_creator_label(self.creator or "")
        if parsed is not None:
            parts.append(f"made by wfc process {parsed[0]}, which is gone")
        parts.append("never started" if self.state == "created" else self.state)
        return f"{self.name or self.id[:12]} ({', '.join(p for p in parts if p)})"


@dataclass(frozen=True)
class LeftoverCounts:
    """The leftover numbers a footer or a doctor row shows.

    Attributes:
        containers: Leftover run containers; ``None`` when Docker did not
            answer.
        builds: Orphaned env builds (from processes: known without Docker).
        unused_images: ``local/<env>`` images no env and no method version
            uses; ``None`` when not known or not asked (a stop-only count).
        cache_bytes: The build cache's total size; ``None`` when not known
            or not asked (a stop-only count).
        docker_error: Why Docker could not be asked, in words that name no
            Docker command; ``None`` when it answered.
        unfinished: This project's containers that are not running and
            whose maker is gone (a prune removes them); 0 when Docker did
            not answer.
    """

    containers: int | None
    builds: int
    unused_images: int | None
    cache_bytes: int | None
    docker_error: str | None = None
    unfinished: int = 0

    @property
    def to_stop(self) -> int:
        """How many leftovers a prune would stop (containers known, plus builds)."""
        return (self.containers or 0) + self.builds

    @property
    def to_clear(self) -> int:
        """How many leftovers a prune would stop or remove."""
        return self.to_stop + self.unfinished

    def headline(self) -> str:
        """The footer's and the doctor row's sentence: what is left, in words.

        ``Left running by an earlier run or crash: ...`` while everything
        counted is running; ``Left by ...`` once a container to remove is.
        """
        left = "Left running" if not self.unfinished else "Left"
        return f"{left} by an earlier run or crash: {', '.join(self.phrases())}."

    def action(self) -> str:
        """What ``wfc prune-envs`` does to them: ``stop``, ``remove`` or both."""
        if self.unfinished and self.to_stop:
            return "stop or remove them"
        return "remove them" if self.unfinished else "stop them"

    def phrases(self) -> list[str]:
        """The non-zero counts in words (``2 run containers to stop``, ...).

        A count Docker could not give is left out; :attr:`docker_error`
        says why.
        """
        out = []
        if self.containers:
            out.append(_plural(self.containers, "run container") + " to stop")
        if self.unfinished:
            out.append(_plural(self.unfinished, "unfinished wfc container") + " to remove")
        if self.builds:
            out.append(_plural(self.builds, "env build") + " to stop")
        if self.unused_images:
            out.append(_plural(self.unused_images, "env image") + " nothing uses")
        if self.cache_bytes:
            out.append(f"build cache {format_size(self.cache_bytes)}")
        return out


@dataclass(frozen=True)
class PrunePlan:
    """What ``wfc prune-envs`` will stop, and what it only counts.

    Built by :func:`plan_prune`; nothing is written until :func:`apply_prune`.

    Attributes:
        project_dir: The project root.
        containers: The leftover run containers; ``None`` when Docker did
            not answer.
        builds: The orphaned env builds.
        unused_images: ``local/<env>`` images nothing uses (counted, kept);
            ``None`` when not known.
        cache_bytes: The build cache's size (counted, kept); ``None`` when
            not known.
        run_running: The caller's liveness, asked again at the apply.
        pipeline_ids: The project's pipeline ids, asked again at the apply.
        docker_error: Why Docker could not be asked, or ``None``.
        planned_at: When the plan was made (for how long a build has run).
        crashed_pipelines: The pipelines the caller says have crashed (the
            wfc that ran them is gone, their record still says running).
        dead_tries: ``(pipeline id, run id)`` of the runs the caller says a
            ``wfc try`` that is gone left running.
        second_look: The caller's second look, asked by the apply before
            its re-check when a crashed pipeline's container is planned;
            ``None`` re-checks with :attr:`run_running`.
        unfinished: This project's labelled containers that are not running
            and whose maker is gone, to remove (empty when Docker did not
            answer).
    """

    project_dir: Path
    containers: tuple[LeftoverContainer, ...] | None
    builds: tuple[OrphanedBuild, ...]
    unused_images: int | None
    cache_bytes: int | None
    run_running: RunRunning = field(repr=False, compare=False)
    pipeline_ids: frozenset[str] = frozenset()
    docker_error: str | None = None
    planned_at: float = 0.0
    crashed_pipelines: frozenset[str] = frozenset()
    dead_tries: frozenset[tuple[str | None, str]] = frozenset()
    second_look: SecondLook | None = field(default=None, repr=False, compare=False)
    unfinished: tuple[UnfinishedContainer, ...] = ()

    @property
    def to_stop(self) -> bool:
        """Say whether the apply has anything to stop."""
        return bool(self.containers) or bool(self.builds)

    @property
    def to_clear(self) -> bool:
        """Say whether the apply has anything to stop or remove."""
        return self.to_stop or bool(self.unfinished)

    def counts(self) -> LeftoverCounts:
        """The plan's numbers, as :func:`leftover_counts` gives them."""
        return LeftoverCounts(
            containers=None if self.containers is None else len(self.containers),
            builds=len(self.builds), unused_images=self.unused_images,
            cache_bytes=self.cache_bytes, docker_error=self.docker_error,
            unfinished=len(self.unfinished))

    def _container_groups(self):
        """The containers by why they are listed: ended runs, crashed pipelines, dead tries."""
        ended: list[LeftoverContainer] = []
        crashed: dict[str, list[LeftoverContainer]] = {}
        tries: dict[str, list[LeftoverContainer]] = {}
        for c in self.containers or ():
            if c.pipeline_id is not None and c.pipeline_id in self.crashed_pipelines:
                crashed.setdefault(c.pipeline_id, []).append(c)
            elif c.run_id is not None and (c.pipeline_id, c.run_id) in self.dead_tries:
                tries.setdefault(c.run_id, []).append(c)
            else:
                ended.append(c)
        return ended, crashed, tries

    def lines(self) -> list[str]:
        """The plan in words: what it will stop, what it only counts and why.

        A crashed pipeline's containers are listed under the line naming
        ``wfc cancel <id>``; a dead try's under a line naming its draft
        step. Printing it writes nothing; no line names a Docker command.
        """
        out: list[str] = []
        if self.containers is None:
            out.append(f"Run containers: not checked ({self.docker_error}).")
        elif self.containers:
            ended, crashed, tries = self._container_groups()
            if ended:
                out.append(f"Run containers to stop ({len(ended)}): "
                           f"their runs are no longer running.")
                out.extend(f"  {c.describe()}" for c in ended)
            for pipeline_id, group in crashed.items():
                out.append(f"{crashed_pipeline_line(pipeline_id)} Its run containers "
                           f"to stop ({len(group)}):")
                out.extend(f"  {c.describe()}" for c in group)
            for run_id, group in tries.items():
                out.append(f"The wfc try that ran draft step {run_id} is gone; its run "
                           f"containers to stop ({len(group)}):")
                out.extend(f"  {c.describe()}" for c in group)
        if self.unfinished:
            out.append(f"To remove: {_plural(len(self.unfinished), 'container')} wfc "
                       f"made that never finished.")
            out.extend(f"  {c.describe()}" for c in self.unfinished)
        if self.builds:
            out.append(f"Env builds to stop ({len(self.builds)}): no wfc command "
                       f"is waiting for them.")
            out.extend(f"  {b.describe(self.planned_at)}" for b in self.builds)
        if not self.to_clear:
            out.append("Nothing to stop.")
        out.append("Counted, not removed:")
        if self.unused_images is None:
            out.append(f"  Env images no env or method version uses: not checked"
                       f"{_because(self.docker_error)}.")
        else:
            out.append(f"  Env images no env or method version uses: "
                       f"{self.unused_images} (kept: prune-envs removes no image).")
        if self.cache_bytes is None:
            out.append(f"  Build cache: size not known{_because(self.docker_error)}.")
        else:
            out.append(f"  Build cache: {format_size(self.cache_bytes)} (kept: "
                       f"register-env reuses it, so the next build is faster).")
        return out


@dataclass
class PruneResult:
    """What :func:`apply_prune` did.

    Attributes:
        containers: The leftover containers stopped.
        builds: Each orphaned build stopped or not.
        skipped: Items no longer leftovers at the re-check, in words.
        errors: What could not be stopped, and why, in words that name no
            Docker command.
        crashed_pipelines: The crashed pipelines whose containers were
            stopped, in order; each one's record still says running.
        removed: The containers that never finished, removed.
    """

    containers: list[LeftoverContainer] = field(default_factory=list)
    builds: list[BuildStop] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    crashed_pipelines: list[str] = field(default_factory=list)
    removed: list[UnfinishedContainer] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """0 when everything planned was stopped or had already ended; else 1."""
        return 1 if self.errors or any(not b.stopped for b in self.builds) else 0

    def lines(self) -> list[str]:
        """The result in words; no line names a Docker command.

        Each crashed pipeline whose containers were stopped gets the line
        naming ``wfc cancel <id>`` again: prune-envs does not close a record.
        """
        out = [f"Stopped run container {c.describe()}" for c in self.containers]
        out.extend(crashed_pipeline_line(pid) for pid in self.crashed_pipelines)
        out.extend(f"Removed container {c.describe()}" for c in self.removed)
        for stop in self.builds:
            if stop.stopped:
                out.append(f"Stopped env build {stop.build.env} ({stop.how})")
            else:
                out.append(f"Could not stop env build {stop.build.env}: its client "
                           f"is still running")
        out.extend(f"Left alone: {why}" for why in self.skipped)
        out.extend(f"Could not stop: {why}" for why in self.errors)
        return out


def _plural(count: int, noun: str) -> str:
    """``1 env build`` / ``2 env builds``."""
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _because(error: str | None) -> str:
    """`` (<error>)`` or nothing."""
    return f" ({error})" if error else ""


#: Docker's size units (decimal, as it prints them) and their binary forms.
_SIZE_UNITS: Final[dict[str, int]] = {
    "b": 1, "kb": 10**3, "mb": 10**6, "gb": 10**9, "tb": 10**12, "pb": 10**15,
    "kib": 2**10, "mib": 2**20, "gib": 2**30, "tib": 2**40, "pib": 2**50,
}
_SIZE: Final = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)\s*$")


def parse_size(text: str) -> int | None:
    """Read a size as Docker prints it (``1.234GB``, ``512kB``, ``0B``) as bytes.

    Args:
        text: The size text.

    Returns:
        The size in bytes, or ``None`` when the text is not a size.
    """
    match = _SIZE.match(text or "")
    if match is None:
        return None
    unit = _SIZE_UNITS.get(match.group(2).lower())
    return None if unit is None else int(float(match.group(1)) * unit)


def format_size(size: int) -> str:
    """Write a byte count in decimal units, as Docker does (``1.2 GB``).

    Args:
        size: Bytes.

    Returns:
        The size with one decimal above a kilobyte.
    """
    value = float(size)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"  # pragma: no cover - the loop returns


# --- the Docker side: leftover containers and the counts --------------------


#: The containers' listing: each one's id, name, state and wfc's four
#: labels, tab-separated (a label it lacks prints empty).
_LISTING_FORMAT: Final[str] = "\t".join(
    ("{{.ID}}", "{{.Names}}", "{{.State}}",
     *(f'{{{{.Label "{label}"}}}}'
       for label in (PROJECT_LABEL, PIPELINE_LABEL, RUN_LABEL, CREATOR_LABEL))))

#: The states of a container that is not running: never started, or ended.
_NOT_RUNNING_STATES: Final[frozenset[str]] = frozenset({"created", "exited", "dead"})


@dataclass(frozen=True)
class _Listed:
    """One row of the containers' listing; a label it lacks is ``""``."""

    id: str
    name: str
    state: str
    project: str
    pipeline: str
    run: str
    creator: str


def _listed_containers() -> list[_Listed]:
    """Return every container's id, name, state and wfc labels, running or not.

    One listing.

    Raises:
        ContainerQueryError: Docker did not answer or refused.
    """
    from .docker import DockerTimeoutError

    try:
        proc = _containers._docker(["ps", "--all", "--no-trunc", "--format",
                                    _LISTING_FORMAT], what="listing the containers")
    except DockerTimeoutError as exc:
        raise ContainerQueryError(str(exc)) from exc
    except OSError as exc:
        raise ContainerQueryError(f"Docker could not be started: {exc}") from exc
    if proc.returncode != 0:
        why = _first_line(proc.stderr) or f"exit status {proc.returncode}"
        raise ContainerQueryError(f"Docker could not list the containers: {why}")
    rows = []
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            continue
        cells = [cell.strip() for cell in (line.split("\t") + [""] * 7)[:7]]
        rows.append(_Listed(cells[0], cells[1], cells[2].lower(), *cells[3:]))
    return rows


def _container_variables(ids: Sequence[str]) -> dict[str, tuple[str, dict[str, str]]]:
    """Inspect *ids*: ``{id: (name, variables)}`` of those still running.

    Raises:
        ContainerQueryError: Docker did not answer or refused.
    """
    from .docker import DockerTimeoutError

    try:
        proc = _containers._docker(["inspect", *ids], what="reading the running containers")
    except DockerTimeoutError as exc:
        raise ContainerQueryError(str(exc)) from exc
    except OSError as exc:
        raise ContainerQueryError(f"Docker could not be started: {exc}") from exc
    try:
        items = json.loads(proc.stdout or "[]")
    except ValueError:
        items = None
    if not isinstance(items, list) or (proc.returncode != 0 and not items):
        why = _first_line(proc.stderr) or f"exit status {proc.returncode}"
        raise ContainerQueryError(f"Docker could not read the running containers: {why}")
    found = {}
    for item in items:  # one that exited since the listing is simply absent
        if not (item.get("State") or {}).get("Running", True):
            continue
        config = item.get("Config") or {}
        variables = dict(entry.split("=", 1) for entry in (config.get("Env") or [])
                         if "=" in entry)
        found[item.get("Id", "")] = ((item.get("Name") or "").lstrip("/"), variables)
    return found


def _projects_containers(project_dir: str | Path, pipeline_ids: frozenset[str]
                         ) -> tuple[list[LeftoverContainer], list[UnfinishedContainer]]:
    """Return this project's running run containers and its labelled ones not running.

    One listing gives every container's state and labels. A running
    container with wfc's project label is this project's run container
    only with this project's label and a run label (else another
    project's, or a dev-loop container), decided without an inspect. Only a
    running container with no project label, which the labels cannot
    place, is inspected, and only when the caller names pipeline ids: it is
    this project's when its ``WFC_PIPELINE_ID`` is one of them. A container
    that is not running (created and never started, exited, dead) is
    returned only with this project's label; one without it is never
    looked at.

    Raises:
        ContainerQueryError: Docker did not answer or refused.
    """
    mine = project_label(project_dir)
    found: list[LeftoverContainer] = []
    stopped: list[UnfinishedContainer] = []
    unplaced: list[str] = []
    for row in _listed_containers():
        if row.state in _NOT_RUNNING_STATES:
            if row.project == mine:
                stopped.append(UnfinishedContainer(
                    id=row.id, name=row.name, state=row.state,
                    pipeline_id=row.pipeline or None, run_id=row.run or None,
                    creator=row.creator or None))
        elif row.state == "removing":
            continue  # on its way out already
        elif row.project:
            if row.project == mine and row.run:
                found.append(LeftoverContainer(id=row.id, name=row.name,
                                               pipeline_id=row.pipeline or None,
                                               run_id=row.run, labelled=True))
        elif pipeline_ids:
            unplaced.append(row.id)
    if unplaced:
        for cid, (name, variables) in _container_variables(unplaced).items():
            pipeline_id = variables.get(_PIPELINE_VARIABLE)
            if pipeline_id and pipeline_id in pipeline_ids:
                found.append(LeftoverContainer(id=cid, name=name, pipeline_id=pipeline_id,
                                               run_id=variables.get(_RUN_VARIABLE),
                                               labelled=False))
    return found, stopped


def _still_running(run_running: RunRunning, pipeline_id: str | None,
                   run_id: str | None) -> bool:
    """Ask the caller's liveness; a liveness that fails counts as running."""
    try:
        return bool(run_running(pipeline_id, run_id))
    except Exception:  # noqa: BLE001 - when in doubt, a container is not a leftover
        return True


def _creator_alive(creator: str, processes: Mapping[int, ProcessInfo]) -> bool:
    """Say whether the process a creator label names is still that process.

    A label that cannot be read counts as alive: when in doubt, a container
    is not removed.
    """
    parsed = parse_creator_label(creator)
    if parsed is None:
        return True
    pid, created = parsed
    info = processes.get(pid)
    return info is not None and abs(info.created - created) <= _CREATED_TOLERANCE_S


def _made_by_something_gone(container: UnfinishedContainer, run_running: RunRunning,
                            processes: Callable[[], Mapping[int, ProcessInfo]]) -> bool:
    """Say whether everything that marks a not-running container live is gone.

    Its run (a run or pipeline label) has ended under the caller's
    liveness, and its creator (a creator label) is gone or reused; with
    neither label nothing marks it live.
    """
    if (container.pipeline_id or container.run_id) and _still_running(
            run_running, container.pipeline_id, container.run_id):
        return False
    return not (container.creator and _creator_alive(container.creator, processes()))


def _leftover_containers(project_dir: str | Path, run_running: RunRunning,
                         pipeline_ids: frozenset[str],
                         processes: Mapping[int, ProcessInfo] | None = None
                         ) -> tuple[tuple[LeftoverContainer, ...],
                                    tuple[UnfinishedContainer, ...]]:
    """Find this project's leftover containers: to stop, and to remove.

    To stop: its running run containers whose run is no longer running. To
    remove: its labelled containers that are not running and that nothing
    marks live (:func:`_made_by_something_gone`).

    Args:
        project_dir: The project root.
        run_running: The caller's liveness.
        pipeline_ids: The project's pipeline ids.
        processes: The process table, when the caller has read it; read
            here only if a creator label needs it.

    Raises:
        ContainerQueryError: Docker did not answer or refused.
    """
    table: list[Mapping[int, ProcessInfo]] = [] if processes is None else [processes]

    def read_table() -> Mapping[int, ProcessInfo]:
        if not table:
            table.append(_build_clients._process_table())
        return table[0]

    running, stopped = _projects_containers(project_dir, pipeline_ids)
    return (tuple(c for c in running
                  if not _still_running(run_running, c.pipeline_id, c.run_id)),
            tuple(c for c in stopped
                  if _made_by_something_gone(c, run_running, read_table)))


_IMAGE_ID: Final = re.compile(r"@(sha256:[0-9a-f]{64})$")


def _unused_images(project_dir: Path, recorded: RecordedImages) -> int:
    """Count the ``local/<env>`` images of this project's envs nothing uses.

    An image is used when an env's record names it or a method version
    records it (its captures folder's ``image.json``).

    Raises:
        ContainerQueryError: Docker did not answer or refused.
    """
    from .captures import CaptureError, image_info
    from .docker import DockerTimeoutError
    from .manifest import list_envs

    try:
        records = dict(list_envs(project_dir))
    except ValueError:
        records = {}
    keep: set[str] = set()
    for record in records.values():
        match = _IMAGE_ID.search(record.container or "")
        if match:
            keep.add(match.group(1))
    for env, versions in recorded.items():
        for _, fingerprint in versions:
            if not fingerprint:
                continue
            try:
                match = _IMAGE_ID.search(image_info(env, fingerprint, project_dir).image)
            except CaptureError:
                continue
            if match:
                keep.add(match.group(1))
    repositories = {f"local/{name}" for name in (*records, *recorded)}
    try:
        proc = _containers._docker(["images", "--no-trunc", "--filter", "reference=local/*",
                                    "--format", "{{.Repository}}\t{{.ID}}"],
                                   what="listing the env images")
    except DockerTimeoutError as exc:
        raise ContainerQueryError(str(exc)) from exc
    except OSError as exc:
        raise ContainerQueryError(f"Docker could not be started: {exc}") from exc
    if proc.returncode != 0:
        why = _first_line(proc.stderr) or f"exit status {proc.returncode}"
        raise ContainerQueryError(f"Docker could not list the env images: {why}")
    unused = set()
    for line in (proc.stdout or "").splitlines():
        repository, _, image_id = line.strip().partition("\t")
        if repository in repositories and image_id and image_id not in keep:
            unused.add(image_id)
    return len(unused)


def _build_cache_size() -> int:
    """Return the build cache's total size in bytes, as Docker reports it.

    Raises:
        ContainerQueryError: Docker did not answer, refused, or gave no size.
    """
    from .docker import DockerTimeoutError

    try:
        proc = _containers._docker(["system", "df", "--format", "{{json .}}"],
                                   what="reading the build cache's size")
    except DockerTimeoutError as exc:
        raise ContainerQueryError(str(exc)) from exc
    except OSError as exc:
        raise ContainerQueryError(f"Docker could not be started: {exc}") from exc
    if proc.returncode != 0:
        why = _first_line(proc.stderr) or f"exit status {proc.returncode}"
        raise ContainerQueryError(f"Docker could not report the build cache's size: {why}")
    for line in (proc.stdout or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("Type") == "Build Cache":
            size = parse_size(str(row.get("Size", "")))
            if size is not None:
                return size
    raise ContainerQueryError("Docker did not report the build cache's size")


@task(purpose="Plan wfc prune-envs for one project, writing nothing: the "
              "leftover run containers (this project's label and a run the "
              "caller says has ended, or unlabelled with one of the project's "
              "pipeline ids and an ended run), the orphaned env builds (from "
              "processes: a build client of this project's .wfc/build/ with "
              "no live wfc ancestor), and the counts kept (local/<env> images "
              "nothing uses, the build cache's size)",
      inputs="project root; the caller's run liveness, the project's "
             "pipeline ids, the method versions recording each env; the "
             "pipelines the caller says crashed, the runs a gone wfc try "
             "left, and the caller's second look",
      outputs="a PrunePlan that words itself",
      critical="never lists another project's container or build, a "
               "dev-loop container, or a container whose run is running; "
               "orphaned builds come from processes, never build history; "
               "Docker not answering is said, never guessed, and the builds "
               "are still found; which runs crashed is the caller's, never "
               "read here")
def plan_prune(project_dir: str | Path, *, run_running: RunRunning,
               pipeline_ids: Iterable[str] = (),
               recorded: RecordedImages | None = None,
               crashed_pipelines: Iterable[str] = (),
               dead_tries: Iterable[tuple[str | None, str]] = (),
               second_look: SecondLook | None = None) -> PrunePlan:
    """State what ``wfc prune-envs`` will stop and what it only counts.

    Args:
        project_dir: The project root.
        run_running: ``(pipeline id, run id) -> bool``: whether that run is
            still running (Execution's liveness, passed in). One that raises
            counts as running.
        pipeline_ids: The pipeline ids this project's records hold (an
            unlabelled container counts as this project's only with one).
        recorded: ``env -> [(module/method vN, env_fingerprint)]`` for the
            method versions recording each env, as ``plan_push`` takes it.
        crashed_pipelines: The pipelines whose wfc is gone while their
            record still says running (the caller's judgement; its
            *run_running* already says their runs ended). Their containers
            are listed under the line naming ``wfc cancel <id>``.
        dead_tries: ``(pipeline id, run id)`` of the runs a ``wfc try``
            that is gone left running (the run id is its draft step); their
            containers are listed under a line naming the draft step.
        second_look: Called by the apply, before its re-check, when a
            crashed pipeline's container is planned: it returns the
            liveness to re-check with, and may wait as the caller's rule
            needs. ``None`` re-checks with *run_running*.

    Returns:
        The :class:`PrunePlan`.
    """
    project_dir = Path(project_dir)
    ids = frozenset(str(p) for p in pipeline_ids)
    口 = Step(step_num=1, name="Find the orphaned env builds",
             purpose="From the process table: this project's build clients "
                     "with no live wfc ancestor",
             critical="known without Docker")
    processes = _build_clients._process_table()
    builds = tuple(orphaned_builds(processes, project_dir))
    口 = Step(step_num=2, name="Find the leftover containers",
             purpose="To stop: running containers with this project's label "
                     "and an ended run, or unlabelled with this project's "
                     "pipeline id and an ended run; a dev-loop container never. "
                     "To remove: this project's labelled containers that are "
                     "not running whose run has ended and whose creator is gone",
             critical="Docker not answering leaves the containers and counts "
                      "unknown, and says why; an unlabelled container that is "
                      "not running is never listed")
    containers: tuple[LeftoverContainer, ...] | None
    unfinished: tuple[UnfinishedContainer, ...] = ()
    unused: int | None = None
    cache: int | None = None
    error: str | None = None
    try:
        containers, unfinished = _leftover_containers(project_dir, run_running, ids,
                                                      processes)
    except ContainerQueryError as exc:
        containers, error = None, str(exc)
    口 = Step(step_num=3, name="Count what is kept",
             purpose="The local/<env> images no env and no method version uses, "
                     "and the build cache's size; neither is ever removed")
    if containers is not None:
        try:
            unused = _unused_images(project_dir, recorded or {})
        except ContainerQueryError as exc:
            error = str(exc)
        try:
            cache = _build_cache_size()
        except ContainerQueryError as exc:
            error = error or str(exc)
    return PrunePlan(project_dir=project_dir, containers=containers, builds=builds,
                     unused_images=unused, cache_bytes=cache, run_running=run_running,
                     pipeline_ids=ids, docker_error=error, planned_at=time.time(),
                     crashed_pipelines=frozenset(str(p) for p in crashed_pipelines),
                     dead_tries=frozenset((p, str(r)) for p, r in dead_tries),
                     second_look=second_look, unfinished=unfinished)


def _remove_unfinished(container: UnfinishedContainer) -> tuple[bool, str | None]:
    """Remove one container that never finished, by a plain remove (never forced).

    Returns:
        ``(removed, why)``: ``(True, None)`` when removed; ``(False, why)``
        when left alone (it started since, or is gone already) or when
        the remove failed. *why* names no Docker command.
    """
    from .docker import DockerTimeoutError

    try:
        proc = _containers._docker(["rm", container.id],
                                   what="removing a container that never finished")
    except DockerTimeoutError as exc:
        return False, f"{container.describe()}: {exc}"
    except OSError as exc:
        return False, f"{container.describe()}: Docker could not be started: {exc}"
    if proc.returncode == 0:
        return True, None
    why = _first_line(proc.stderr) or f"exit status {proc.returncode}"
    return False, f"{container.describe()}: {why}"


def _refused_as_running(why: str) -> bool:
    """Say whether a remove failed because the container is running now."""
    return "running" in why.lower()


def _gone_already(why: str) -> bool:
    """Say whether a remove failed because the container no longer exists."""
    return "no such container" in why.lower()


def _kill_container(container: LeftoverContainer) -> str | None:
    """Kill one container wfc did not label, by its id; return why it failed, or None."""
    from .docker import DockerTimeoutError

    try:
        proc = _containers._docker(["kill", container.id],
                                   what="stopping a leftover container")
    except DockerTimeoutError as exc:
        return f"{container.describe()}: {exc}"
    except OSError as exc:
        return f"{container.describe()}: Docker could not be started: {exc}"
    if proc.returncode == 0:
        return None
    why = _first_line(proc.stderr) or f"exit status {proc.returncode}"
    if "no such container" in why.lower() or "is not running" in why.lower():
        return None  # it ended on its own
    return f"{container.describe()}: {why}"


def _always_running(pipeline_id: str | None, run_id: str | None) -> bool:
    """The liveness of a second look that failed: every run is still running."""
    return True


@task(purpose="Apply a confirmed wfc prune-envs plan: take the caller's "
              "second look when a crashed pipeline's container is planned, "
              "re-check each planned item is still a leftover, kill each "
              "leftover container (by its labels when it carries them, else "
              "by its id), and end each orphaned build by interrupting its "
              "client, a bounded wait, then its process tree",
      inputs="the PrunePlan the user confirmed",
      outputs="a PruneResult: what was stopped, left alone, or could not be; "
              "each crashed pipeline stopped names wfc cancel again",
      critical="removes no image and no build cache; never touches an item "
               "that is no longer a leftover or a bystander build; never kills "
               "docker.exe alone; a second look that fails leaves every "
               "container alone")
def apply_prune(plan: PrunePlan) -> PruneResult:
    """Stop what *plan* lists, each re-checked first.

    Args:
        plan: The plan :func:`plan_prune` made, confirmed by the user.

    Returns:
        The :class:`PruneResult`; its ``exit_code`` is 1 when something
        could not be stopped.
    """
    result = PruneResult()
    root = plan.project_dir
    口 = Step(step_num=1, name="Take the caller's second look",
             purpose="Only when a crashed pipeline's container is planned: the "
                     "caller's second look (which may wait) gives the liveness "
                     "the re-check uses, so a heartbeat that moved since the "
                     "plan keeps its container",
             critical="one that raises counts every run as running")
    running = plan.run_running
    planned_pipelines = [*(c.pipeline_id for c in plan.containers or ()),
                         *(c.pipeline_id for c in plan.unfinished)]
    if plan.second_look is not None and any(
            p in plan.crashed_pipelines for p in planned_pipelines):
        try:
            running = plan.second_look()
        except Exception:  # noqa: BLE001 - when in doubt, a container is not a leftover
            running = _always_running
    口 = Step(step_num=2, name="Re-check the containers",
             purpose="List this project's leftover containers again with the "
                     "caller's liveness; one no longer a leftover (to stop or "
                     "to remove) is left alone")
    fresh: dict[str, LeftoverContainer] = {}
    removable: set[str] = set()
    rechecked = False
    if plan.containers or plan.unfinished:
        try:
            again, again_unfinished = _leftover_containers(root, running, plan.pipeline_ids)
            fresh = {c.id: c for c in again}
            removable = {c.id for c in again_unfinished}
            rechecked = True
        except ContainerQueryError as exc:
            result.errors.append(f"the run containers could not be checked again: {exc}")
    labelled: dict[tuple[str | None, str | None], list[LeftoverContainer]] = {}
    for container in plan.containers or ():
        if container.id not in fresh and not result.errors:
            result.skipped.append(f"{container.describe()}: no longer a leftover")
        elif container.id in fresh and container.labelled:
            labelled.setdefault((container.pipeline_id, container.run_id), []).append(container)
    口 = Step(step_num=3, name="Stop the containers",
             purpose="A kill by label per ended run (the stop by label); a container "
                     "found by its variables is killed by its id; each crashed "
                     "pipeline with a container stopped is named for wfc cancel")
    for (pipeline_id, run_id), group in labelled.items():
        口 = AutoStep(step_num=3.1, name="Stop one run's containers")
        stop = stop_containers(container_labels(root, pipeline_id=pipeline_id, run_id=run_id))
        result.containers.extend(c for c in group if c.id in stop.stopped)
        if stop.error is not None:
            result.errors.append(f"run {run_id}'s containers: {stop.error}")
    for container in plan.containers or ():
        if container.id in fresh and not container.labelled:
            failure = _kill_container(container)
            if failure is None:
                result.containers.append(container)
            else:
                result.errors.append(failure)
    for container in result.containers:
        pipeline_id = container.pipeline_id
        if pipeline_id in plan.crashed_pipelines and pipeline_id not in result.crashed_pipelines:
            result.crashed_pipelines.append(pipeline_id)
    口 = Step(step_num=4, name="Remove the containers that never finished",
             purpose="Each one still not running and still made by something "
                     "gone is removed by a plain remove; one that started since "
                     "is refused by Docker and left alone, never forced")
    for unfinished in plan.unfinished if rechecked else ():
        if unfinished.id not in removable:
            result.skipped.append(f"{unfinished.describe()}: no longer one to remove")
            continue
        removed, why = _remove_unfinished(unfinished)
        if removed:
            result.removed.append(unfinished)
        elif why is not None and _refused_as_running(why):
            result.skipped.append(f"{unfinished.describe()}: it started since the plan, "
                                  f"so it was not removed")
        elif why is not None and _gone_already(why):
            result.skipped.append(f"{unfinished.describe()}: already gone")
        elif why is not None:
            result.errors.append(why)
    口 = Step(step_num=5, name="Re-check the builds",
             purpose="Read the process table again; a planned build still an "
                     "orphan is stopped as it is now, one that ended or gained "
                     "a wfc parent is left alone")
    now = orphaned_builds(_build_clients._process_table(), root) if plan.builds else []
    for build in plan.builds:
        current = next((b for b in now if b.holds(build)), None)
        if current is None:
            result.skipped.append(f"env build {build.env}: no longer running "
                                  f"without wfc")
            continue
        口 = AutoStep(step_num=5.1, name="Stop one orphaned build")
        stop = stop_orphaned_build(current)
        result.builds.append(stop)
    return result


@task(purpose="Count the leftovers, listing nothing: the run containers and "
              "orphaned builds to stop and, unless stop-only (wfc list-envs' "
              "footer, the doctor's row, the count after an interrupted "
              "apply), the env images nothing uses and the build cache's size",
      inputs="as plan_prune; stop_only",
      outputs="LeftoverCounts; with Docker unreachable the Docker counts are "
              "unknown and say why, the build count still comes from processes",
      critical="stop-only lists no image and asks for no disk usage")
def leftover_counts(project_dir: str | Path, *, run_running: RunRunning,
                    pipeline_ids: Iterable[str] = (),
                    recorded: RecordedImages | None = None,
                    stop_only: bool = False) -> LeftoverCounts:
    """Return the numbers :func:`plan_prune` finds, without listing anything.

    Args:
        project_dir: The project root.
        run_running: As :func:`plan_prune`.
        pipeline_ids: As :func:`plan_prune`.
        recorded: As :func:`plan_prune`; not read when *stop_only*.
        stop_only: Count only what a prune would stop (the run containers,
            found by their labels first, and the orphaned builds): no image
            listing and no disk-usage query, so ``unused_images`` and
            ``cache_bytes`` are ``None``.

    Returns:
        The :class:`LeftoverCounts`.
    """
    if not stop_only:
        plan = plan_prune(project_dir, run_running=run_running,
                          pipeline_ids=pipeline_ids, recorded=recorded)
        return plan.counts()
    project_dir = Path(project_dir)
    processes = _build_clients._process_table()
    builds = orphaned_builds(processes, project_dir)
    try:
        containers, unfinished = _leftover_containers(
            project_dir, run_running, frozenset(str(p) for p in pipeline_ids), processes)
    except ContainerQueryError as exc:
        return LeftoverCounts(containers=None, builds=len(builds), unused_images=None,
                              cache_bytes=None, docker_error=str(exc))
    return LeftoverCounts(containers=len(containers), builds=len(builds),
                          unused_images=None, cache_bytes=None,
                          unfinished=len(unfinished))
