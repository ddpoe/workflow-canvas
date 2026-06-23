"""Behavior test for the one-time legacy .pm/ -> .wfc/ backup-migration (ADR-021).

The migration — back up the legacy .pm/ dir, move it to .wfc/, rename
pm.db -> wfc.db, and no-op when already migrated — is the single new behavior
introduced by the rename. The rest of the suite proves the unchanged logic
still works.
"""

from dflow.core.decorators import workflow, Step


@workflow(purpose="A legacy .pm/ project is backed up then migrated to .wfc/; re-run is a no-op")
def test_legacy_pm_dir_backup_migration(tmp_path):
    """Staging a legacy .pm/ with pm.db triggers a backup + migrate; idempotent."""
    from wfc.database import migrate_legacy_state_dir

    _ = Step(step_num=1, name="Stage a legacy .pm/ project",
             purpose="Create .pm/ with wf-canvas.toml + pm.db, no .wfc/")
    legacy = tmp_path / ".pm"
    legacy.mkdir()
    (legacy / "wf-canvas.toml").write_text('[database]\nurl = "sqlite:///.wfc/wfc.db"\n')
    (legacy / "pm.db").write_bytes(b"SQLITE-DB-BYTES")
    (legacy / "envs.json").write_text("{}")

    _ = Step(step_num=2, name="Trigger migration",
             purpose="migrate_legacy_state_dir backs up then renames")
    migrated = migrate_legacy_state_dir(tmp_path)
    assert migrated is True

    # .wfc/ exists with the renamed db; legacy .pm/ is gone.
    assert (tmp_path / ".wfc").is_dir()
    assert (tmp_path / ".wfc" / "wfc.db").read_bytes() == b"SQLITE-DB-BYTES"
    assert (tmp_path / ".wfc" / "wf-canvas.toml").exists()
    assert not (tmp_path / ".pm").exists()

    # A timestamped backup of the original .pm/ exists and still holds pm.db.
    backups = list(tmp_path.glob(".pm.bak-*"))
    assert len(backups) == 1, f"expected one backup, got {backups}"
    assert (backups[0] / "pm.db").read_bytes() == b"SQLITE-DB-BYTES"

    _ = Step(step_num=3, name="Re-run is a no-op",
             purpose="A project already on .wfc/ is left untouched (idempotent)")
    again = migrate_legacy_state_dir(tmp_path)
    assert again is False
    # No second backup created.
    assert len(list(tmp_path.glob(".pm.bak-*"))) == 1


def test_migration_noop_when_no_legacy_dir(tmp_path):
    """No .pm/ and no .wfc/ -> migration is a no-op returning False."""
    from wfc.database import migrate_legacy_state_dir
    assert migrate_legacy_state_dir(tmp_path) is False
    assert not (tmp_path / ".wfc").exists()
