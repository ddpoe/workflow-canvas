"""
Tests for DVC run output lifecycle.

Covers:
- Cache-authoritative resolve_input (CACHE / REMOTE-PULL / FAIL)
- Cache pruning (wfc cache prune)

See ``tests/test_resolve.py`` for the full three-state coverage.
"""

import os
import shutil
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from tests.fixtures.conftest import project_archive_dir
from tests.fixtures.routes import claimed_run
from tests.harness import Phase


def test_resolve_input_fail_path(cli, tmp_project):
    """resolve_input refuses an output it can read from nowhere, naming it."""
    run_id = _register_and_complete_with_hash(cli, tmp_project)

    from wfc.persistence import get_session, RunOutput
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == int(run_id))
        ).first()
        artifact_path = Path(ro.artifact_path)
        content_hash = ro.content_hash

    # Deferred archiving: content_hash may be NULL until archive pass

    # Delete the artifact
    if artifact_path.exists():
        if artifact_path.is_dir():
            shutil.rmtree(artifact_path)
        else:
            artifact_path.unlink()

    # Delete the DVC cache entry (entries are read-only — make deletable
    # first, exactly as prune_dvc_cache does)
    from wfc.storage.cache import _make_writable
    cache_path = tmp_project / ".dvc" / "cache" / "files" / "md5" / content_hash[:2] / content_hash[2:]
    if cache_path.exists():
        _make_writable(cache_path)
        if cache_path.is_dir():
            shutil.rmtree(cache_path)
        else:
            cache_path.unlink()

    from wfc.storage import InputUnavailableError, resolve_input

    with pytest.raises(InputUnavailableError) as refusal:
        resolve_input(int(run_id))
    assert content_hash in str(refusal.value)
    assert f"Re-run run {run_id}" in str(refusal.value)


def test_resolve_input_no_content_hash(cli, tmp_project):
    """A pre-archive row resolves to its run archive while the file is there.

    A row with no ``content_hash`` yet is a pre-archive row: the archive
    pass has not reached it, the normal in-pipeline state.
    ``resolve_input`` resolves it to the run archive,
    ``RunOutput.artifact_path``; once that file is gone the output is
    missing and the resolver refuses it loudly, naming the path and the
    re-run, rather than return the dead path.
    """
    # A run stopped after the collect phase: the RunOutput row is the one
    # collect recorded, and the record verb below completes it without
    # archiving, which is what leaves content_hash unset.
    with pytest.MonkeyPatch.context() as mp:
        run = claimed_run(tmp_project, monkeypatch=mp, through=Phase.COLLECT,
                          method="csv_merge", module="csv_tools", sample="S1",
                          outputs={"result": ".csv"})
    run_id = str(run.run_id)
    output_file = run.output_rows[0]["artifact_path"]

    cli("complete_run", "--run-id", run_id, "--status", "completed",
        "--output", output_file)

    # Force content_hash to None
    from wfc.persistence import get_session, RunOutput
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == int(run_id))
        ).first()
        ro.content_hash = None
        session.commit()

    from wfc.storage import InputUnavailableError, resolve_input
    assert resolve_input(int(run_id)) == output_file

    # Once the run-archive file is gone the output is in neither place: a
    # loud failure, never a path that does not exist.
    os.remove(output_file)
    with pytest.raises(InputUnavailableError) as refusal:
        resolve_input(int(run_id))
    assert Path(output_file).name in str(refusal.value)
    assert f"Re-run run {run_id}" in str(refusal.value)


# =============================================================================
# Cache pruning
# =============================================================================


def test_cache_prune_dry_run(cli, tmp_project):
    """wfc cache prune --dry-run prints what would be deleted but doesn't delete."""
    # Create some run archives
    runs_dir = tmp_project / ".runs"
    (runs_dir / "00000099").mkdir(parents=True, exist_ok=True)
    (runs_dir / "00000099" / "output.csv").write_text("data")

    r = cli("cache", "prune", "--dry-run", "--force")
    assert r.returncode == 0
    assert "dry run" in r.stdout.lower()
    # Archive should still exist
    assert (runs_dir / "00000099").exists()


def test_cache_prune_removes_unreferenced(cli, tmp_project):
    """wfc cache prune removes unreferenced archives, keeps referenced ones."""
    # Create a real run (will be referenced in DB)
    run_id = _register_and_complete_with_hash(cli, tmp_project)

    # Create an orphan archive (no DB reference)
    runs_dir = tmp_project / ".runs"
    orphan = runs_dir / "00099999"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "output.csv").write_text("orphan data")

    # Prune with --force
    r = cli("cache", "prune", "--force")
    assert r.returncode == 0

    # Orphan should be deleted
    assert not orphan.exists(), "Unreferenced archive should be deleted"
    # Referenced archive should remain
    ref_archive = runs_dir / f"{int(run_id):08d}"
    assert ref_archive.exists(), "Referenced archive should be preserved"


def test_cache_prune_include_local(cli, tmp_project):
    """wfc cache prune --all --include-local removes archives AND .dvc/cache/ entries; DB rows preserved."""
    run_id = _register_and_complete_with_hash(cli, tmp_project)

    # Verify the run has a content_hash and a DVC cache entry
    from wfc.persistence import get_session, RunOutput
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == int(run_id))
        ).first()
        content_hash = ro.content_hash

    # Deferred archiving: content_hash may be NULL until archive pass

    # Verify both archive and DVC cache exist before pruning
    runs_dir = tmp_project / ".runs"
    archive_dir = runs_dir / f"{int(run_id):08d}"
    cache_entry = tmp_project / ".dvc" / "cache" / "files" / "md5" / content_hash[:2] / content_hash[2:]
    assert archive_dir.exists(), "Archive should exist before prune"
    assert cache_entry.exists(), "DVC cache entry should exist before prune"

    # Prune with --all --include-local --force
    r = cli("cache", "prune", "--all", "--include-local", "--force")
    assert r.returncode == 0

    # Both archive dirs AND .dvc/cache/files/md5/ entries should be removed
    assert not archive_dir.exists(), "Archive should be deleted after --all --include-local prune"
    assert not cache_entry.exists(), "DVC cache entry should be deleted after --include-local prune"

    # DB rows should be preserved
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == int(run_id))
        ).first()
        assert ro is not None, "RunOutput DB row should be preserved after prune"
        assert ro.content_hash == content_hash, "content_hash should be unchanged"


def test_cache_prune_safety_check(cli, tmp_project):
    """wfc cache prune aborts when DVC remote is unreachable (no --force)."""
    run_id = _register_and_complete_with_hash(cli, tmp_project)

    # Create an orphan archive so there's something to prune
    runs_dir = tmp_project / ".runs"
    orphan = runs_dir / "00099999"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "output.csv").write_text("orphan data")

    # Make the archive unreachable the way a user does: it is a directory
    # outside the project (an external drive, a network share), and it is
    # gone. `wfc init` always writes a [dvc] section, so "no [dvc] section"
    # is not a state a real project reaches.
    _remove_archive(tmp_project)

    # Without --force, prune should abort with return code 1
    r = cli("cache", "prune")
    assert r.returncode == 1, f"Should abort when remote unreachable: {r.stdout} / {r.stderr}"
    assert "unreachable" in r.stderr.lower() or "unreachable" in r.stdout.lower(), \
        f"Should warn about unreachable remote: {r.stderr}"

    # Orphan should still exist (nothing was deleted)
    assert orphan.exists(), "Orphan archive should be preserved when safety check aborts"

    # With --force, prune should proceed
    r = cli("cache", "prune", "--force")
    assert r.returncode == 0
    assert not orphan.exists(), "Orphan archive should be deleted with --force"


def test_cache_prune_safety_check_include_local_elevated(cli, tmp_project):
    """wfc cache prune --include-local shows elevated warning when remote unreachable."""
    _register_and_complete_with_hash(cli, tmp_project)

    # Create orphan archive
    runs_dir = tmp_project / ".runs"
    orphan = runs_dir / "00099999"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "output.csv").write_text("orphan data")

    # The archive directory is gone => remote unreachable (see the
    # safety-check test above for why this is the producible shape).
    # --include-local without --force => elevated severity error
    _remove_archive(tmp_project)
    r = cli("cache", "prune", "--include-local")
    assert r.returncode == 1, "Should abort with --include-local when remote unreachable"
    assert "unrecoverable" in r.stderr.lower(), \
        f"Should warn about unrecoverable outputs: {r.stderr}"


# =============================================================================
# Helpers
# =============================================================================


def _remove_archive(project_dir):
    """Delete the project's DVC archive whole, the way a user loses one.

    DVC writes its objects read-only and ``shutil.rmtree`` on Windows refuses
    a read-only file, so the objects a registered sample pushed there are
    made writable first (as ``prune_dvc_cache`` does before deleting). The
    archive has to be gone for the remote to read as unreachable; an
    ``ignore_errors`` removal that left it in place would leave the safety
    check nothing to refuse.
    """
    from wfc.storage.cache import _make_writable

    archive = project_archive_dir(project_dir)
    if archive.exists():
        _make_writable(archive)
        shutil.rmtree(archive)


def _register_and_complete_with_hash(cli, project_dir, method="csv_merge",
                                      module="csv_tools", sample="S1"):
    """Complete a run through the record verb and archive its output. Returns run ID.

    The claim, materialize, dispatch and collect phases run through the
    route, stopped after collect, so the ``RunOutput`` row ``complete_run``
    updates by path is the one the collect phase recorded. The route's pins
    (cwd and the ``WFC_*`` environment) are scoped to the call: ``tmp_project``
    already pins the same root for the test. After complete_run, calls
    archive_outputs to populate content_hash (archiving is deferred;
    complete_run does not archive inline).
    """
    from wfc.storage import archive_outputs

    with pytest.MonkeyPatch.context() as mp:
        run = claimed_run(project_dir, monkeypatch=mp, through=Phase.COLLECT,
                          method=method, module=module, sample=sample,
                          outputs={"result": ".csv"})
    run_id = str(run.run_id)
    output_file = run.output_rows[0]["artifact_path"]

    r = cli("complete_run", "--run-id", run_id, "--status", "completed",
            "--output", output_file)
    assert r.returncode == 0, r.stderr

    # Deferred archiving: explicitly archive to populate content_hash
    archive_outputs(project_dir, run_id=int(run_id))

    return run_id
