"""The ``wfc export`` engine: correct bytes or a clear error, never a partial export.

Every requested output is resolved before anything is printed or
written, so a run whose outputs are not all archived produces no
half-filled destination directory and no misleading path on stdout.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiom_annotations import Step, task


class ExportPathError(ValueError):
    """An output's local path cannot be given; the message says why."""


@dataclass(frozen=True)
class ExportPath:
    """One output's read-only local path, as ``wfc export --path`` gives it.

    Attributes:
        slot: The output slot.
        name: The name it is exported under (its file or directory name; with
            ``--all``, its slot folder when two outputs share a file name).
        path: Its local path: the cache entry or checkout of a recorded run's
            output, the file in its draft's folder for a draft step's.
    """

    slot: str
    name: str
    path: Path


def _recorded_outputs(run_id: int, slot: str | None,
                      export_all: bool) -> list[tuple[Any, Path, str]]:
    """Every requested output of a run: its row, its cache path, its export name.

    Raises:
        ResolveOutputError: A bad slot, a malformed record, an un-archived
            row, a cache miss or outputs that cannot be named apart.
    """
    from ..storage import (
        exportable_outputs,
        output_export_name,
        output_export_names,
        resolve_output,
    )

    if export_all:
        resolved = exportable_outputs(run_id)
        names = output_export_names([ro for ro, _ in resolved])
        return [(ro, path, name) for (ro, path), name in zip(resolved, names, strict=True)]
    cache_path, ro = resolve_output(run_id, slot, pull=True)
    return [(ro, cache_path, output_export_name(ro))]


def _draft_outputs(ref: str, step: dict, slot: str | None,
                   export_all: bool) -> list[tuple[str, Path]] | str:
    """Every requested output of a draft step: ``(slot, path)``, or the refusal."""
    from ..execution.draft import draft_step_output

    outputs = step.get("outputs") or {}
    slots: list[str] = list(outputs) if slot is None or export_all else [slot]
    located: list[tuple[str, Path]] = []
    for wanted in slots:
        found = draft_step_output(ref, wanted)
        if found.path is None:
            return f"{ref}: {found.refusal}"
        located.append((wanted, found.path))
    return located


@task(purpose="Give the read-only local path of a run's or a draft step's "
              "output(s) without copying, or refuse saying why",
      inputs="a run id or a draft string, a slot or every output",
      outputs="list[ExportPath]: slot, export name and local path per output",
      critical="reads only; every output is resolved before any is given")
def export_paths(run: int | str, slot: str | None = None,
                 export_all: bool = False) -> list[ExportPath]:
    """The local path of each requested output, as ``wfc export --path`` gives it.

    Args:
        run: A recorded run's id, or a draft string
            (``draft:<module>.<method>/<node>@<sample>``).
        slot: The output slot; ignored with ``export_all``.
        export_all: Every output of the run.

    Returns:
        One :class:`ExportPath` per requested output.

    Raises:
        ExportPathError: The run, the draft step or an output cannot be
            resolved (the resolver's words).
    """
    from ..execution.draft import DraftRefError, is_draft_ref, resolve_draft_step
    from ..storage import ResolveOutputError

    if isinstance(run, str) and is_draft_ref(run):
        try:
            _ref, _record, step = resolve_draft_step(run)
        except DraftRefError as exc:
            raise ExportPathError(str(exc)) from exc
        located = _draft_outputs(run, step, slot, export_all)
        if isinstance(located, str):
            raise ExportPathError(located)
        return [ExportPath(slot=s, name=p.name, path=p) for s, p in located]
    try:
        resolved = _recorded_outputs(int(run), slot, export_all)
    except ResolveOutputError as exc:
        raise ExportPathError(str(exc)) from exc
    return [ExportPath(slot=str(ro.slot or name), name=name, path=path)
            for ro, path, name in resolved]


@task(purpose="Export a run's archived output(s) to a user-owned destination")
def export_output(
    *,
    run_id: int,
    slot: str | None = None,
    dest: str | None = None,
    export_all: bool = False,
    path_only: bool = False,
    force: bool = False,
) -> int:
    """Export a run's output(s): mutable copy out of the cache, or ``--path``.

    Resolution happens up front for every requested output — on any
    resolution error nothing is printed to stdout and no file is produced
    (correct bytes or a clear error, never a partial export).

    Args:
        run_id: The run's integer id.
        slot: The output slot to export; ``None`` without ``export_all``
            lists the run's slots with their file names on stdout and
            returns 0.
        dest: Destination file/directory (copy mode).
        export_all: Export every output of the run into ``dest`` (a
            directory) — or print every path with ``path_only``.
        path_only: Print the resolved read-only cache path(s) on stdout
            instead of copying.
        force: Overwrite existing destination files.

    Returns:
        0 on success, 1 on any error.
    """
    from ..storage import (
        ResolveOutputError,
        copy_out,
        output_export_name,
        output_listing,
    )

    if slot is None and not export_all:
        口 = Step(step_num=1, name="List the run's outputs",
                 purpose="With no slot the answer is the listing: each output's "
                         "slot and file name on stdout, exit 0; a run that does "
                         "not exist or has a malformed record is still refused")
        try:
            listing = output_listing(run_id)
        except ResolveOutputError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if not listing:
            print(f"Run {run_id} has no recorded outputs.")
            return 0
        width = max(len("SLOT"), *(len(s) for s, _ in listing))
        print(f"{'SLOT'.ljust(width)}  FILE")
        for listed_slot, file_name in listing:
            print(f"{listed_slot.ljust(width)}  {file_name}")
        return 0

    口 = Step(step_num=2, name="Resolve every requested output",
             purpose="Fail before producing anything: a bad slot, a malformed "
                     "record, an un-archived row, a cache miss or outputs "
                     "that cannot be named apart end the export with nothing "
                     "written and nothing on stdout")

    # (RunOutput row, cache path, export name) for every requested output.
    try:
        triples = _recorded_outputs(run_id, slot, export_all)
    except ResolveOutputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    resolved: list[tuple[Any, Path]] = [(ro, path) for ro, path, _ in triples]
    names: list[str] = [name for _, _, name in triples]

    if path_only:
        口 = Step(step_num=3, name="Print the cache path(s)",
                 purpose="stdout carries only the path(s), script-friendly, "
                         "each named by its slot with --all as a draft step's "
                         "are; the read-only warning goes to stderr")
        print(
            "WARNING: the printed path points into the read-only DVC cache "
            "(managed storage). Read it, don't modify it; use a copy export "
            "for a writable file.",
            file=sys.stderr,
        )
        if export_all:
            for name, (ro, cache_path) in zip(names, resolved):
                print(f"{ro.slot or name}\t{cache_path}")
        else:
            print(str(resolved[0][1]))
        return 0

    # -- copy mode --
    if dest is None:
        # cli_main normally rejects this at argparse level (exit 2); kept
        # as a guard for direct callers.
        print("ERROR: destination required (or use --path).", file=sys.stderr)
        return 1

    if export_all:
        口 = Step(step_num=4, name="Copy every output into the destination",
                 purpose="Each output under its export name (its file name, or "
                         "its slot folder when two outputs share a file name), "
                         "every existing destination named and refused before "
                         "the first copy, then one writable copy per output")
        dest_dir = Path(dest)
        if dest_dir.exists() and not dest_dir.is_dir():
            print(
                f"ERROR: --all destination must be a directory: {dest_dir}",
                file=sys.stderr,
            )
            return 1
        dest_dir.mkdir(parents=True, exist_ok=True)

        targets: list[tuple[str, Path, Path]] = [
            (name, cache_path, dest_dir / name)
            for name, (_, cache_path) in zip(names, resolved)
        ]

        conflicts = [t for _, _, t in targets if t.exists()]
        if conflicts and not force:
            existing = ", ".join(str(c) for c in conflicts)
            print(
                f"ERROR: destination(s) already exist: {existing}. "
                f"Use --force to overwrite.",
                file=sys.stderr,
            )
            return 1

        for name, cache_path, target in targets:
            copy_out(cache_path, target, force)
            print(f"Exported {name} -> {target}")
        return 0

    口 = Step(step_num=5, name="Copy the one selected output out",
             purpose="Place the file (a directory destination receives it "
                     "under its original name), refuse an existing "
                     "destination without --force, then copy it writable")
    ro, cache_path = resolved[0]
    dest_path = Path(dest)
    if dest_path.is_dir():
        # Existing-directory destination: place basename(artifact_path)
        # inside it under the output's original name.
        base = (
            Path(ro.artifact_path).name if ro.artifact_path
            else output_export_name(ro)
        )
        dest_path = dest_path / base
    if dest_path.exists() and not force:
        print(
            f"ERROR: destination already exists: {dest_path}. "
            f"Use --force to overwrite.",
            file=sys.stderr,
        )
        return 1
    copy_out(cache_path, dest_path, force)
    print(f"Exported {ro.output_name} -> {dest_path}")
    return 0


@task(purpose="Export a draft step's output(s) from its draft's latest draft "
              "run, saying which run it used, or refuse saying why")
def export_draft_output(
    *,
    ref: str,
    slot: str | None = None,
    dest: str | None = None,
    export_all: bool = False,
    path_only: bool = False,
    force: bool = False,
) -> int:
    """Export a draft step's output(s), named by its draft string.

    The string means the latest draft run of its draft; which run that was
    is said on stderr. Every requested output is resolved before anything is
    written, as for a run.

    Args:
        ref: The draft string, ``draft:<module>.<method>/<node>@<sample>``.
        slot: The output to export; ``None`` without ``export_all`` lists
            the step's outputs.
        dest: Destination file or directory (copy mode).
        export_all: Export every output into ``dest`` (a directory).
        path_only: Print the output's path inside the draft folder instead
            of copying.
        force: Overwrite existing destination files.

    Returns:
        0 on success, 1 on a refusal (the draft is gone, registered or has no
        draft run, the node or sample is not in it, the step did not
        complete, or the output is missing).
    """
    from ..execution.draft import (
        DraftRefError,
        draft_run_label,
        resolve_draft_step,
    )
    from ..storage import copy_out

    口 = Step(step_num=1, name="Resolve the draft string",
             purpose="The latest draft run's step, or the resolver's refusal "
                     "naming why; the run used is said on stderr")
    try:
        _ref, record, step = resolve_draft_step(ref)
    except DraftRefError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    outputs = step.get("outputs") or {}
    label = draft_run_label(record)
    if slot is None and not export_all:
        print(f"{ref}: from {label}, {step.get('status')}.", file=sys.stderr)
        if not outputs:
            print(f"{ref} has no outputs.")
            return 0
        width = max(len("SLOT"), *(len(s) for s in outputs))
        print(f"{'SLOT'.ljust(width)}  FILE")
        for listed, rel in outputs.items():
            print(f"{listed.ljust(width)}  {Path(rel).name}")
        return 0

    口 = Step(step_num=2, name="Locate every requested output",
             purpose="Each output inside the draft folder, or the refusal; "
                     "nothing is written before all are found")
    # Past the listing above, a missing slot means export_all.
    located = _draft_outputs(ref, step, slot, export_all)
    if isinstance(located, str):
        print(f"ERROR: {located}", file=sys.stderr)
        return 1
    print(f"{ref}: from {label}.", file=sys.stderr)

    if path_only:
        for name, path in located:
            print(f"{name}\t{path}" if export_all else str(path))
        return 0
    if dest is None:
        print("ERROR: destination required (or use --path).", file=sys.stderr)
        return 1

    口 = Step(step_num=3, name="Copy the outputs out",
             purpose="Into a directory under their file names, or onto the "
                     "one destination; an existing one is refused without --force")
    dest_path = Path(dest)
    if export_all or dest_path.is_dir():
        if dest_path.exists() and not dest_path.is_dir():
            print(f"ERROR: --all destination must be a directory: {dest_path}",
                  file=sys.stderr)
            return 1
        targets = [(name, path, dest_path / path.name) for name, path in located]
    else:
        targets = [(located[0][0], located[0][1], dest_path)]
    conflicts = [str(t) for _, _, t in targets if t.exists()]
    if conflicts and not force:
        print(f"ERROR: destination(s) already exist: {', '.join(conflicts)}. "
              f"Use --force to overwrite.", file=sys.stderr)
        return 1
    if targets and not targets[0][2].parent.exists():
        targets[0][2].parent.mkdir(parents=True, exist_ok=True)
    for name, path, target in targets:
        copy_out(path, target, force)
        print(f"Exported {name} -> {target}")
    return 0
