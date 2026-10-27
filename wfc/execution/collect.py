"""Collect phase: turn the method's exit into outputs and metrics.

Reads the single results channel (``_wfc_results.json``), locates the node's
declared output slots in the run-archive dir, and records one RunOutput row
per slot, carrying the slot — the sole writer of those rows. A missing
declared slot reports the ``missing-slot`` ending, and two slots saved to one
file report the ``duplicate-output`` ending, and a directory output wfc
cannot identify (a symlink inside, a case-only name collision, no files)
reports the ``refused-output`` ending, for the record tail to compose. An
output whose stat record or row wfc cannot write (the stat record, or the one
transaction that writes every row) reports the ``unrecorded-output`` ending,
naming the slot.

Collect reads no output byte under the default ``[integrity] inputs``: it
lists a directory with Identity's stat-only walk, then Storage makes each
output read-only and takes its stat record (a directory's member list goes
in its member stamp), which the row carries. Under ``hash`` it also hashes
every output into the row's collect hash (never ``content_hash``), and an
output it cannot read reports ``refused-output``.

Slot names (and the single ``output`` slot of a node that declares none)
come from ``resolve_node_outputs``; the run-archive dir / DVC cache is the
authoritative store.
"""
from __future__ import annotations

import os
from pathlib import Path

from axiom_annotations import AutoStep, Step, task

from .. import layout
from ..identity import (
    DirectoryContentError,
    directory_listing,
    directory_manifest,
    hash_path,
)
from ..persistence import INTEGRITY_INPUTS_HASH, write_transaction
from ..persistence import project_root as get_project_root
from ..storage import inputs_setting, record_output


@task(purpose="Collect phase: read the results manifest, scan declared output "
              "slots, and record RunOutput rows",
      inputs="run id, node output-slot declarations",
      outputs="output file list + metrics, or the missing-slot, "
              "duplicate-output, refused-output or unrecorded-output ending")
def run_collect(
    method_name: str,
    run_id: int,
    node_cfg: dict,
    slot_outputs: dict,
) -> dict:
    """Collect the method's declared outputs and metrics.

    Single results channel: ``_wfc_results.json`` is the one channel
    for declared outputs AND metrics. When present (Tier-1 via wfc-client,
    or a Tier-2 method that wrote it by hand), it supplies metrics and the
    recorded path for each declared output (which may live under
    ``_workdir/``). When absent (pure Tier-2, outputs-only), metrics default
    to empty and declared outputs are located by scanning run_dir for their
    filenames. There is no second channel.

    Args:
        method_name: Resolved method name (for the missing-slot error).
        run_id: The registered run's ID.
        node_cfg: The node's pipeline-JSON config (drives the slot scan,
            including the single ``output`` slot for empty ``slot_outputs``).
        slot_outputs: Declared output filenames per slot.

    Returns:
        ``{"ok": True, "output_files": [...], "metrics": {...}}`` on
        success; ``{"ok": False, "ending": "missing-slot",
        "error_message": ..., "error_traceback": ...}`` when a declared
        slot was not produced; or ``{"ok": False, "ending":
        "duplicate-output", "error_message": ..., "error_traceback": ...}``
        when two slots resolve to the same file; or ``{"ok": False,
        "ending": "refused-output", ...}`` when a directory output holds a
        symlink, a case-only name collision or no files; or ``{"ok": False,
        "ending": "unrecorded-output", ...}`` when an output's stat record or
        its row could not be written (naming the slot; every slot when the
        row transaction failed to commit).
    """
    project_root = get_project_root()
    run_dir = layout.run_archive_dir(project_root, run_id)

    口 = Step(step_num=1, name="Read results manifest",
             purpose="Read _wfc_results.json (single channel) for metrics and "
                     "manifest-recorded output paths")
    from ..manifest import read_results_manifest
    manifest = read_results_manifest(run_dir)
    metrics = dict(manifest.metrics) if manifest is not None else {}
    manifest_outputs = manifest.outputs if manifest is not None else {}

    口 = Step(step_num=2, name="Locate each declared slot's output",
             purpose="For each declared slot, take the path the manifest records "
                     "for it, else scan run_dir for the declared file name, and "
                     "verify it exists; fail the run on a missing declared slot")
    # Scan outputs slot-first. Slot names come from the node's
    # declared slot_outputs via resolve_node_outputs; a node with empty
    # slot_outputs has one "output" slot with a generated file name. The base
    # path passed here is irrelevant — only the slot names and the file names
    # are used.
    from ..contracts import resolve_node_outputs
    slot_map = resolve_node_outputs(node_cfg, Path("."))  # {slot: filename path}

    located: list[tuple[str, Path]] = []
    for slot_name, fallback_path in slot_map.items():
        filename = slot_outputs.get(slot_name, fallback_path.name)
        # Prefer the manifest-recorded path for this output (Tier-1 may have
        # written it under _workdir/); fall back to the run_dir scan path.
        if slot_name in manifest_outputs:
            archive_entry = manifest_outputs[slot_name]
        else:
            archive_entry = run_dir / filename
        if not archive_entry.exists():
            # Method honored pre_run but failed to produce a declared slot.
            error_msg = (
                f"Method '{method_name}' did not produce declared slot "
                f"'{slot_name}' (expected at {archive_entry})"
            )
            return {"ok": False, "ending": "missing-slot",
                    "error_message": error_msg, "error_traceback": error_msg}
        located.append((slot_name, archive_entry))

    口 = Step(step_num=3, name="Refuse two slots saved to one file",
             purpose="Fail the run before any row is written when two slots "
                     "resolve to the same path, naming both slots and the path")
    # Outside the best-effort write below, so this failure ends the run.
    seen: dict[str, str] = {}
    for slot_name, archive_entry in located:
        key = os.path.normcase(str(archive_entry.resolve()))
        prior = seen.get(key)
        if prior is not None:
            error_msg = (
                f"Method '{method_name}' saved output slots '{prior}' and "
                f"'{slot_name}' to the same file ({archive_entry}); each "
                f"output slot needs its own file"
            )
            return {"ok": False, "ending": "duplicate-output",
                    "error_message": error_msg, "error_traceback": error_msg}
        seen[key] = slot_name

    口 = Step(step_num=4, name="Refuse an output wfc cannot identify",
             purpose="List each directory output with Identity's stat-only walk "
                     "before any row is written (under [integrity] inputs = hash, "
                     "its manifest from the same walk, and each file output's "
                     "hash); a symlink inside, a case-only name collision, an "
                     "empty directory or, under hash, an output that cannot be "
                     "read fails the run naming the slot and the offending path",
             critical="Under the default no output byte is read here: collect "
                      "is on the step's path, and the archive pass hashes")
    hash_outputs = inputs_setting(project_root) == INTEGRITY_INPUTS_HASH
    collect_hashes: dict[str, str] = {}
    for slot_name, archive_entry in located:
        try:
            if archive_entry.is_dir():
                if hash_outputs:
                    collect_hashes[slot_name] = directory_manifest(archive_entry).hash
                else:
                    directory_listing(archive_entry)
            elif hash_outputs:
                collect_hashes[slot_name] = hash_path(archive_entry)
        except DirectoryContentError as exc:
            error_msg = (
                f"Method '{method_name}' saved output slot '{slot_name}' as a "
                f"directory wfc cannot store: {exc}"
            )
            return {"ok": False, "ending": "refused-output",
                    "error_message": error_msg, "error_traceback": error_msg}
        except OSError as exc:
            # Under the default nothing is hashed here: only the listing
            # of a directory reads the output.
            cannot = "read to hash it" if hash_outputs else "read or list"
            error_msg = (
                f"Method '{method_name}' saved output slot '{slot_name}' at "
                f"{archive_entry}, which wfc cannot {cannot}: {exc}"
            )
            return {"ok": False, "ending": "refused-output",
                    "error_message": error_msg, "error_traceback": error_msg}

    口 = Step(step_num=5, name="Take each output's stat record",
             purpose="Make each output read-only and take its stat record "
                     "through Storage (a directory's member list goes in its "
                     "member stamp), before any row is written; a failure "
                     "fails the run naming the slot",
             critical="Collect is the one writer of an output's stat record; "
                      "the collect hash never goes in content_hash")
    # Deferred archiving: no output is cached here, and content_hash stays
    # NULL until the archive pass copies the output and verifies the copy.
    # Determine artifact_type: slot-declared files are module outputs.
    artifact_type = "module_file" if slot_outputs else "method_file"
    output_files_in_archive = [str(entry) for _slot, entry in located]
    stats: dict = {}
    for slot_name, archive_entry in located:
        try:
            口 = AutoStep(step_num=5.1, name="Take the output's stat record")
            stats[slot_name] = record_output(archive_entry, project_dir=project_root,
                                             run_id=run_id, slot=slot_name)
        except Exception as exc:  # noqa: BLE001 -- the run fails, naming it
            return _unrecorded(method_name, [slot_name], exc, archive_entry)

    口 = Step(step_num=6, name="Write every output row in one transaction",
             purpose="Insert or update the RunOutput row keyed by (run_id, "
                     "slot) for every slot, with the saved file's name, the "
                     "stat columns and, under hash, the collect hash "
                     "(content_hash=NULL -- deferred archiving), in one write "
                     "transaction; a failed write fails the run with the "
                     "unrecorded-output ending naming the slot, and no row "
                     "of the run is written",
             critical="Every row lands or none does; a failure is the run's "
                      "ending, never a warning")
    writing: list[str] = []
    try:
        with write_transaction() as session:
            for slot_name, archive_entry in located:
                writing = [slot_name]
                _write_output_row(session, run_id, slot_name, archive_entry,
                                  artifact_type, stats[slot_name],
                                  collect_hashes.get(slot_name))
            # A failure from here is the commit's, which writes every slot.
            writing = [slot for slot, _entry in located]
    except Exception as exc:  # noqa: BLE001 -- the run fails, naming it
        return _unrecorded(method_name, writing, exc)

    return {"ok": True, "output_files": output_files_in_archive, "metrics": metrics}


def _write_output_row(session, run_id: int, slot: str, archive_entry: Path,
                      artifact_type: str, stat, collect_hash: str | None) -> None:
    """Insert or update one slot's RunOutput row inside collect's transaction.

    Args:
        session: Collect's write transaction.
        run_id: The run.
        slot: The output slot.
        archive_entry: The saved output.
        artifact_type: ``module_file`` or ``method_file``.
        stat: The output's stat record (``storage.record_output``).
        collect_hash: The collect hash under ``[integrity] inputs = hash``.
    """
    from sqlmodel import select

    from ..persistence import RunOutput

    columns = {
        "file_size": stat.file_size,
        "file_mtime": stat.file_mtime,
        "stat_mtime_ns": stat.stat_mtime_ns,
        "stat_ctime_ns": stat.stat_ctime_ns,
        "stat_file_index": stat.stat_file_index,
        "member_count": stat.member_count,
        "stat_xattr_names": stat.stat_xattr_names,
        "collect_hash": collect_hash,
    }
    # One row per (run_id, slot); output_name keeps the saved file's name
    # for display.
    existing = session.exec(
        select(RunOutput).where(RunOutput.run_id == run_id, RunOutput.slot == slot)
    ).first()
    if existing:
        existing.output_name = archive_entry.name
        existing.artifact_path = str(archive_entry)
        existing.artifact_type = artifact_type
        for name, value in columns.items():
            setattr(existing, name, value)
        session.add(existing)
    else:
        session.add(RunOutput(
            run_id=run_id, slot=slot, output_name=archive_entry.name,
            artifact_path=str(archive_entry), artifact_type=artifact_type,
            **columns,
        ))
    session.flush()


def _unrecorded(method_name: str, slots: list[str], exc: Exception,
                archive_entry: Path | None = None) -> dict:
    """The unrecorded-output ending: wfc could not record an output's row.

    Args:
        method_name: The method's name.
        slots: The slot (or, for a failed commit, every slot) being written.
        exc: The failure.
        archive_entry: The saved output, when one slot is named.

    Returns:
        The failed collect result.
    """
    names = ", ".join(f"'{s}'" for s in slots)
    where = f" at {archive_entry}" if archive_entry is not None else ""
    noun = "slot" if len(slots) == 1 else "slots"
    error_msg = (f"Method '{method_name}' saved output {noun} {names}{where}, "
                 f"but wfc could not record it: {exc}")
    return {"ok": False, "ending": "unrecorded-output",
            "error_message": error_msg, "error_traceback": error_msg}
