"""``wfc verify``: re-hash everything wfc holds and list what is not what it recorded.

:func:`verify_project` walks five kinds, each through Storage's one check
(``integrity.check_entry``) with ``[integrity] inputs = "hash"`` whatever the
project's setting, so a matching stamp or stat record never vouches:

- **samples**: each registered sample's cache entry, and its restored copy
  when one is present;
- **outputs**: each output the archive pass has not reached, file and
  directory alike, against collect's hash when the row carries one, else
  against collect's stat record. A changed output is marked changed (the
  one-way mark every check writes);
- **objects**: each archived output's cache entry (a file's object, or a
  directory's manifest and members);
- **checkouts**: each directory checkout under the project tree;
- **blobs**: each blob row's cache entry.

A thing that is not there is not listed: absence is each reader's own
refusal, and a pruned entry is pushed, so a read pulls it. The one
exception is an entry no record shows pushed that is not complete in the
local cache: nothing can bring it back, so it is listed as lost. A
malformed entry (a directory tree at an address without the ``.dir``
suffix) is listed as malformed, pushed or not: nothing converts it. A shared
cache entry is checked (and, with ``repair``, pulled again) once and listed
under each record that names it; when a sample's restore pulls it again
after that pull failed, every record naming it shows it repaired.

With ``repair`` it puts back what wfc can restore exactly, from bytes it
recorded: a sample's copy from its checked cache entry, a checkout rebuilt
from its objects, and an object recorded pushed (by any record naming it)
pulled again after the changed local copy is set aside. What it cannot
repair is listed with the action that does: a changed pre-archive output
(re-run its step), a changed object never pushed, a lost or malformed entry
and a sample's copy of a malformed entry (re-run its run, or re-register its
sample), and a checkout whose entry is changed or not complete locally.
``--repair`` creates and changes no record, so it asks no confirmation.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow
from sqlmodel import col, select

from .. import layout
from ..identity import is_directory_hash
from ..persistence import (
    INTEGRITY_INPUTS_HASH,
    Blob,
    OutputIntegrity,
    PushStatus,
    Run,
    RunOutput,
    Sample,
    get_session,
)
from ..persistence import project_root as get_project_root
from .cache import (
    MALFORMED_SHAPE,
    ChangedEntryError,
    MalformedEntryError,
    blob_repair,
    changed_objects,
    changed_phrase,
    check_entry_shape,
    checkout,
    entry_is_local,
    output_repair,
    sample_repair,
)
from .integrity import CHANGED, check_entry, check_output, mark_changed

#: The kinds ``wfc verify`` walks, in the order it walks and reports them.
KIND_SAMPLES = "samples"
KIND_OUTPUTS = "outputs"
KIND_OBJECTS = "objects"
KIND_CHECKOUTS = "checkouts"
KIND_BLOBS = "blobs"
VERIFY_KINDS = (KIND_SAMPLES, KIND_OUTPUTS, KIND_OBJECTS, KIND_CHECKOUTS, KIND_BLOBS)

#: Every check re-hashes: a stamp or a stat record never vouches.
_SETTING = INTEGRITY_INPUTS_HASH
_WHO = "wfc verify"


@dataclass
class VerifyProblem:
    """One thing that is not what wfc recorded.

    Attributes:
        kind: One of :data:`VERIFY_KINDS`.
        subject: What it is, e.g. ``sample 'S1' restored copy``.
        path: Where it sits.
        detail: What is wrong with it.
        action: For a repaired problem, what ``--repair`` did; otherwise
            what repairs it (``wfc verify --repair``, or the user's action).
        repaired: Whether ``--repair`` put it back.
    """

    kind: str
    subject: str
    path: str
    detail: str
    action: str
    repaired: bool = False

    def line(self) -> str:
        """The problem as one report line."""
        return f"{self.subject} at {self.path}: {self.detail}. {self.action}"


@dataclass
class VerifyReport:
    """What :func:`verify_project` found, and repaired.

    Attributes:
        problems: Every problem found, in walk order.
        repair: Whether the walk repaired what it could.
    """

    problems: list[VerifyProblem] = field(default_factory=list)
    repair: bool = False

    @property
    def remaining(self) -> list[VerifyProblem]:
        """The problems still standing (all of them without ``repair``)."""
        return [p for p in self.problems if not p.repaired]

    @property
    def repaired(self) -> list[VerifyProblem]:
        """The problems ``--repair`` put back."""
        return [p for p in self.problems if p.repaired]

    @property
    def ok(self) -> bool:
        """Whether nothing is left wrong."""
        return not self.remaining

    def as_dict(self) -> dict:
        """The report for ``--json``: every kind present, problems grouped by kind.

        Returns:
            ``{"ok", "repair", "problems": {kind: [...]}, "repaired": {kind: [...]}}``;
            ``problems`` holds only what is still wrong.
        """
        def grouped(items: list[VerifyProblem]) -> dict[str, list[dict]]:
            out: dict[str, list[dict]] = {kind: [] for kind in VERIFY_KINDS}
            for p in items:
                entry = asdict(p)
                del entry["kind"], entry["repaired"]
                out[p.kind].append(entry)
            return out

        return {"ok": self.ok, "repair": self.repair,
                "problems": grouped(self.remaining), "repaired": grouped(self.repaired)}

    def lines(self) -> list[str]:
        """The text report: one line per problem, grouped by kind.

        Returns:
            The repaired problems first (with ``--repair``), then what is
            left, each under its kind's heading, then a one-line verdict.
        """
        out: list[str] = []
        for title, items in (("repaired", self.repaired), ("problem", self.remaining)):
            for kind in VERIFY_KINDS:
                group = [p for p in items if p.kind == kind]
                if not group:
                    continue
                out.append(f"{kind} ({title}s: {len(group)}):")
                out.extend(f"  {title}: {p.line()}" for p in group)
        if self.ok:
            done = f"; {len(self.repaired)} repaired" if self.repaired else ""
            out.append(f"wfc verify: everything is what wfc recorded{done}.")
        else:
            left = len(self.remaining)
            out.append(f"wfc verify: {left} problem(s)"
                       + (" left after --repair." if self.repair else
                          ". Run `wfc verify --repair` to repair what wfc can."))
        return out


@dataclass
class _Walk:
    """State one verify walk shares across its kinds."""

    root: Path
    repair: bool
    #: Every entry some record names as pushed: the remote holds its bytes.
    pushed: set[str]
    #: Each entry's changed objects as the walk found them, checked once.
    changed: dict[str, list[str]] = field(default_factory=dict)
    #: Each pulled-again entry's outcome: None (repaired) or why not.
    repairs: dict[str, str | None] = field(default_factory=dict)
    #: Every entry whose address holds a directory tree: a malformed entry.
    malformed: set[str] = field(default_factory=set)

    def changed_objects(self, content_hash: str) -> list[str]:
        """The entry's changed local objects, re-hashed once per walk.

        A malformed entry has no objects to compare: it is recorded in
        :attr:`malformed` and reported with no changed object.
        """
        if content_hash not in self.changed:
            try:
                check_entry_shape(self.root, content_hash)
                self.changed[content_hash] = changed_objects(self.root, content_hash,
                                                             _SETTING)
            except MalformedEntryError:
                self.malformed.add(content_hash)
                self.changed[content_hash] = []
        return self.changed[content_hash]


def _pushed(status: str | None) -> bool:
    return status == PushStatus.pushed.value


def _object_path(root: Path, content_hash: str) -> str:
    return str(layout.dvc_cache_entry(root, content_hash))


def _repair_entry(walk: _Walk, content_hash: str, where: str, repair: str) -> str | None:
    """Pull a changed entry again when the remote holds it.

    Args:
        walk: The walk.
        content_hash: The entry's hash.
        where: The entry's description, for messages.
        repair: The sentence naming the user's repair.

    Returns:
        None when the entry is now what wfc recorded; otherwise why not,
        ending with the repair.
    """
    from .resolve import NotInCacheError, pull_again

    changed = walk.changed_objects(content_hash)
    if not changed:
        return None
    if content_hash not in walk.pushed:
        return f"It was never pushed, so there is nothing to pull it again from. {repair}"
    if content_hash in walk.repairs:
        # Pulled again for an earlier record naming the same entry.
        return walk.repairs[content_hash]
    try:
        pull_again(walk.root, content_hash, changed, where, repair, _WHO, _SETTING)
        why = None
    except (NotInCacheError, MalformedEntryError) as exc:
        why = str(exc)
    walk.repairs[content_hash] = why
    return why


def _entry_problem(walk: _Walk, kind: str, content_hash: str, subject: str,
                   repair: str) -> VerifyProblem | None:
    """Check one cache entry and, with ``repair``, pull it again when pushed.

    Args:
        walk: The walk.
        kind: The kind it is listed under.
        content_hash: The entry's hash.
        subject: What the entry is, e.g. ``run 3 output 'out' cache entry``.
        repair: The sentence naming the user's repair.

    Returns:
        The problem, or None when the entry is what wfc recorded.
    """
    changed = walk.changed_objects(content_hash)
    if content_hash in walk.malformed:
        # Neither a pull nor a restore converts it, pushed or not.
        return VerifyProblem(
            kind=kind, subject=subject, path=_object_path(walk.root, content_hash),
            detail=f"malformed: {MALFORMED_SHAPE}",
            action=f"wfc cannot repair it. {repair}",
        )
    if not changed:
        if content_hash in walk.pushed or entry_is_local(walk.root, content_hash):
            return None
        return VerifyProblem(
            kind=kind, subject=subject, path=_object_path(walk.root, content_hash),
            detail="lost: not in the local cache and never pushed",
            action=f"wfc cannot repair it. {repair}",
        )
    problem = VerifyProblem(
        kind=kind, subject=subject, path=_object_path(walk.root, content_hash),
        detail=f"changed in the local cache ({changed_phrase(changed)})",
        action=("Run `wfc verify --repair` to pull it again from the remote."
                if content_hash in walk.pushed else
                f"It was never pushed, so wfc cannot repair it. {repair}"),
    )
    if walk.repair:
        why = _repair_entry(walk, content_hash, f"{subject} ({content_hash})", repair)
        if why is None:
            problem.repaired = True
            problem.action = "Pulled it again from the remote."
        else:
            problem.action = why
    return problem


@task(purpose="Re-hash every registered sample's cache entry and its restored copy; "
              "with repair, pull a changed pushed entry again and restore a changed "
              "copy from its checked entry",
      inputs="the sample rows; their cache entries, restored copies and stamps",
      outputs="the samples' problems, each marked repaired or naming its repair")
def _verify_samples(walk: _Walk, samples: list[Sample]) -> list[VerifyProblem]:
    """Check each sample's cache entry, then its restored copy when present.

    Args:
        walk: The walk.
        samples: The sample rows that carry a content hash.

    Returns:
        The problems found.
    """
    from .restore import (
        MalformedSampleRecordError,
        restore_verified,
        sample_data_path,
    )

    problems: list[VerifyProblem] = []
    # Each entry's problems so far, one per sample naming it.
    entries: dict[str, list[VerifyProblem]] = {}
    for sample in samples:
        name, content_hash = sample.name, sample.content_hash
        assert content_hash is not None  # callers pass hashed samples only
        口 = Step(step_num=1.1, name="Check the sample's cache entry",
                 purpose="Re-hash its objects; with repair, pull a changed one "
                         "again when pushed")
        repair = sample_repair(name)
        entry = _entry_problem(walk, KIND_SAMPLES, content_hash,
                               f"sample '{name}' cache entry", repair)
        if entry is not None:
            problems.append(entry)
            entries.setdefault(content_hash, []).append(entry)

        口 = Step(step_num=1.2, name="Check the restored copy",
                 purpose="Re-hash the copy against the sample's hash; a copy of "
                         "a malformed entry is listed and never restored")
        try:
            dest = sample_data_path(walk.root, sample)
        except MalformedSampleRecordError:
            continue
        if dest is None or not os.path.lexists(dest):
            continue
        stamp = layout.sample_stamp(walk.root, name)
        if check_entry(dest, recorded_hash=content_hash, stamp=stamp,
                       setting=_SETTING).intact:
            continue
        problem = VerifyProblem(
            kind=KIND_SAMPLES, subject=f"sample '{name}' restored copy", path=str(dest),
            detail=f"not what wfc registered (content_hash {content_hash})",
            action="Run `wfc verify --repair` to restore it from its cache entry.",
        )
        problems.append(problem)
        if content_hash in walk.malformed:
            # Nothing restores a copy from a malformed entry, pushed or not.
            problem.action = (f"Its cache entry is malformed, so wfc cannot "
                              f"restore it. {repair}")
            continue
        if walk.repair:
            口 = AutoStep(step_num=1.3, name="Restore the copy")
            why = restore_verified(name, content_hash, content_hash in walk.pushed,
                                   dest, walk.root, _SETTING)
            if why is None:
                problem.repaired = True
                problem.action = "Restored the registered bytes from its cache entry."
                if walk.repairs.get(content_hash) is not None:
                    # The restore checked the entry first and pulled it again
                    # itself after the pull above failed: the entry is what
                    # wfc recorded now, for every record naming it.
                    walk.repairs[content_hash] = None
                    for earlier in entries.get(content_hash, []):
                        earlier.repaired = True
                        earlier.action = "Pulled it again from the remote."
            else:
                problem.action = why
    return problems


@task(purpose="Re-hash every output the archive pass has not reached against "
              "collect's hash, or check it on collect's stat record, and mark a "
              "changed one; it is never repaired",
      inputs="the pre-archive output rows; their run-archive outputs and member stamps",
      outputs="the outputs' problems, each naming the re-run; changed rows marked")
def _verify_outputs(walk: _Walk, rows: list[RunOutput]) -> list[VerifyProblem]:
    """Check each pre-archive output; a changed one is marked and listed.

    Args:
        walk: The walk.
        rows: Output rows with no content hash, on completed runs.

    Returns:
        The problems found; none is repairable.
    """
    from .resolve import outside_run_archive, run_archive_path

    problems: list[VerifyProblem] = []
    for row in rows:
        口 = Step(step_num=1.1, name="Check one output",
                 purpose="A marked row is listed; otherwise the one check against "
                         "collect's record, always re-hashing a collect hash")
        path = run_archive_path(row, walk.root)
        if path is None or outside_run_archive(row, walk.root):
            continue
        if row.integrity_status != OutputIntegrity.changed.value:
            if check_output(row, path, walk.root, _SETTING).status != CHANGED:
                continue
            mark_changed(row)
        problems.append(VerifyProblem(
            kind=KIND_OUTPUTS, subject=f"run {row.run_id} output '{row.slot}'",
            path=str(path), detail=f"changed after run {row.run_id} wrote it",
            action=f"wfc cannot repair it. {output_repair(row.run_id)}",
        ))
    return problems


@task(purpose="Re-hash every archived output's cache entry; with repair, pull a "
              "changed one again when it is recorded pushed",
      inputs="the archived output rows' content hashes",
      outputs="the objects' problems, each marked repaired or naming the re-run")
def _verify_objects(walk: _Walk, rows: list[tuple[int, str, str]]) -> list[VerifyProblem]:
    """Check each archived output's cache entry.

    Args:
        walk: The walk.
        rows: ``(run_id, slot, content_hash)`` per archived output row.

    Returns:
        The problems found.
    """
    problems: list[VerifyProblem] = []
    for run_id, slot, content_hash in rows:
        口 = Step(step_num=1.1, name="Check one archived output's entry",
                 purpose="Re-hash its objects; with repair, pull it again when pushed")
        problem = _entry_problem(walk, KIND_OBJECTS, content_hash,
                                 f"run {run_id} output '{slot}' cache entry",
                                 output_repair(run_id))
        if problem is not None:
            problems.append(problem)
    return problems


@task(purpose="Re-hash every directory checkout under the project tree; with "
              "repair, rebuild a changed one from its checked objects",
      inputs="the checkouts directory; each checkout's entry and stamp",
      outputs="the checkouts' problems, each marked repaired or naming why not")
def _verify_checkouts(walk: _Walk) -> list[VerifyProblem]:
    """Check each checkout against its ``.dir`` hash.

    Args:
        walk: The walk.

    Returns:
        The problems found.
    """
    problems: list[VerifyProblem] = []
    root_dir = layout.checkouts_dir(walk.root)
    found = sorted(p for p in root_dir.iterdir()
                   if p.is_dir() and is_directory_hash(p.name)) if root_dir.is_dir() else []
    for dest in found:
        口 = Step(step_num=1.1, name="Check one checkout",
                 purpose="Re-hash it against its hash; with repair, rebuild it")
        dir_hash = dest.name
        stamp = layout.checkout_stamp(walk.root, dir_hash)
        if check_entry(dest, recorded_hash=dir_hash, stamp=stamp, setting=_SETTING).intact:
            continue
        problem = VerifyProblem(
            kind=KIND_CHECKOUTS, subject=f"checkout of {dir_hash}", path=str(dest),
            detail="not the directory its cache entry holds",
            action="Run `wfc verify --repair` to rebuild it from the cache.",
        )
        if walk.repair:
            try:
                口 = AutoStep(step_num=1.2, name="Rebuild the checkout")
                built = checkout(dir_hash, dest, walk.root, stamp=stamp, setting=_SETTING)
                why = None if built else (
                    "Its cache entry is not complete in the local cache, so it "
                    "cannot be rebuilt here; the next read pulls the entry and "
                    "rebuilds it.")
            except (ChangedEntryError, MalformedEntryError) as exc:
                why = f"It cannot be rebuilt: {exc}."
            if why is None:
                problem.repaired = True
                problem.action = "Rebuilt it from the cache."
            else:
                problem.action = why
        problems.append(problem)
    return problems


@task(purpose="Re-hash every blob row's cache entry; with repair, pull a changed "
              "one again when it is recorded pushed",
      inputs="the blob rows",
      outputs="the blobs' problems, each marked repaired or naming what recreates it")
def _verify_blobs(walk: _Walk, blobs: list[tuple[str, str]]) -> list[VerifyProblem]:
    """Check each blob's cache entry.

    Args:
        walk: The walk.
        blobs: ``(hash, kind)`` per blob row.

    Returns:
        The problems found.
    """
    problems: list[VerifyProblem] = []
    for content_hash, kind in blobs:
        口 = Step(step_num=1.1, name="Check one blob's entry",
                 purpose="Re-hash its objects; with repair, pull it again when pushed")
        problem = _entry_problem(walk, KIND_BLOBS, content_hash,
                                 f"blob {kind} cache entry", blob_repair(kind))
        if problem is not None:
            problems.append(problem)
    return problems


@workflow(purpose="wfc verify: re-hash every sample, pre-archive output, archived "
                  "cache entry, checkout and blob wfc holds, list each that is not "
                  "what wfc recorded by kind, and with repair put back what wfc can "
                  "restore exactly",
          inputs="the project's sample, output and blob rows; its cache, run archive, "
                 "checkouts and restored samples; whether to repair",
          outputs="a VerifyReport; changed pre-archive rows marked; with repair, "
                  "copies restored, checkouts rebuilt, pushed objects pulled again")
def verify_project(project_dir: Path | str | None = None, *,
                   repair: bool = False) -> VerifyReport:
    """Check everything wfc holds against what it recorded, always re-hashing.

    Args:
        project_dir: The project root; the active project when None.
        repair: Repair what wfc can restore exactly, and list the rest.

    Returns:
        The :class:`VerifyReport`.
    """
    root = Path(project_dir) if project_dir is not None else get_project_root()

    口 = Step(step_num=1, name="Read the records",
             purpose="Read the sample, output and blob rows once; an entry is "
                     "pushed when any record naming it is recorded pushed")
    with get_session() as session:
        sample_rows = session.exec(select(Sample).order_by(col(Sample.name))).all()
        samples = [s for s in sample_rows if s.content_hash]
        pre_archive = list(session.exec(
            select(RunOutput)
            .join(Run, col(RunOutput.run_id) == col(Run.id))
            .where(col(RunOutput.content_hash).is_(None))
            .where(Run.status == "completed")
            .order_by(col(RunOutput.id))
        ).all())
        archived_rows = session.exec(
            select(RunOutput).where(col(RunOutput.content_hash).isnot(None))
            .order_by(col(RunOutput.id))
        ).all()
        archived = [(r.run_id, r.slot or "", r.content_hash) for r in archived_rows]
        blob_rows = session.exec(select(Blob).order_by(col(Blob.hash))).all()
        blobs = [(b.hash, b.kind) for b in blob_rows]
        pushed = ({s.content_hash for s in sample_rows if _pushed(s.push_status)}
                  | {r.content_hash for r in archived_rows if _pushed(r.push_status)}
                  | {b.hash for b in blob_rows if _pushed(b.push_status)})
        session.expunge_all()
    walk = _Walk(root=root, repair=repair, pushed=pushed)
    report = VerifyReport(repair=repair)

    口 = AutoStep(step_num=2, name="Samples")
    found = _verify_samples(walk, samples)
    report.problems.extend(found)

    口 = AutoStep(step_num=3, name="Pre-archive outputs")
    found = _verify_outputs(walk, pre_archive)
    report.problems.extend(found)

    口 = AutoStep(step_num=4, name="Archived outputs' cache entries")
    found = _verify_objects(walk, archived)
    report.problems.extend(found)

    口 = AutoStep(step_num=5, name="Checkouts")
    found = _verify_checkouts(walk)
    report.problems.extend(found)

    口 = AutoStep(step_num=6, name="Blobs")
    found = _verify_blobs(walk, blobs)
    report.problems.extend(found)
    return report
