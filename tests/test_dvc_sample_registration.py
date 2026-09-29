"""
Tests for DVC-backed sample registration.

Covers:
- Registration with DVC stores content_hash and caches file
- restore_sample materializes file from DVC cache
- Push/pull round-trip for samples
- Registration without DVC config errors cleanly
- Snakemake rule generation for root steps with restore_sample rules

restore_sample's refusal of a NULL content_hash is asserted in
tests/test_legacy_samples.py, beside the pipeline-start refusal it pairs
with.
"""

import hashlib
import os
import stat
import textwrap
from pathlib import Path

import pytest
from sqlmodel import select

from tests.fixtures.fakes import stub_transport


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

from wfc.persistence import get_session, Sample
from wfc.registration import register_sample
from wfc.storage import restore_sample


# =============================================================================
# Helpers
# =============================================================================

def _setup_dvc(project_root: Path) -> Path:
    """Write wf-canvas.toml with the canonical [dvc] ``url =`` shape and
    initialize the DVC cache/remote — the config shape ``wfc init`` ships."""
    remote_dir = project_root.parent / f"{project_root.name}-dvc-remote"
    remote_dir.mkdir(parents=True, exist_ok=True)
    config_path = project_root / ".wfc" / "wf-canvas.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (project_root / ".wfc" / "wfc.db").as_posix()
    config_path.write_text(
        f'[database]\nurl = "sqlite:///{db_path}"\n\n'
        f'[project]\nname = "test"\n\n'
        f'[dvc]\nurl = "{remote_dir.as_posix()}"\nauto_init = true\n'
    )
    from wfc.storage import init_dvc
    init_dvc(project_root, {"url": str(remote_dir)})
    return remote_dir


def _setup_dvc_legacy(project_root: Path) -> Path:
    """FROZEN legacy shape: the pre-``url =`` [dvc] section that names the
    remote via ``remote_type`` / ``remote_path``. Kept for the one test that
    must exercise the ``remote_type``/``remote_path`` config fallback still
    read by provenance's remote resolver; new config is written with ``url =``
    (see ``_setup_dvc``)."""
    remote_dir = project_root.parent / f"{project_root.name}-dvc-remote"
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
    from wfc.storage import init_dvc
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
# Registration with DVC stores content_hash and caches file
# =============================================================================

@workflow(purpose="register_sample with DVC stores content_hash and file in cache")
def test_register_sample_stores_hash_and_caches(tmp_project):
    """After registration, content_hash is set in DB and file exists in DVC cache.

    Deliberately drives the FROZEN legacy [dvc] remote_type/remote_path config
    shape so the remote-resolver fallback that still reads it stays covered;
    every other case here uses the canonical ``url =`` shape.
    """
    _ = Step(step_num=1, name="Setup DVC", purpose="Configure DVC (legacy config shape)")
    _setup_dvc_legacy(tmp_project)

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
# A second registration of a name is refused
# =============================================================================

@workflow(purpose="A second registration of an already-registered sample name "
                  "is refused with a ValueError naming the existing row, before "
                  "anything is cached or pushed: the cache holds no object for "
                  "the refused source, the remote received none, and the "
                  "existing row is unchanged")
def test_register_sample_duplicate_name_refused(tmp_project, monkeypatch):
    """``registration:sample-duplicate`` and P3-11 (the refusal precedes the cache write)."""
    from wfc import layout
    from wfc.identity import hash_path

    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
    _ = Step(step_num=1, name="Register a sample", purpose="The first row")
    remote_dir = _setup_dvc(tmp_project)
    first = tmp_project / "first.csv"
    first.write_text("a\n1\n")
    register_sample(name="dup_sample", source_path=first, project_root=tmp_project)
    with get_session() as session:
        before = session.exec(select(Sample).where(Sample.name == "dup_sample")).one()
        before_fields = before.model_dump()

    _ = Step(step_num=2, name="Register another source under the same name",
             purpose="Refused with the existing row's id")
    second = tmp_project / "second.csv"
    second.write_text("b\n2\n")
    with pytest.raises(ValueError, match=rf"already registered \(id={before_fields['id']}\)"):
        register_sample(name="dup_sample", source_path=second, project_root=tmp_project)

    _ = Step(step_num=3, name="The first row is unchanged, and nothing was stored",
             purpose="One row, as registered; no local object and no remote object "
                     "for the refused source")
    with get_session() as session:
        rows = session.exec(select(Sample).where(Sample.name == "dup_sample")).all()
        after_fields = [r.model_dump() for r in rows]
    assert after_fields == [before_fields]
    refused = hash_path(second)
    assert not layout.dvc_cache_entry(tmp_project, refused).exists()
    assert not (remote_dir / "files" / "md5" / refused[:2] / refused[2:]).exists()
    # The first registration did reach the remote, so the check above can fail.
    first_hash = before_fields["content_hash"]
    assert (remote_dir / "files" / "md5" / first_hash[:2] / first_hash[2:]).exists()


# =============================================================================
# Registration without DVC config errors cleanly
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
    from wfc.storage import DvcNotConfiguredError
    with pytest.raises(DvcNotConfiguredError, match="No \\[dvc\\] section"):
        register_sample(name="fail_sample", source_path=src, project_root=tmp_project)

    _ = Step(step_num=4, name="Verify no DB entry", purpose="No sample row should exist")
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "fail_sample")).first()
    assert row is None

    _ = Step(step_num=5, name="Verify no file copy", purpose="data/samples/ should not have the file")
    assert not (tmp_project / "data" / "samples" / "fail_sample").exists()


# =============================================================================
# restore_sample happy path and NULL hash error
# =============================================================================

@workflow(purpose="restore_sample materializes file from DVC cache")
def test_restore_sample_happy_path(tmp_project):
    """restore_sample retrieves cached file when content_hash is present."""
    _ = Step(step_num=1, name="Setup DVC and register", purpose="Register a sample with DVC")
    _setup_dvc(tmp_project)
    src = tmp_project / "data.csv"
    src.write_text("col\n42\n")
    register_sample(name="restorable", source_path=src, project_root=tmp_project)

    _ = Step(step_num=2, name="Ensure clean workspace", purpose="register_sample does not copy into data/samples/; registered_path is restore_sample's target")
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "restorable")).first()
    registered = Path(row.registered_path)
    # register_sample writes only to the DVC cache (no copy
    # into data/samples/).  Use missing_ok=True so this is a no-op when the
    # workspace path was never populated.
    registered.unlink(missing_ok=True)
    assert not registered.exists()

    _ = Step(step_num=3, name="Restore sample", purpose="Call restore_sample to materialize from cache")
    restore_sample(name="restorable", project_root=tmp_project)

    _ = Step(step_num=4, name="Verify file restored", purpose="File should be back at registered_path")
    assert registered.exists()
    assert registered.read_text() == "col\n42\n"


# A sample row with no content_hash is a malformed record and is refused,
# not degraded around. The refusal is asserted in
# tests/test_legacy_samples.py, which drives the pipeline-start refusal and
# this module's restore-side one in a single witness.


# =============================================================================
# Error propagation: pull_cache errors are not swallowed
# =============================================================================

@workflow(purpose="restore_sample propagates pull_cache errors")
def test_restore_sample_propagates_pull_errors(tmp_project, monkeypatch):
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
    # register_sample does not populate data/samples/ -- the
    # workspace file may not exist.  missing_ok keeps the step safe.
    registered.unlink(missing_ok=True)
    _rmtree_force(tmp_project / ".dvc" / "cache")

    _ = Step(step_num=3, name="Mock pull_cache to raise", purpose="Simulate network/auth error")
    def failing_pull(*_args, **_kwargs):
        raise OSError("simulated network error")

    stub_transport(monkeypatch, pull_cache=failing_pull)
    with pytest.raises(OSError, match="simulated network error"):
        restore_sample(name="pull_err", project_root=tmp_project)


# =============================================================================
# Snakemake rule generation for root steps with restore_sample
# =============================================================================

@workflow(purpose="A registered sample's content hash reaches the emitted "
                  "restore_sample rule through the composer's hash read")
def test_snakefile_restore_sample_rules(tmp_project):
    """A step a selector feeds gets restore_sample rules in the generated Snakefile."""
    _ = Step(step_num=1, name="Setup DVC and register samples", purpose="Register samples with hashes")
    _setup_dvc(tmp_project)
    src = tmp_project / "sample_data.csv"
    src.write_text("x\n1\n")
    register_sample(name="S1", source_path=src, project_root=tmp_project)

    _ = Step(step_num=2, name="Read the hashes and build the pipeline def",
             purpose="The composer's read, over its session, gives the emitter "
                     "the registered sample's hash; the pipeline has a step a selector feeds")
    from wfc.graph import StepDef, PipelineDef
    from wfc.persistence import get_session
    from wfc.orchestration import generate_snakefile
    from wfc.registration import load_sample_hashes
    with get_session() as session:
        hashes = load_sample_hashes(["S1"], session)
        registered = session.exec(select(Sample).where(Sample.name == "S1")).one()
    assert list(hashes) == ["S1"]
    assert hashes["S1"] == registered.content_hash
    step = StepDef(
        method_name="preprocess",
        module_name="data_prep",
        script_path="methods/preprocess/preprocess.py",
        params={"threshold": 0.5},
        depends_on=[],
        output_ext=".parquet",
        selector_slot="data",
    )
    pipeline = PipelineDef(steps=[step], samples=["S1"])

    _ = Step(step_num=3, name="Generate Snakefile", purpose="Hand the emitter the hash table")
    snakefile = generate_snakefile(pipeline, project_root=str(tmp_project),
                                   sample_hashes=hashes)

    _ = Step(step_num=4, name="Verify restore_sample rule", purpose="Snakefile should contain restore_sample rule")
    assert "rule restore_sample:" in snakefile
    assert f"SAMPLE_HASHES = {hashes!r}" in snakefile
    assert "wfc restore-sample" in snakefile

    _ = Step(step_num=5, name="Verify root step depends on sentinel", purpose="Root step input should reference sentinel")
    assert ".sample_ready" in snakefile


@workflow(
    purpose="The emitter's restore rule carries exactly the hash table it is "
            "handed, with no database: two of three samples registered with a "
            "hash give a two-entry SAMPLE_HASHES, the restore rule and the "
            "sample reader's readiness input; an empty table emits no restore rule "
            "(Tier 2)",
)
def test_restore_rule_is_emitted_from_the_handed_hash_table(tmp_path):
    from wfc.graph import StepDef, PipelineDef
    from wfc.orchestration import generate_snakefile

    step = StepDef(
        method_name="preprocess",
        module_name="data_prep",
        script_path="methods/preprocess/preprocess.py",
        params={},
        depends_on=[],
        output_ext=".parquet",
        selector_slot="data",
    )
    pipeline = PipelineDef(steps=[step], samples=["s1", "s2", "s3"])
    hashes = {"s1": "a" * 32, "s2": "b" * 32}

    snakefile = generate_snakefile(pipeline, str(tmp_path), sample_hashes=hashes)

    # The exact table line: the two handed hashes, and no entry for the
    # hashless third sample.
    assert f"SAMPLE_HASHES = {hashes!r}" in snakefile
    assert "rule restore_sample:" in snakefile
    assert ('"{sys.executable} -m wfc restore-sample '
            '--name {wildcards.sample} {params.hash_arg}"') in snakefile
    assert ".sample_ready" in snakefile

    bare = generate_snakefile(pipeline, str(tmp_path), sample_hashes={})
    assert "rule restore_sample:" not in bare
    assert "SAMPLE_HASHES" not in bare


# =============================================================================
# Push/pull round-trip for samples
# =============================================================================

@workflow(purpose="A sample's content hash round-trips through the DVC remote: pushed, pulled back, restored")
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
    from wfc.storage import pull_cache, restore_from_cache
    from wfc.storage import push as remote_push
    assert not remote_push([h], tmp_project).failed

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
