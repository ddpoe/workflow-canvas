"""Sample content reachability: can wfc still get a registered sample's bytes?

The one classifier, shared by the pipeline-start preflight
(:func:`preflight_sample_content`) and ``wfc doctor``'s ``samples`` check
(``wfc.execution.readiness.check_samples``).  Two copies of this table would
drift, so there is one.

The table is local-cache presence x ``push_status``, and it costs **zero
network I/O** -- ``push_status`` is already on the row and the cache entry is
one ``Path.exists()`` away:

===============  ==================  ==============  =========================
local cache      ``push_status``     state           action
===============  ==================  ==============  =========================
present          ``pushed``          ``healthy``     --
present          not ``pushed``      ``local_only``  report, never refuse
missing          ``pushed``          ``cold``        silent -- the restore pulls
missing          not ``pushed``      ``unreachable`` refuse
===============  ==================  ==============  =========================

The principle behind it: **can the system recover this state by itself?**
Recoverable states are not reported as problems, because reporting them
trains a user to ignore the report.  A local miss on a pushed row is the cold
start ADR-018 documents ("first run after a cache prune will repopulate from
remote"); it must neither refuse nor warn.  A row that is cached but not
pushed still runs here and is simply not portable -- that is worth saying and
is never worth refusing over.

``pending`` and ``in_flight`` fall on the "not pushed" side of both rows, and
that is correct rather than incidental: a push reads the bytes out of the
local cache, so a row whose cache entry is gone before its push completed can
never be pushed, and the archive has nothing.  ``pushed`` is the only status
that makes a local miss recoverable.

A row carrying no ``content_hash`` is ``unreachable`` whatever its push
status.  The cache is content-addressed, so with no hash there is nothing to
look the bytes up by -- it is the malformed record ``load_sample_hashes`` and
``restore_sample`` already refuse, and it is unreachable for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from sqlmodel import select

from ..persistence import PushStatus, Sample

#: The four states the local-cache x ``push_status`` table produces.
SampleState = Literal["healthy", "local_only", "cold", "unreachable"]

_PUSHED = PushStatus.pushed.value


@dataclass(frozen=True)
class SampleHealth:
    """One sample row's place in the reachability table.

    Attributes:
        name: The registered sample name.
        content_hash: The row's content hash, or ``""`` for a malformed
            row that carries none.
        push_status: The row's ``push_status`` verbatim.
        cached: Whether this project's local DVC cache holds the entry the
            content hash addresses.
        state: ``"healthy"``, ``"local_only"``, ``"cold"`` or
            ``"unreachable"`` -- see the module docstring's table.
    """

    name: str
    content_hash: str
    push_status: str
    cached: bool
    state: SampleState

    @property
    def reachable(self) -> bool:
        """Whether wfc can still get this sample's bytes from somewhere."""
        return self.state != "unreachable"


def _entry_is_whole(project_dir: Path, content_hash: str) -> bool:
    """Whether the local cache holds the whole entry (a directory: manifest
    and every member).  A malformed entry is not a usable local copy."""
    from ..storage import entry_is_local

    return entry_is_local(project_dir, content_hash)


def classify_sample(row: Sample, project_dir: Path | str) -> SampleHealth:
    """Place one ``Sample`` row in the reachability table.

    One ``Path.exists()`` and no network I/O.  See the module docstring for
    the table and the principle behind it.

    Args:
        row: The registered sample's row.
        project_dir: Root directory of the wfc project whose local DVC
            cache is consulted.

    Returns:
        The row's :class:`SampleHealth`.
    """
    content_hash = row.content_hash or ""
    pushed = row.push_status == _PUSHED
    cached = bool(content_hash) and _entry_is_whole(Path(project_dir), content_hash)

    if not content_hash:
        # A malformed record: content-addressed storage with no address.
        state: SampleState = "unreachable"
    elif cached:
        state = "healthy" if pushed else "local_only"
    else:
        state = "cold" if pushed else "unreachable"

    return SampleHealth(
        name=row.name,
        content_hash=content_hash,
        push_status=row.push_status,
        cached=cached,
        state=state,
    )


def classify_samples(
    rows: Iterable[Sample], project_dir: Path | str
) -> list[SampleHealth]:
    """Place every given ``Sample`` row in the reachability table.

    Args:
        rows: The sample rows to classify, in the order to report them.
        project_dir: Root directory of the wfc project.

    Returns:
        One :class:`SampleHealth` per row, in the given order.
    """
    return [classify_sample(row, project_dir) for row in rows]


def load_sample_health(session, project_dir: Path | str) -> list[SampleHealth]:
    """Classify every registered sample in the project, by name.

    Args:
        session: An open database session.
        project_dir: Root directory of the wfc project.

    Returns:
        One :class:`SampleHealth` per registered sample, name-ordered.
    """
    rows = session.exec(select(Sample).order_by(Sample.name)).all()
    return classify_samples(rows, project_dir)


class UnreachableSampleError(ValueError):
    """A pipeline's samples include content wfc cannot reach anywhere.

    Raised by :func:`preflight_sample_content` at pipeline start -- before
    any run row is written -- and carries every offender rather than the
    first, so one message names the whole repair.

    Attributes:
        unreachable: The offending :class:`SampleHealth` entries, in the
            pipeline's sample order.
    """

    def __init__(self, unreachable: list[SampleHealth]):
        """Build the refusal message for ``unreachable``.

        Args:
            unreachable: The offending entries, in the pipeline's order.
        """
        self.unreachable = unreachable
        count = len(unreachable)
        noun = "sample" if count == 1 else "samples"
        lines = [
            f"The pipeline was not started: {count} {noun} cannot be "
            f"materialized. Each is a malformed record -- it names content "
            f"that is in neither this project's local cache nor its archive, "
            f"so there is nothing to key a run on and nothing to restore. "
            f"There is no conversion and no fallback; re-register each "
            f"sample from its source:",
            "",
        ]
        for entry in unreachable:
            where = (
                f"content hash {entry.content_hash}"
                if entry.content_hash
                else "no content hash recorded"
            )
            lines.append(
                f"  {entry.name} ({where}, push status: {entry.push_status})"
            )
            lines.append(
                f"    wfc register-sample --name {entry.name} --source <path>"
            )
        super().__init__("\n".join(lines))


def preflight_sample_content(
    samples: list[str], session, project_dir: Path | str
) -> list[SampleHealth]:
    """Refuse a pipeline whose samples include unreachable content.

    Pipeline start is the earliest moment that sees the whole sample set,
    and it is before any run row exists -- so the refusal is one message
    naming every offender, rather than N engine rules exiting non-zero with
    the diagnosis buried in job logs.

    Only the ``unreachable`` state refuses.  A ``cold`` sample (a local miss
    on a pushed row) is silent, because the emitted restore rule pulls it;
    a ``local_only`` sample is returned for a caller that wants to report it
    but never refuses, because the run works here.

    Args:
        samples: Sample names, in the pipeline's order.
        session: An open database session.
        project_dir: Root directory of the wfc project.

    Returns:
        One :class:`SampleHealth` per named sample that has a row, in the
        given order.  A name with no row is absent -- an unregistered name
        is not this refusal (the claim refuses it when a step reads it).

    Raises:
        UnreachableSampleError: One or more named samples are registered
            but their content is in neither the local cache nor the
            archive.  Nothing has been launched.
    """
    health: list[SampleHealth] = []
    for name in samples:
        row = session.exec(select(Sample).where(Sample.name == name)).first()
        if row is None:
            continue
        health.append(classify_sample(row, project_dir))

    unreachable = [entry for entry in health if entry.state == "unreachable"]
    if unreachable:
        raise UnreachableSampleError(unreachable)
    return health
