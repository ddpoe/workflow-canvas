"""
Unit & integration tests: content-addressed versioning, input fingerprinting,
and run caching (Gap 15).

Coverage:
  - DirtyRepositoryError raised when working tree is dirty
  - build_code_fingerprint determinism and edge cases
  - get_or_create_version deduplicates (method_id, code_fingerprint) pairs
  - build_input_fingerprint is stable regardless of DB insertion order
  - build_cache_key is deterministic; parameter changes break it
  - register_sample captures file_size and file_mtime from the source file
  - registration_mode="link" raises NotImplementedError
  - pre_run on a cache miss inserts a Run with version_id and cache_key set
  - pre_run on a cache hit returns CACHED and audits the hit in the DB
  - Code fingerprint stable across unrelated file changes
  - Version lookup by fingerprint: same fingerprint + different commits = same version
  - Source files copied to registered location after register_method
  - Fingerprint computed from registered copy, not working tree

These are Tier 2 tests: @workflow(purpose=...) with Step() markers.
No Snakemake invocation; no subprocess except where explicitly mocked.
"""

import filecmp
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from sqlmodel import select

from dflow.core.decorators import workflow, Step

from wfc.database import get_session
from wfc.init import init_project
from wfc.models import MethodVersion, Run, Sample
from wfc.register import register_module, register_method
from wfc.version import (
    DirtyRepositoryError,
    build_cache_key,
    build_code_fingerprint,
    build_input_fingerprint,
    get_or_create_version)
from wfc.cli import pre_run, register_sample


# =============================================================================
# Helpers
# =============================================================================

def _seed_method(module_name: str = "versioning_mod", method_name: str = "v_method") -> int:
    """Insert a minimal Module + Method into the DB and return method.id."""
    from wfc.models import Module, Method
    with get_session() as session:
        mod = Module(name=module_name, description="versioning test module")
        session.add(mod)
        session.commit()
        session.refresh(mod)
        method = Method(name=method_name, module_id=mod.id, script_path="methods/v_method/run.py")
        session.add(method)
        session.commit()
        session.refresh(method)
        return method.id  # type: ignore[return-value]


# =============================================================================
# get_git_commit: dirty-tree guard
# =============================================================================

@workflow(
    purpose="get_git_commit raises DirtyRepositoryError when the working tree has uncommitted changes"
)
def test_dirty_tree_raises(tmp_path):
    """subprocess mock returns a non-empty porcelain status → DirtyRepositoryError."""
    口 = Step(
        step_num=1,
        name="Patch subprocess.run",
        purpose="Simulate git status --porcelain reporting a dirty working tree")
    dirty_status = MagicMock(returncode=0, stdout="M some_file.py\n", stderr="")

    with patch("wfc.version.subprocess.run", return_value=dirty_status) as mock_run:
        口 = Step(
            step_num=2,
            name="Call get_git_commit",
            purpose="Verify that a dirty status causes an immediate DirtyRepositoryError")
        from wfc.version import get_git_commit
        with pytest.raises(DirtyRepositoryError, match="uncommitted changes"):
            get_git_commit(tmp_path)

        口 = Step(
            step_num=3,
            name="Verify git rev-parse was NOT called",
            purpose="Confirm the function aborts before querying HEAD when the tree is dirty")
        # Only the status call should have been made — rev-parse must not run
        assert mock_run.call_count == 1
        assert mock_run.call_args == call(
            ["git", "status", "--porcelain"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True)


@workflow(
    purpose="get_git_commit succeeds when only untracked files are present — "
            "run outputs and other untracked paths must not trigger DirtyRepositoryError"
)
def test_untracked_files_do_not_block_run(tmp_path):
    """?? lines in git status (untracked files) must be silently ignored.

    Run outputs land in .runs/workspace/ which is gitignored and therefore
    always shows up as ?? in git status --porcelain.  They must never prevent
    a pipeline from executing.
    """
    口 = Step(
        step_num=1,
        name="Patch subprocess.run",
        purpose="Simulate git status reporting only untracked files (?? prefix)")
    fake_commit = "a" * 40
    untracked_status = MagicMock(
        returncode=0,
        stdout=(
            "?? .runs/workspace/R2_fqc_cycD1/Rep2_siRNA/default/output.csv\n"
            "?? .runs/workspace/R2_fqc_scr50/Rep2_siRNA/default/output.csv\n"
            "?? .wfc/workflow.db\n"
        ),
        stderr="")
    rev_parse_result = MagicMock(returncode=0, stdout=fake_commit + "\n", stderr="")

    with patch(
        "wfc.version.subprocess.run",
        side_effect=[untracked_status, rev_parse_result]) as mock_run:
        口 = Step(
            step_num=2,
            name="Call get_git_commit",
            purpose="Verify the function returns a commit SHA without raising")
        from wfc.version import get_git_commit
        result = get_git_commit(tmp_path)

        口 = Step(
            step_num=3,
            name="Verify both git commands were called and no error was raised",
            purpose="Confirm the status check passed through and rev-parse was reached")
        assert result == fake_commit
        assert mock_run.call_count == 2


@workflow(
    purpose="get_git_commit raises DirtyRepositoryError when both tracked dirty files "
            "AND untracked files are present — the tracked changes are what matters"
)
def test_mixed_tracked_and_untracked_still_raises(tmp_path):
    """Untracked files alongside a modified tracked file must not suppress the error.

    This pins the filtering logic: ?? lines are ignored, but any other non-empty
    XY status code (M, A, D, R, etc.) blocks the run regardless of how many
    untracked files coexist.
    """
    口 = Step(
        step_num=1,
        name="Patch subprocess.run",
        purpose="Simulate git status with one modified tracked file and several untracked files")
    mixed_status = MagicMock(
        returncode=0,
        stdout=(
            " M wfc/version.py\n"
            "?? .runs/workspace/R2_fqc_cycD1/output.csv\n"
            "?? reports/figure.png\n"
        ),
        stderr="")

    with patch("wfc.version.subprocess.run", return_value=mixed_status):
        口 = Step(
            step_num=2,
            name="Call get_git_commit",
            purpose="Verify DirtyRepositoryError is raised despite the majority of "
                    "lines being untracked")
        from wfc.version import get_git_commit
        with pytest.raises(DirtyRepositoryError, match="uncommitted changes"):
            get_git_commit(tmp_path)


# =============================================================================
# get_or_create_version: deduplication
# =============================================================================

@workflow(
    purpose="Calling get_or_create_version twice with the same (method_id, code_fingerprint) "
            "returns the same MethodVersion.id without creating a duplicate row"
)
def test_get_or_create_version_dedup(tmp_project):
    """Two calls with identical (method_id, code_fingerprint) -> same id, one DB row."""
    口 = Step(
        step_num=1,
        name="Seed method",
        purpose="Insert a minimal Module and Method so method_id is valid")
    method_id = _seed_method()

    口 = Step(
        step_num=2,
        name="Create version twice",
        purpose="Call get_or_create_version twice with identical code_fingerprint")
    fingerprint = "a" * 64
    id_first = get_or_create_version(method_id, fingerprint)
    id_second = get_or_create_version(method_id, fingerprint)

    口 = Step(
        step_num=3,
        name="Verify deduplication",
        purpose="Confirm both calls return the same ID and only one row exists in the DB")
    assert id_first == id_second
    with get_session() as session:
        rows = session.exec(
            select(MethodVersion).where(
                MethodVersion.method_id == method_id,
                MethodVersion.code_fingerprint == fingerprint)
        ).all()
    assert len(rows) == 1


@workflow(
    purpose="When multiple Snakemake workers attempt to record a new method version at the same "
            "time, all workers end up with the same version record — the concurrency fallback "
            "ensures only one row is ever written"
)
def test_get_or_create_version_concurrent_race(tmp_project):
    """8 threads race to INSERT the same (method_id, code_fingerprint) simultaneously.

    The first thread to commit wins; the remaining 7 hit a UNIQUE constraint
    violation (IntegrityError) and fall through to the re-SELECT fallback.
    All 8 must return the same MethodVersion.id and exactly one row must exist.
    """
    口 = Step(
        step_num=1,
        name="Seed method",
        purpose="Insert a minimal Module and Method so method_id is valid for all workers")
    method_id = _seed_method(module_name="race_mod", method_name="race_method")
    fingerprint = "c" * 64

    口 = Step(
        step_num=2,
        name="Race to register version",
        purpose="Start 8 pipeline workers simultaneously, all trying to record the same "
                "method version at the same moment")
    results: list[int] = []
    errors: list[Exception] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait()  # all threads start the DB call at the same moment
            vid = get_or_create_version(method_id, fingerprint)
            with lock:
                results.append(vid)
        except Exception as exc:
            with lock:
                errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker) for _ in range(8)]
        for f in futures:
            f.result(timeout=10)

    口 = Step(
        step_num=3,
        name="Verify single version record",
        purpose="Confirm every worker received the same version ID, no errors were raised, "
                "and exactly one version row exists in the database")
    assert not errors, f"Workers raised exceptions: {errors}"
    assert len(results) == 8
    assert len(set(results)) == 1, f"Workers returned different IDs: {set(results)}"

    with get_session() as session:
        rows = session.exec(
            select(MethodVersion).where(
                MethodVersion.method_id == method_id,
                MethodVersion.code_fingerprint == fingerprint)
        ).all()
    assert len(rows) == 1


# =============================================================================
# build_input_fingerprint: sorted() is load-bearing
# =============================================================================

@workflow(
    purpose="build_input_fingerprint produces the same fingerprint regardless of "
            "the order in which sample IDs are supplied"
)
def test_build_input_fingerprint_sorted(tmp_project):
    """Two calls with sample_ids in reversed order produce an identical fingerprint.

    This pins the sorted() call in build_input_fingerprint — removing it would
    silently break cache-key stability across different DB insertion orderings.
    """
    口 = Step(
        step_num=1,
        name="Insert two sample rows",
        purpose="Create two Sample DB rows with distinct paths, sizes, and mtimes")
    with get_session() as session:
        s1 = Sample(
            name="samp_a",
            source_path="/data/a.csv",
            registered_path="/proj/data/samples/samp_a/a.csv",
            file_type="csv",
            file_size=1000,
            file_mtime=1_700_000_000.0,
            registration_mode="copy")
        s2 = Sample(
            name="samp_b",
            source_path="/data/b.csv",
            registered_path="/proj/data/samples/samp_b/b.csv",
            file_type="csv",
            file_size=2000,
            file_mtime=1_700_000_001.0,
            registration_mode="copy")
        session.add(s1)
        session.add(s2)
        session.commit()
        session.refresh(s1)
        session.refresh(s2)
        id_a, id_b = s1.id, s2.id  # type: ignore[assignment]

    口 = Step(
        step_num=2,
        name="Fingerprint in both orderings",
        purpose="Call build_input_fingerprint with [a, b] and then [b, a]")
    fp_ab = build_input_fingerprint([], sample_ids=[id_a, id_b])
    fp_ba = build_input_fingerprint([], sample_ids=[id_b, id_a])

    口 = Step(
        step_num=3,
        name="Verify fingerprints match",
        purpose="Confirm insertion order does not alter the fingerprint")
    assert fp_ab == fp_ba
    assert len(fp_ab) == 64  # SHA-256 hex digest length


# =============================================================================
# build_cache_key: determinism and sensitivity
# =============================================================================

@workflow(
    purpose="build_cache_key is deterministic for identical inputs and changes "
            "when any input changes — uses code_fingerprint, not git_commit"
)
def test_build_cache_key_deterministic():
    """Same (code_fingerprint, params, input_fingerprint) -> same key; any change -> different key.

    Pure function -- no DB, no fixtures required.
    """
    口 = Step(
        step_num=1,
        name="Build baseline key",
        purpose="Compute a cache key from a fixed code fingerprint, params, input fingerprint, and env fingerprint")
    code_fp = "b" * 64
    params = {"threshold": 0.5, "normalize": True}
    input_fp = "c" * 64
    env_fp = "a" * 32
    key_1 = build_cache_key(code_fp, params, input_fp, env_fp)
    key_2 = build_cache_key(code_fp, params, input_fp, env_fp)

    口 = Step(
        step_num=2,
        name="Verify idempotence",
        purpose="Confirm two identical calls produce the same 64-char hex key")
    assert key_1 == key_2
    assert len(key_1) == 64

    口 = Step(
        step_num=3,
        name="Verify sensitivity to each input",
        purpose="Confirm that changing code_fingerprint, params, input_fingerprint, or "
                "env_fingerprint each produces a distinct key")
    assert build_cache_key("d" * 64, params, input_fp, env_fp) != key_1
    assert build_cache_key(code_fp, {"threshold": 0.9}, input_fp, env_fp) != key_1
    assert build_cache_key(code_fp, params, "e" * 64, env_fp) != key_1
    assert build_cache_key(code_fp, params, input_fp, "f" * 32) != key_1


# =============================================================================
# register_sample: source stat capture
# =============================================================================

def _setup_dvc_config(project_root):
    """Write wf-canvas.toml with [dvc] section and initialize DVC cache."""
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


@workflow(
    purpose="register_sample records the source file's size and mtime, not the copy's"
)
def test_register_sample_captures_source_mtime(tmp_project):
    """file_size and file_mtime on the Sample row must match the source file stat.

    Post-copy timestamps vary by filesystem and calendar day; the source stat is
    the stable identity of the data file regardless of when it was copied.
    """
    口 = Step(
        step_num=1,
        name="Setup DVC config",
        purpose="DVC is now required for sample registration (ADR-009)")
    _setup_dvc_config(tmp_project)

    口 = Step(
        step_num=2,
        name="Create source file",
        purpose="Write a source CSV with known content so its stat is deterministic")
    src = tmp_project / "raw_data.csv"
    src.write_text("col_a,col_b\n1,2\n3,4\n")
    src_stat = src.stat()

    口 = Step(
        step_num=3,
        name="Register the sample",
        purpose="Call register_sample to copy the file and record it in the DB")
    register_sample(name="my_sample", source_path=src, project_root=tmp_project)

    口 = Step(
        step_num=4,
        name="Verify DB stat values match source",
        purpose="Confirm file_size and file_mtime on the Sample row equal the source file's stat")
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "my_sample")).first()
    assert row is not None
    assert row.file_size == src_stat.st_size
    assert row.file_mtime == src_stat.st_mtime


# =============================================================================
# register_sample: link-mode guard
# =============================================================================

@workflow(
    purpose="register_sample raises NotImplementedError when registration_mode='link'"
)
def test_registration_mode_link_guard(tmp_project):
    """registration_mode='link' is reserved — must raise NotImplementedError immediately."""
    口 = Step(
        step_num=1,
        name="Create source file",
        purpose="Provide a real source path so the function does not fail on a missing file first")
    src = tmp_project / "data.csv"
    src.write_text("x\n1\n")

    口 = Step(
        step_num=2,
        name="Attempt link registration",
        purpose="Confirm the function rejects link mode before touching the filesystem")
    with pytest.raises(NotImplementedError, match="link"):
        register_sample(
            name="link_sample",
            source_path=src,
            project_root=tmp_project,
            registration_mode="link")


# =============================================================================
# pre_run: cache miss
# =============================================================================

def _ensure_method_source(project_dir, method_name="v_method"):
    """Create a minimal method source directory with a .py file for fingerprinting."""
    method_dir = Path(project_dir) / "methods" / method_name
    method_dir.mkdir(parents=True, exist_ok=True)
    script = method_dir / f"{method_name}.py"
    if not script.exists():
        script.write_text("def main():\n    pass\n")
    return method_dir


@workflow(
    purpose="pre_run on a cache miss registers a new Run with version_id and cache_key set"
)
def test_pre_run_miss_creates_versioned_run(tmp_project):
    """First call -> ('NEW', run_id); Run row has version_id and cache_key populated."""
    口 = Step(
        step_num=1,
        name="Seed module, method, and source files",
        purpose="Insert the Module and Method rows pre_run requires and create method source dir")
    _seed_method(module_name="versioning_mod", method_name="v_method")
    _ensure_method_source(tmp_project, "v_method")

    口 = Step(
        step_num=2,
        name="Call pre_run with a fixed commit",
        purpose="Bypass git subprocess by supplying git_commit directly; expect a cache miss")
    commit = "f" * 40
    flag, run_id = pre_run(
        method_name="v_method",
        module_name="versioning_mod",
        sample="samp_x",
        params={"alpha": 0.1},
        git_commit=commit)

    口 = Step(
        step_num=3,
        name="Verify return value and DB state",
        purpose="Confirm flag is NEW and the Run row has version_id and cache_key set")
    assert flag == "NEW"
    assert isinstance(run_id, int)

    with get_session() as session:
        run = session.get(Run, run_id)
    assert run is not None
    assert run.version_id is not None
    assert run.cache_key is not None
    assert len(run.cache_key) == 64
    assert run.cache_source_run_id is None

    # Verify git_commit stored as audit metadata on MethodVersion
    with get_session() as session:
        mv = session.get(MethodVersion, run.version_id)
    assert mv is not None
    assert mv.code_fingerprint is not None
    assert len(mv.code_fingerprint) == 64
    assert mv.git_commit == commit


# =============================================================================
# pre_run: cache hit
# =============================================================================

@workflow(
    purpose="A second identical pre_run call returns CACHED with the audit "
            "row's ID (not the cached source), and records cache_source_run_id "
            "on the audit row so the original can still be reached when needed"
)
def test_pre_run_hit_returns_cached_flag(tmp_project):
    """Two identical pre_run calls → second returns ('CACHED', audit_id).

    The returned ID is the newly-inserted audit row, not the cached source
    — that contract change lets downstream code (restore_output, sidecar
    writer) stamp workspace sidecars with the audit row's ID, keeping DAG
    lineage wired through the *current* pipeline instead of leaking back
    into whatever pipeline produced the cached outputs.
    """
    口 = Step(
        step_num=1,
        name="Seed module, method, and source files",
        purpose="Insert the Module and Method rows both pre_run calls require and create method source dir")
    _seed_method(module_name="versioning_mod", method_name="v_method")
    _ensure_method_source(tmp_project, "v_method")

    口 = Step(
        step_num=2,
        name="First pre_run — cache miss",
        purpose="Register an initial run and manually mark it completed so it is eligible for caching")
    commit = "a1" * 20  # 40 chars
    flag_1, run_id_1 = pre_run(
        method_name="v_method",
        module_name="versioning_mod",
        sample="samp_y",
        params={"beta": 0.5},
        git_commit=commit)
    assert flag_1 == "NEW"

    # Mark the run completed so the cache-lookup query can match it
    with get_session() as session:
        run = session.get(Run, run_id_1)
        run.status = "completed"  # type: ignore[union-attr]
        session.add(run)
        session.commit()

    口 = Step(
        step_num=3,
        name="Second pre_run — cache hit",
        purpose="Repeat the identical call and confirm it resolves to the cached run")
    flag_2, returned_id = pre_run(
        method_name="v_method",
        module_name="versioning_mod",
        sample="samp_y",
        params={"beta": 0.5},
        git_commit=commit)

    口 = Step(
        step_num=4,
        name="Verify return value and audit row",
        purpose="Confirm CACHED flag, returned ID is the audit row (not the "
                "source), and the audit Run row has cache_source_run_id set "
                "to the original run's ID")
    assert flag_2 == "CACHED"
    # Contract: CACHED now returns the audit row's ID, not the source's.
    assert returned_id != run_id_1

    with get_session() as session:
        audit = session.get(Run, returned_id)
    assert audit is not None
    assert audit.cache_source_run_id == run_id_1
    assert audit.cache_key is not None
    assert audit.version_id is not None


@workflow(
    purpose="Cache-hit audit rows must record RunInput lineage for the current "
            "pipeline so fan-out sub-DAGs where some branches cache-hit still "
            "render as connected paths in PathsView / DescendantTree"
)
def test_pre_run_hit_records_run_inputs_for_audit_row(tmp_project):
    """Regression: cache-hit audit rows must insert RunInput rows.

    Without this, any fan-out pipeline where some sample branches cache-hit
    will show disconnected orphan runs in the history tab — every cache-hit
    run loses its parent chain because ``run_inputs`` is only populated on
    the cache-miss path.
    """
    from wfc.models import RunInput

    口 = Step(
        step_num=1,
        name="Seed module, method, and a fake upstream Run row",
        purpose="The audit-row lineage recording needs concrete source_run_ids to reference")
    _seed_method(module_name="versioning_mod", method_name="v_method")
    _ensure_method_source(tmp_project, "v_method")
    with get_session() as session:
        upstream = Run(
            method_id=1,  # seeded method id
            sample="samp_z",
            status="completed",
            started_at=datetime.now(timezone.utc),
        )
        session.add(upstream)
        session.commit()
        session.refresh(upstream)
        upstream_id: int = upstream.id  # type: ignore[assignment]

    口 = Step(
        step_num=2,
        name="First pre_run — cache miss with a parent slot link",
        purpose="Register the baseline run that the second call will cache-hit against")
    commit = "c" * 40
    parent_link = f"data:{upstream_id}"
    flag_1, run_id_1 = pre_run(
        method_name="v_method",
        module_name="versioning_mod",
        sample="samp_z",
        params={"gamma": 0.25},
        parent_run_ids=[parent_link],
        git_commit=commit)
    assert flag_1 == "NEW"

    with get_session() as session:
        run = session.get(Run, run_id_1)
        run.status = "completed"  # type: ignore[union-attr]
        session.add(run)
        session.commit()

    口 = Step(
        step_num=3,
        name="Second pre_run — cache hit with the same parent link",
        purpose="Trigger the cache-hit audit-row path and inspect its run_inputs")
    flag_2, audit_id = pre_run(
        method_name="v_method",
        module_name="versioning_mod",
        sample="samp_z",
        params={"gamma": 0.25},
        parent_run_ids=[parent_link],
        git_commit=commit)
    assert flag_2 == "CACHED"
    # pre_run's new contract returns the audit row on CACHED, not the source.
    assert audit_id != run_id_1

    口 = Step(
        step_num=4,
        name="Verify audit-row RunInput",
        purpose="The audit row must carry one RunInput row referencing the "
                "upstream via slot 'data' and reference the cached source "
                "via cache_source_run_id")
    with get_session() as session:
        audit = session.get(Run, audit_id)
        assert audit is not None
        assert audit.cache_source_run_id == run_id_1
        inputs = session.exec(
            select(RunInput).where(RunInput.run_id == audit_id)
        ).all()
    assert len(inputs) == 1
    assert inputs[0].input_name == "data"
    assert inputs[0].source_run_id == upstream_id


# =============================================================================
# build_code_fingerprint: determinism and edge cases
# =============================================================================

def test_build_code_fingerprint_deterministic(tmp_path):
    """Same directory contents -> same fingerprint; different contents -> different."""
    # Create a method source directory with two .py files
    src_dir = tmp_path / "method_a"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def run():\n    return 1\n")
    (src_dir / "helper.py").write_text("def helper():\n    pass\n")

    fp1 = build_code_fingerprint(src_dir)
    fp2 = build_code_fingerprint(src_dir)
    assert fp1 == fp2
    assert len(fp1) == 64

    # Modify a file -> fingerprint changes
    (src_dir / "helper.py").write_text("def helper():\n    return 42\n")
    fp3 = build_code_fingerprint(src_dir)
    assert fp3 != fp1


def test_build_code_fingerprint_empty_dir_raises(tmp_path):
    """Empty directory (no .py files) raises ValueError."""
    empty_dir = tmp_path / "empty_method"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="no .py files"):
        build_code_fingerprint(empty_dir)


def test_build_code_fingerprint_missing_dir_raises(tmp_path):
    """Non-existent directory raises ValueError."""
    with pytest.raises(ValueError, match="does not exist"):
        build_code_fingerprint(tmp_path / "nonexistent")


# =============================================================================
# Code fingerprint stable across unrelated file changes (US-1)
# =============================================================================

@workflow(
    purpose="Code fingerprint remains stable when unrelated files change — "
            "only .py files in the method directory affect the fingerprint"
)
def test_fingerprint_stable_across_unrelated_changes(tmp_path):
    """Add/modify files outside the method dir -> fingerprint unchanged."""
    口 = Step(
        step_num=1,
        name="Create method source directory",
        purpose="Write a method .py file and compute initial fingerprint")
    method_dir = tmp_path / "methods" / "my_method"
    method_dir.mkdir(parents=True)
    (method_dir / "my_method.py").write_text("def run():\n    return 1\n")
    fp_before = build_code_fingerprint(method_dir)

    口 = Step(
        step_num=2,
        name="Make unrelated changes",
        purpose="Create files outside the method directory (docs, config, other methods)")
    (tmp_path / "README.md").write_text("# Updated docs\n")
    other_method = tmp_path / "methods" / "other_method"
    other_method.mkdir(parents=True)
    (other_method / "other.py").write_text("def other(): pass\n")
    (method_dir / "data.csv").write_text("a,b\n1,2\n")  # non-.py file in method dir

    口 = Step(
        step_num=3,
        name="Verify fingerprint unchanged",
        purpose="Recompute fingerprint and confirm it matches the original")
    fp_after = build_code_fingerprint(method_dir)
    assert fp_before == fp_after


# =============================================================================
# Version lookup by fingerprint: same fingerprint, different commits (US-3)
# =============================================================================

@workflow(
    purpose="Two calls to get_or_create_version with the same code_fingerprint but "
            "different git_commits return the same MethodVersion — identical code "
            "across commits shares cached results"
)
def test_same_fingerprint_different_commits_same_version(tmp_project):
    """Same code_fingerprint + different git_commits -> same MethodVersion row."""
    口 = Step(
        step_num=1,
        name="Seed method",
        purpose="Insert a minimal Module and Method")
    method_id = _seed_method(module_name="fp_mod", method_name="fp_method")

    口 = Step(
        step_num=2,
        name="Create version with two different commits but same fingerprint",
        purpose="Simulate identical code across two different git commits")
    fingerprint = "ab" * 32  # 64 chars
    commit_a = "a" * 40
    commit_b = "b" * 40
    id_a = get_or_create_version(method_id, fingerprint, git_commit=commit_a)
    id_b = get_or_create_version(method_id, fingerprint, git_commit=commit_b)

    口 = Step(
        step_num=3,
        name="Verify same version returned",
        purpose="Confirm both calls return the same MethodVersion.id")
    assert id_a == id_b

    with get_session() as session:
        rows = session.exec(
            select(MethodVersion).where(
                MethodVersion.method_id == method_id,
                MethodVersion.code_fingerprint == fingerprint)
        ).all()
    assert len(rows) == 1


@workflow(
    purpose="Two calls to get_or_create_version with different code_fingerprints "
            "return different MethodVersions — distinct code produces distinct versions"
)
def test_different_fingerprints_different_versions(tmp_project):
    """Different code_fingerprints -> different MethodVersion rows."""
    口 = Step(
        step_num=1,
        name="Seed method",
        purpose="Insert a minimal Module and Method")
    method_id = _seed_method(module_name="fp_mod2", method_name="fp_method2")

    口 = Step(
        step_num=2,
        name="Create versions with different fingerprints",
        purpose="Simulate two different code versions of the same method")
    fp_v1 = "a" * 64
    fp_v2 = "b" * 64
    id_v1 = get_or_create_version(method_id, fp_v1, git_commit="c" * 40)
    id_v2 = get_or_create_version(method_id, fp_v2, git_commit="d" * 40)

    口 = Step(
        step_num=3,
        name="Verify different versions",
        purpose="Confirm the two calls return different MethodVersion.ids")
    assert id_v1 != id_v2


# =============================================================================
# US-2: Source snapshot — register_method copies source and fingerprint isolation
# =============================================================================

@workflow(
    purpose="After register_method, source files are copied to methods/{method_name}/ "
            "and match the originals"
)
def test_register_copies_source_files(tmp_project):
    """register_method copies .py files from the source dir to the registered location."""
    口 = Step(
        step_num=1,
        name="Set up project and method source",
        purpose="Create a git-initialised project with a method in a non-registered location")
    init_project(tmp_project, init_git=True)

    # Create a method source dir outside methods/ so the copy step fires
    src_dir = tmp_project / "workspace" / "my_method"
    src_dir.mkdir(parents=True)
    script = src_dir / "my_method.py"
    script.write_text("def main():\n    return 42\n")
    helper = src_dir / "utils.py"
    helper.write_text("def helper():\n    return 1\n")
    # Also create a subdirectory .py file to verify rglob copies recursively
    sub_dir = src_dir / "sub"
    sub_dir.mkdir()
    sub_script = sub_dir / "nested.py"
    sub_script.write_text("def nested():\n    return 99\n")

    口 = Step(
        step_num=2,
        name="Register module and method",
        purpose="Run register_method from the workspace source directory")
    register_module(name="test_mod", contracts=[], description="test module")
    register_method(method_dir=src_dir, module_name="test_mod")

    口 = Step(
        step_num=3,
        name="Verify source files copied to registered location",
        purpose="methods/my_method/ must contain all .py files matching the originals")
    registered_dir = tmp_project / "methods" / "my_method"
    assert registered_dir.is_dir(), "registered directory should exist"

    # Check top-level files
    assert (registered_dir / "my_method.py").exists()
    assert (registered_dir / "utils.py").exists()
    assert filecmp.cmp(
        str(script), str(registered_dir / "my_method.py"), shallow=False)
    assert filecmp.cmp(
        str(helper), str(registered_dir / "utils.py"), shallow=False)

    # Check subdirectory file (proves rglob, not glob)
    assert (registered_dir / "sub" / "nested.py").exists()
    assert filecmp.cmp(
        str(sub_script), str(registered_dir / "sub" / "nested.py"), shallow=False)


@workflow(
    purpose="Fingerprint from registered copy is unchanged after modifying original source — "
            "proves fingerprint is computed from registered copy, not working tree"
)
def test_fingerprint_stable_after_original_modified(tmp_project):
    """Modify the original source after registration; fingerprint must not change."""
    口 = Step(
        step_num=1,
        name="Set up project and method source",
        purpose="Create a project with a method in a workspace directory")
    init_project(tmp_project, init_git=True)

    src_dir = tmp_project / "workspace" / "fp_method"
    src_dir.mkdir(parents=True)
    script = src_dir / "fp_method.py"
    script.write_text("def main():\n    return 1\n")

    口 = Step(
        step_num=2,
        name="Register method and compute initial fingerprint",
        purpose="Register copies source to methods/fp_method/; fingerprint is from that copy")
    register_module(name="fp_mod", contracts=[], description="fingerprint test")
    register_method(method_dir=src_dir, module_name="fp_mod")

    registered_dir = tmp_project / "methods" / "fp_method"
    fp_before = build_code_fingerprint(registered_dir)

    口 = Step(
        step_num=3,
        name="Modify original source file",
        purpose="Change the working-tree copy that is NOT the registered copy")
    script.write_text("def main():\n    return 999  # changed\n")

    口 = Step(
        step_num=4,
        name="Recompute fingerprint from registered copy",
        purpose="Fingerprint must be unchanged because it reads from methods/, not workspace/")
    fp_after = build_code_fingerprint(registered_dir)
    assert fp_before == fp_after, (
        f"Fingerprint changed after modifying original source: {fp_before} != {fp_after}"
    )
