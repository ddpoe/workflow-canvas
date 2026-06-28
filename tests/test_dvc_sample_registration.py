"""
Tests for ADR-009: DVC-backed sample registration.

Covers:
- US-1: Registration with DVC stores content_hash and caches file
- US-2: restore_sample materializes file from DVC cache
- US-3: Push/pull round-trip for samples
- US-4: Registration without DVC config errors cleanly
- US-5: restore_sample errors on NULL content_hash (legacy sample)
- Snakemake rule generation for root steps with restore_sample rules
"""

import hashlib
import os
import stat
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import select


def _rmtree_force(path: Path) -> None:
    """``shutil.rmtree`` that chmod+retry on read-only files (Windows DVC cache).

    DVC's ``cache_file(move=True)`` makes cache blobs read-only after the
    rename, so a plain ``shutil.rmtree`` raises ``PermissionError`` on
    Windows.  This helper resets the write bit and retries.
    """
    import shutil
    def _onexc(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    shutil.rmtree(path, onexc=_onexc)

from axiom_annotations import workflow, Step

from wfc.database import get_session
from wfc.models import Sample
from wfc.cli import register_sample, restore_sample


# =============================================================================
# Helpers
# =============================================================================

def _setup_dvc(project_root: Path) -> Path:
    """Write wf-canvas.toml with [dvc] and initialize DVC cache structure."""
    remote_dir = project_root / "dvc_remote"
    remote_dir.mkdir(parents=True, exist_ok=True)
    config_path = project_root / ".wfc" / "wf-canvas.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (project_root / ".wfc" / "wfc.db").as_posix()
    config_path.write_text(
        f'[database]\nurl = "sqlite:///{db_path}"\n\n'
        f'[project]\nname = "test"\n\n'
        f'[dvc]\nremote_type = "local"\n'
        f'remote_path = "{remote_dir.as_posix()}"\nauto_init = true\n'
    )
    from wfc.provenance import init_dvc
    init_dvc(project_root, {"url": str(remote_dir)})
    return remote_dir


def _write_config_no_dvc(project_root: Path) -> None:
    """Write wf-canvas.toml WITHOUT [dvc] section."""
    config_path = project_root / ".wfc" / "wf-canvas.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (project_root / ".wfc" / "wfc.db").as_posix()
    config_path.write_text(
        f'[database]\nurl = "sqlite:///{db_path}"\n\n'
        f'[project]\nname = "test"\n'
    )


# =============================================================================
# US-1: Registration with DVC stores content_hash and caches file
# =============================================================================

@workflow(purpose="register_sample with DVC stores content_hash and file in cache")
def test_register_sample_stores_hash_and_caches(tmp_project):
    """After registration, content_hash is set in DB and file exists in DVC cache."""
    _ = Step(step_num=1, name="Setup DVC", purpose="Configure DVC for sample registration")
    _setup_dvc(tmp_project)

    _ = Step(step_num=2, name="Create source file", purpose="Write a known CSV file")
    src = tmp_project / "input.csv"
    src.write_text("a,b\n1,2\n")

    _ = Step(step_num=3, name="Register sample", purpose="Call register_sample with DVC configured")
    register_sample(name="test_sample", source_path=src, project_root=tmp_project)

    _ = Step(step_num=4, name="Verify content_hash in DB", purpose="Check the Sample row has content_hash set")
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "test_sample")).first()
    assert row is not None
    assert row.content_hash is not None
    assert len(row.content_hash) == 32  # MD5 hex digest

    _ = Step(step_num=5, name="Verify file in DVC cache", purpose="Check .dvc/cache/ contains the cached file")
    h = row.content_hash
    cache_path = tmp_project / ".dvc" / "cache" / "files" / "md5" / h[:2] / h[2:]
    assert cache_path.exists()


# =============================================================================
# US-4: Registration without DVC config errors cleanly
# =============================================================================

@workflow(purpose="register_sample without DVC config raises DvcNotConfiguredError")
def test_register_sample_no_dvc_errors(tmp_project):
    """Registration must fail with clear error when [dvc] section is missing."""
    _ = Step(step_num=1, name="Write config without DVC", purpose="Config has no [dvc] section")
    _write_config_no_dvc(tmp_project)

    _ = Step(step_num=2, name="Create source file", purpose="Write a source file to register")
    src = tmp_project / "input.csv"
    src.write_text("x\n1\n")

    _ = Step(step_num=3, name="Attempt registration", purpose="Should raise DvcNotConfiguredError")
    from wfc.provenance import DvcNotConfiguredError
    with pytest.raises(DvcNotConfiguredError, match="No \\[dvc\\] section"):
        register_sample(name="fail_sample", source_path=src, project_root=tmp_project)

    _ = Step(step_num=4, name="Verify no DB entry", purpose="No sample row should exist")
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "fail_sample")).first()
    assert row is None

    _ = Step(step_num=5, name="Verify no file copy", purpose="data/samples/ should not have the file")
    assert not (tmp_project / "data" / "samples" / "fail_sample").exists()


# =============================================================================
# US-2 + US-5: restore_sample happy path and NULL hash error
# =============================================================================

@workflow(purpose="restore_sample materializes file from DVC cache")
def test_restore_sample_happy_path(tmp_project):
    """restore_sample retrieves cached file when content_hash is present."""
    _ = Step(step_num=1, name="Setup DVC and register", purpose="Register a sample with DVC")
    _setup_dvc(tmp_project)
    src = tmp_project / "data.csv"
    src.write_text("col\n42\n")
    register_sample(name="restorable", source_path=src, project_root=tmp_project)

    _ = Step(step_num=2, name="Ensure clean workspace", purpose="ADR-018: register_sample no longer copies into data/samples/; registered_path is restore_sample's target")
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "restorable")).first()
    registered = Path(row.registered_path)
    # Post-ADR-018 register_sample writes only to the DVC cache (no copy
    # into data/samples/).  Use missing_ok=True so this is a no-op when the
    # workspace path was never populated.
    registered.unlink(missing_ok=True)
    assert not registered.exists()

    _ = Step(step_num=3, name="Restore sample", purpose="Call restore_sample to materialize from cache")
    restore_sample(name="restorable", project_root=tmp_project)

    _ = Step(step_num=4, name="Verify file restored", purpose="File should be back at registered_path")
    assert registered.exists()
    assert registered.read_text() == "col\n42\n"


@workflow(purpose="restore_sample errors on legacy sample with NULL content_hash")
def test_restore_sample_null_hash_errors(tmp_project):
    """restore_sample must error with guidance when content_hash is NULL."""
    _ = Step(step_num=1, name="Setup DVC config", purpose="Need config for DB access")
    _setup_dvc(tmp_project)

    _ = Step(step_num=2, name="Insert legacy sample row", purpose="Create a Sample with no content_hash")
    with get_session() as session:
        legacy = Sample(
            name="legacy_sample",
            source_path="/old/path.csv",
            registered_path=str(tmp_project / "data" / "samples" / "legacy_sample" / "path.csv"),
            file_type="csv",
            registration_mode="copy",
            content_hash=None,
        )
        session.add(legacy)
        session.commit()

    _ = Step(step_num=3, name="Attempt restore", purpose="Should exit with error about re-registration")
    with pytest.raises(SystemExit):
        restore_sample(name="legacy_sample", project_root=tmp_project)


# =============================================================================
# Error propagation: pull_cache errors are not swallowed
# =============================================================================

@workflow(purpose="restore_sample propagates pull_cache errors")
def test_restore_sample_propagates_pull_errors(tmp_project):
    """If pull_cache raises an unexpected error, it should propagate, not be swallowed."""
    _ = Step(step_num=1, name="Setup DVC and register", purpose="Register a sample with DVC")
    _setup_dvc(tmp_project)
    src = tmp_project / "err.csv"
    src.write_text("x\n1\n")
    register_sample(name="pull_err", source_path=src, project_root=tmp_project)

    _ = Step(step_num=2, name="Get hash and delete local cache", purpose="Force a cache miss")
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "pull_err")).first()
    registered = Path(row.registered_path)
    # ADR-018: register_sample does not populate data/samples/ -- the
    # workspace file may not exist.  missing_ok keeps the step safe.
    registered.unlink(missing_ok=True)
    _rmtree_force(tmp_project / ".dvc" / "cache")

    _ = Step(step_num=3, name="Mock pull_cache to raise", purpose="Simulate network/auth error")
    with patch("wfc.provenance.pull_cache", side_effect=OSError("simulated network error")):
        with pytest.raises(OSError, match="simulated network error"):
            restore_sample(name="pull_err", project_root=tmp_project)


# =============================================================================
# US-2: Snakemake rule generation for root steps with restore_sample
# =============================================================================

@workflow(purpose="generate_snakefile emits restore_sample rules for root steps")
def test_snakefile_restore_sample_rules(tmp_project):
    """Root steps should get restore_sample rules in generated Snakefile."""
    _ = Step(step_num=1, name="Setup DVC and register samples", purpose="Register samples with hashes")
    _setup_dvc(tmp_project)
    src = tmp_project / "sample_data.csv"
    src.write_text("x\n1\n")
    register_sample(name="S1", source_path=src, project_root=tmp_project)

    _ = Step(step_num=2, name="Build pipeline def", purpose="Create a pipeline with a root step")
    from wfc.snakemake_gen import StepDef, PipelineDef, generate_snakefile
    step = StepDef(
        method_name="preprocess",
        module_name="data_prep",
        script_path="methods/preprocess/preprocess.py",
        params={"threshold": 0.5},
        depends_on=[],
        output_ext=".parquet",
    )
    pipeline = PipelineDef(steps=[step], samples=["S1"])

    _ = Step(step_num=3, name="Generate Snakefile", purpose="Call generate_snakefile")
    snakefile = generate_snakefile(
        pipeline,
        wfc_module_path=str(tmp_project),
        project_root=str(tmp_project),
    )

    _ = Step(step_num=4, name="Verify restore_sample rule", purpose="Snakefile should contain restore_sample rule")
    assert "rule restore_sample:" in snakefile
    assert "SAMPLE_HASHES" in snakefile
    assert "wfc restore-sample" in snakefile

    _ = Step(step_num=5, name="Verify root step depends on sentinel", purpose="Root step input should reference sentinel")
    assert ".sample_ready" in snakefile


# =============================================================================
# US-3: Push/pull round-trip for samples
# =============================================================================

@workflow(purpose="push_cache and pull_cache work with sample content hashes")
def test_push_pull_sample_round_trip(tmp_project):
    """Samples cached via register_sample can be pushed and pulled."""
    _ = Step(step_num=1, name="Setup DVC and register", purpose="Register a sample")
    remote_dir = _setup_dvc(tmp_project)
    src = tmp_project / "round_trip.csv"
    src.write_text("data\nvalue\n")
    register_sample(name="rt_sample", source_path=src, project_root=tmp_project)

    _ = Step(step_num=2, name="Get content hash", purpose="Look up the hash from DB")
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "rt_sample")).first()
    h = row.content_hash

    _ = Step(step_num=3, name="Push to remote", purpose="Push sample to DVC remote")
    from wfc.provenance import push_cache, pull_cache, restore_from_cache
    assert push_cache([h], tmp_project) is True

    _ = Step(step_num=4, name="Verify remote has file", purpose="Check remote cache directory")
    remote_file = remote_dir / "files" / "md5" / h[:2] / h[2:]
    assert remote_file.exists()

    _ = Step(step_num=5, name="Delete local cache and pull", purpose="Clear local cache, pull from remote")
    _rmtree_force(tmp_project / ".dvc" / "cache")
    assert pull_cache([h], tmp_project) is True

    _ = Step(step_num=6, name="Verify restore after pull", purpose="Can restore from pulled cache")
    dest = tmp_project / "restored.csv"
    assert restore_from_cache(h, dest, tmp_project) is True
    assert dest.read_text() == "data\nvalue\n"
