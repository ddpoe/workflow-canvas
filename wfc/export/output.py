"""The ``wfc export`` engine: correct bytes or a clear error, never a partial export.

Every requested output is resolved before anything is printed or
written, so a run whose outputs are not all archived produces no
half-filled destination directory and no misleading path on stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from axiom_annotations import Step, task


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
        slot: The output slot to export; ``None`` triggers the discovery
            error listing the run's slots with their file names.
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
        exportable_outputs,
        output_export_name,
        output_export_names,
        resolve_output,
    )

    口 = Step(step_num=1, name="Resolve every requested output",
             purpose="Fail before producing anything: a bad slot, a malformed "
                     "record, an un-archived row, a cache miss or outputs "
                     "that cannot be named apart end the export with nothing "
                     "written and nothing on stdout")

    # (RunOutput row, cache path) for every requested output; with --all,
    # also the name each one is exported under.
    resolved: list[tuple[Any, Path]] = []
    names: list[str] = []
    try:
        if export_all:
            resolved = exportable_outputs(run_id)
            names = output_export_names([ro for ro, _ in resolved])
        else:
            # slot=None raises UnknownOutputError listing the run's slots —
            # that error IS the discovery listing.
            cache_path, ro = resolve_output(run_id, slot, pull=True)
            resolved.append((ro, cache_path))
    except ResolveOutputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if path_only:
        口 = Step(step_num=2, name="Print the cache path(s)",
                 purpose="stdout carries only the path(s), script-friendly; "
                         "the read-only warning goes to stderr")
        print(
            "WARNING: the printed path points into the read-only DVC cache "
            "(managed storage). Read it, don't modify it; use a copy export "
            "for a writable file.",
            file=sys.stderr,
        )
        if export_all:
            for name, (_, cache_path) in zip(names, resolved):
                print(f"{name}\t{cache_path}")
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
        口 = Step(step_num=3, name="Copy every output into the destination",
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
            listing = ", ".join(str(c) for c in conflicts)
            print(
                f"ERROR: destination(s) already exist: {listing}. "
                f"Use --force to overwrite.",
                file=sys.stderr,
            )
            return 1

        for name, cache_path, target in targets:
            copy_out(cache_path, target, force)
            print(f"Exported {name} -> {target}")
        return 0

    口 = Step(step_num=4, name="Copy the one selected output out",
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
