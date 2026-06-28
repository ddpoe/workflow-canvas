"""
Tests for DVC run output lifecycle (ADR-018).

Covers:
- Cache-authoritative resolve_input (CACHE / REMOTE-PULL / FAIL)
- Cache pruning (wfc cache prune)

ADR-018: ``.runs/workspace/`` is gone; the cache IS the workspace.
The old `_publish_to_workspace` helper, HOT/WARM/COLD tier model, and
`restore_output` command are deleted.  See ``tests/test_resolve.py``
(Task 4) for the new three-state coverage.
"""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step


def test_resolve_input_fail_path(cli, tmp_project):
    """resolve_input returns None when all restore attempts fail."""
    _seed_module_method(cli)
    run_id = _register_and_complete_with_hash(cli, tmp_project)

    from wfc.database import get_session
    from wfc.models import RunOutput
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

    # Delete the DVC cache entry
    cache_path = tmp_project / ".dvc" / "cache" / "files" / "md5" / content_hash[:2] / content_hash[2:]
    if cache_path.exists():
        if cache_path.is_dir():
            shutil.rmtree(cache_path)
        else:
            cache_path.unlink()

    from wfc.cli import resolve_input

    result = resolve_input(int(run_id))
    assert result is None, "resolve_input should return None when all restore fails"


def test_resolve_input_no_content_hash(cli, tmp_project):
    """resolve_input returns artifact_path as-is when content_hash is None (backward compat)."""
    _seed_module_method(cli)

    # Create a run without content hashing
    args = ["register_run", "--method", "csv_merge", "--module", "csv_tools",
            "--sample", "S1", "--params", "{}"]
    r = cli(*args)
    assert r.returncode == 0, r.stderr
    run_id = r.stdout.strip()

    archive = os.path.join(".runs", f"{int(run_id):08d}")
    os.makedirs(archive, exist_ok=True)
    output_file = os.path.join(archive, "output.csv")
    with open(output_file, "w") as f:
        f.write("col\n1\n")

    # Complete without content hashing (mock hash_path to fail)
    with patch("wfc.cli.complete_run.__wrapped__", side_effect=None):
        # Just use CLI directly; content hashing may fail gracefully
        cli("complete_run", "--run-id", run_id, "--status", "completed",
            "--output", output_file)

    # Force content_hash to None
    from wfc.database import get_session
    from wfc.models import RunOutput
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == int(run_id))
        ).first()
        ro.content_hash = None
        session.commit()

    # Delete the artifact file
    os.remove(output_file)

    from wfc.cli import resolve_input
    result = resolve_input(int(run_id))
    # Should return artifact_path as-is, even though it doesn't exist
    assert result == output_file


# =============================================================================
# US-3: Cache pruning
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
    _seed_module_method(cli)

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
    _seed_module_method(cli)

    run_id = _register_and_complete_with_hash(cli, tmp_project)

    # Verify the run has a content_hash and a DVC cache entry
    from wfc.database import get_session
    from wfc.models import RunOutput
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
    _seed_module_method(cli)
    run_id = _register_and_complete_with_hash(cli, tmp_project)

    # Create an orphan archive so there's something to prune
    runs_dir = tmp_project / ".runs"
    orphan = runs_dir / "00099999"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "output.csv").write_text("orphan data")

    # No [dvc] section in wf-canvas.toml => remote unreachable
    # Default marker has no [dvc] section, so remote is "not configured"

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
    _seed_module_method(cli)
    _register_and_complete_with_hash(cli, tmp_project)

    # Create orphan archive
    runs_dir = tmp_project / ".runs"
    orphan = runs_dir / "00099999"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "output.csv").write_text("orphan data")

    # No [dvc] section => remote unreachable
    # --include-local without --force => elevated severity error
    r = cli("cache", "prune", "--include-local")
    assert r.returncode == 1, "Should abort with --include-local when remote unreachable"
    assert "unrecoverable" in r.stderr.lower(), \
        f"Should warn about unrecoverable outputs: {r.stderr}"


# =============================================================================
# Helpers
# =============================================================================


def _seed_module_method(cli, module="csv_tools", method="csv_merge"):
    """Register a module and method via CLI so register_run can reference them."""
    result = cli("register-module", "--name", module, "--description", "test module", "--contracts", "[]")
    assert result.returncode == 0, result.stderr
    method_dir = os.path.join("methods", method)
    os.makedirs(method_dir, exist_ok=True)
    script_name = f"{method}.py"
    script_path = os.path.join(method_dir, script_name)
    if not os.path.exists(script_path):
        with open(script_path, "w") as f:
            f.write("def main(df, params): return df\n")
    # ADR-019 Cycle H: execution is container-only, so every registered method
    # must name a built container env. The tmp_project fixture writes a
    # placeholder ``fixture-env`` record so this registration validates
    # Docker-free (no image pull at registration time).
    yaml_path = os.path.join(method_dir, "method.yaml")
    if not os.path.exists(yaml_path):
        with open(yaml_path, "w") as f:
            f.write(
                "inputs:\n"
                "  data:\n"
                "    type: .csv\n"
                "    required: true\n"
                "outputs:\n"
                "  result:\n"
                "    type: .csv\n"
                "    required: true\n"
                "params: {}\n"
                "executor: python\n"
                "env: container:fixture-env\n"
            )
    result = cli("register-method", method_dir, "--module", module)
    assert result.returncode == 0, result.stderr


def _register_and_complete_with_hash(cli, project_dir, method="csv_merge",
                                      module="csv_tools", sample="S1"):
    """Register a run, set up DVC cache, complete with content hashing. Returns run ID.

    After complete_run, calls archive_outputs to populate content_hash
    (archiving is deferred and no longer happens inline during complete_run).
    """
    from wfc.provenance import archive_outputs

    # Ensure DVC cache exists
    cache_dir = project_dir / ".dvc" / "cache" / "files" / "md5"
    cache_dir.mkdir(parents=True, exist_ok=True)

    args = ["register_run", "--method", method, "--module", module,
            "--sample", sample, "--params", "{}"]
    r = cli(*args)
    assert r.returncode == 0, r.stderr
    run_id = r.stdout.strip()

    archive = os.path.join(".runs", f"{int(run_id):08d}")
    os.makedirs(archive, exist_ok=True)
    output_file = os.path.join(archive, "output.csv")
    with open(output_file, "w") as f:
        f.write("col\n1\n")

    cli("complete_run", "--run-id", run_id, "--status", "completed",
        "--output", output_file)

    # Deferred archiving: explicitly archive to populate content_hash
    archive_outputs(project_dir, run_id=int(run_id))

    return run_id
