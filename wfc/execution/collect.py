"""Collect phase: turn the method's exit into outputs and metrics.

Reads the single results channel (``_wfc_results.json``), locates the node's
declared output slots in the run-archive dir, and records one RunOutput row
per slot, carrying the slot — the sole writer of those rows. A missing
declared slot reports the ``missing-slot`` ending, and two slots saved to one
file report the ``duplicate-output`` ending, and a directory output wfc
cannot identify (a symlink inside, a case-only name collision, no files)
reports the ``refused-output`` ending, for the record tail to compose. A
directory output's row carries its total size and newest mtime.

Slot names (and the single ``output`` slot of a node that declares none)
come from ``resolve_node_outputs``; the run-archive dir / DVC cache is the
authoritative store.
"""
from __future__ import annotations

import os
from pathlib import Path

from axiom_annotations import task, Step

from .. import layout
from ..identity import DirectoryContentError, DirectoryManifest, directory_manifest
from ..persistence import get_session, project_root as get_project_root


@task(purpose="Collect phase: read the results manifest, scan declared output "
              "slots, and record RunOutput rows",
      inputs="run id, node output-slot declarations",
      outputs="output file list + metrics, or the missing-slot, "
              "duplicate-output or refused-output ending")
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
        symlink, a case-only name collision or no files.
    """

    run_dir = layout.run_archive_dir(get_project_root(), run_id)

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

    口 = Step(step_num=4, name="Refuse a directory output wfc cannot identify",
             purpose="Read each directory output's manifest before any row is "
                     "written; a symlink inside, a case-only name collision or "
                     "an empty directory fails the run naming the slot and the "
                     "offending path, and a readable one supplies the row's "
                     "total size and newest mtime")
    dir_manifests: dict[str, DirectoryManifest] = {}
    for slot_name, archive_entry in located:
        if not archive_entry.is_dir():
            continue
        try:
            dir_manifests[slot_name] = directory_manifest(archive_entry)
        except DirectoryContentError as exc:
            error_msg = (
                f"Method '{method_name}' saved output slot '{slot_name}' as a "
                f"directory wfc cannot store: {exc}"
            )
            return {"ok": False, "ending": "refused-output",
                    "error_message": error_msg, "error_traceback": error_msg}

    口 = Step(step_num=5, name="Record one row per slot",
             purpose="Insert or update the RunOutput row keyed by (run_id, slot), "
                     "carrying the slot and the saved file's name "
                     "(content_hash=NULL — deferred archiving)")
    # Deferred archiving: wfc.identity.hash_path and cache_file are NOT called here.
    # Content hashing happens in the post-pipeline archive pass.
    # Determine artifact_type: slot-declared files are module outputs.
    artifact_type = "module_file" if slot_outputs else "method_file"
    output_files_in_archive: list[str] = []
    for slot_name, archive_entry in located:
        output_files_in_archive.append(str(archive_entry))

        # One row per (run_id, slot); output_name keeps the saved file's
        # name for display (content_hash=NULL).
        try:
            from sqlmodel import select

            from ..persistence import RunOutput
            with get_session() as session:
                existing = session.exec(
                    select(RunOutput).where(
                        RunOutput.run_id == run_id,
                        RunOutput.slot == slot_name,
                    )
                ).first()
                if slot_name in dir_manifests:
                    file_size = dir_manifests[slot_name].total_size
                    file_mtime = dir_manifests[slot_name].newest_mtime
                elif archive_entry.is_file():
                    stat = archive_entry.stat()
                    file_size = stat.st_size
                    file_mtime = stat.st_mtime
                else:
                    file_size = None
                    file_mtime = None
                if existing:
                    existing.output_name = archive_entry.name
                    existing.artifact_path = str(archive_entry)
                    existing.artifact_type = artifact_type
                    existing.file_size = file_size
                    existing.file_mtime = file_mtime
                else:
                    session.add(RunOutput(
                        run_id=run_id, slot=slot_name,
                        output_name=archive_entry.name,
                        artifact_path=str(archive_entry), artifact_type=artifact_type,
                        file_size=file_size, file_mtime=file_mtime,
                    ))
                session.commit()
        except Exception:
            pass  # Best-effort DB write

    return {"ok": True, "output_files": output_files_in_archive, "metrics": metrics}
