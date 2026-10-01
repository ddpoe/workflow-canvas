"""The source snapshot: its write, its layout and its stale-script purge.

Method registration's last step copies the registered script set into
``methods/<name>/``, so the code fingerprint reads the registered copy.
Declared helpers from outside the method directory are copied under
``HELPER_SNAPSHOT_DIR``; the purge removes snapshot scripts that the
method no longer declares.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from axiom_annotations import task

from .. import layout
from ..identity import collect_method_scripts
from ..persistence import project_root as get_project_root

# Reserved snapshot subdirectory for out-of-dir declared helpers (strict
# helpers mode). Out-of-dir helpers are copied under
# ``methods/<name>/_wfc_helpers/<project-relative-path>`` so the snapshot
# layout never contains ``..`` and the fingerprint (which walks the
# snapshot) covers them by construction.
HELPER_SNAPSHOT_DIR = "_wfc_helpers"


def _purge_stale_snapshot_scripts(
    registered_dir: Path,
    keep: set[Path],
) -> None:
    """Remove recognized-extension script files not in the registered set.

    Step 8 rebuilds the snapshot at ``methods/<name>/`` on every
    registration, but an earlier registration may have left recognized
    script files behind (e.g. a script that was renamed, deleted, or is no
    longer declared). Those ghosts would linger in the snapshot and leak
    into the code fingerprint — breaking "snapshot = exactly the registered
    set". This purge removes them in BOTH modes (strict and permissive).
    Non-script files (data, config, ``method.yaml``) are never touched.

    Args:
        registered_dir: The snapshot directory ``methods/<name>/``.
        keep: Resolved destination paths of the freshly registered script
            set (main script + helpers / collected scripts).
    """
    if not registered_dir.exists():
        return
    for p in collect_method_scripts(registered_dir):
        if p.resolve() not in keep:
            rel = p.relative_to(registered_dir).as_posix()
            p.unlink()
            print(f"  source snapshot: purged stale script {rel}")


def _backup_snapshot(registered_dir: Path) -> Path | None:
    """Copy an existing snapshot aside, so a refused registration can put it back.

    Args:
        registered_dir: The snapshot directory ``methods/<name>/``.

    Returns:
        A temporary directory holding the copy, or ``None`` when there was no
        snapshot to copy.
    """
    if not registered_dir.exists():
        return None
    import tempfile
    holder = Path(tempfile.mkdtemp(prefix="wfc-snapshot-"))
    backup = holder / "snapshot"
    shutil.copytree(registered_dir, backup)
    return backup


def _restore_snapshot(registered_dir: Path, backup: Path | None) -> None:
    """Put the snapshot back as it was before the registration began.

    With no backup the registration created the directory, so it is removed.

    Args:
        registered_dir: The snapshot directory ``methods/<name>/``.
        backup: What :func:`_backup_snapshot` returned.
    """
    if registered_dir.exists():
        shutil.rmtree(registered_dir)
    if backup is not None:
        shutil.copytree(backup, registered_dir)


def _discard_backup(backup: Path | None) -> None:
    """Remove the temporary copy :func:`_backup_snapshot` made.

    Args:
        backup: What :func:`_backup_snapshot` returned.
    """
    if backup is not None:
        shutil.rmtree(backup.parent, ignore_errors=True)


@task(
    purpose="Snapshot method source files into methods/{method_name}/ so the "
            "code fingerprint is always computed from the registered copy, not "
            "whatever is on disk at run time",
    inputs="method directory, method name, main script, helpers mode, declared helpers",
    outputs="methods/{method_name}/ holding the registered script set and method.yaml",
)
def _write_snapshot(
    method_dir: Path,
    method_name: str,
    script_path: Path,
    strict_helpers: bool,
    helper_paths: list[Path],
) -> None:
    """Step 8 of method registration: write the source snapshot.

    In strict helpers mode the snapshot holds exactly the main script, the
    declared helpers and ``method.yaml``, and stale scripts are purged. In
    permissive mode, a method registered from outside ``methods/<name>/``
    has the scripts ``collect_method_scripts`` finds copied (the set the
    code fingerprint hashes) and stale ones purged; a method registered in
    place is left as it is.

    Args:
        method_dir: The method directory being registered, resolved.
        method_name: The method's name; the snapshot is ``methods/<name>/``.
        script_path: The main script.
        strict_helpers: True when ``method.yaml`` has a ``helpers:`` key.
        helper_paths: The declared helpers, resolved.
    """
    project_dir = get_project_root()
    registered_dir = layout.method_dir(project_dir, method_name)
    in_place = method_dir.resolve() == registered_dir.resolve()

    if strict_helpers:
        # Strict helpers mode: snapshot exactly main script + declared helpers
        # + method.yaml. Out-of-dir helpers land under the reserved
        # HELPER_SNAPSHOT_DIR mirroring their project-relative path (no
        # `..` in the snapshot layout). The reserved dir is rebuilt from
        # scratch so undeclared stale helpers never linger in the
        # fingerprint. The fingerprint walks the snapshot, so snapshot and
        # fingerprint stay in lockstep by construction.
        registered_dir.mkdir(parents=True, exist_ok=True)
        helpers_dest_root = registered_dir / HELPER_SNAPSHOT_DIR
        if helpers_dest_root.exists():
            shutil.rmtree(helpers_dest_root)

        # Track every script destination the fresh registration covers, so
        # stale recognized scripts from an earlier registration can be
        # purged below ("snapshot = exactly the registered set").
        expected_scripts: set[Path] = set()
        script_dest = registered_dir / script_path.relative_to(method_dir)
        expected_scripts.add(script_dest.resolve())
        if not in_place:
            script_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(script_path, script_dest)
            yaml_file = method_dir / "method.yaml"
            if yaml_file.exists():
                shutil.copy2(yaml_file, registered_dir / "method.yaml")

        method_dir_resolved = method_dir.resolve()
        for helper in helper_paths:
            try:
                rel = helper.relative_to(method_dir_resolved)
                dest = registered_dir / rel
                copy_needed = not in_place
            except ValueError:
                # Out-of-dir helper: mirror its project-relative path under
                # the reserved subdir (collision-safe — project-relative
                # paths are unique).
                rel = helper.relative_to(project_dir.resolve())
                dest = helpers_dest_root / rel
                copy_needed = True
            expected_scripts.add(dest.resolve())
            if copy_needed:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(helper, dest)
        _purge_stale_snapshot_scripts(registered_dir, expected_scripts)
        print(f"  source snapshot: strict helpers mode — copied to {registered_dir}")
    elif not in_place:
        registered_dir.mkdir(parents=True, exist_ok=True)
        # collect_method_scripts is the SAME set build_code_fingerprint
        # hashes — snapshot and fingerprint must stay in lockstep so
        # everything that registers is covered by the cache key.
        expected_scripts = set()
        for src_file in collect_method_scripts(method_dir):
            rel = src_file.relative_to(method_dir)
            dest = registered_dir / rel
            expected_scripts.add(dest.resolve())
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
        # Also copy method.yaml if it exists (contract spec)
        yaml_file = method_dir / "method.yaml"
        if yaml_file.exists():
            shutil.copy2(yaml_file, registered_dir / "method.yaml")
        # Ghost purge: drop recognized scripts an earlier registration left
        # in the snapshot that are no longer in the collected set.
        _purge_stale_snapshot_scripts(registered_dir, expected_scripts)
        print(f"  source snapshot: copied to {registered_dir}")
    else:
        print("  source snapshot: already at registered location")
