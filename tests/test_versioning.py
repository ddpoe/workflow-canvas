"""
Unit & integration tests: content-addressed versioning, input fingerprinting,
and run caching.

Coverage:
  - DirtyRepositoryError raised when working tree is dirty
  - build_code_fingerprint determinism and edge cases
  - get_or_create_version deduplicates (method_id, code_fingerprint) pairs
  - build_input_fingerprint is stable regardless of the order identities are handed in
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
import hashlib
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

# seed_sample_row, not create_sample_csv, deliberately: these tests compose and
# compare cache keys, where the row's content_hash IS the input under test.
# Nothing here runs a pipeline or restores a sample.
from tests.conftest import seed_sample_row
from tests.fixtures.routes import completed_run
from wfc.contracts import parse_method_yaml, render_contract_projection
from wfc.persistence import get_session
from wfc.init import init_project
from wfc.persistence import MethodVersion, Run, Sample
from wfc.registration import register_module, register_method
from wfc.identity import (
    SampleIdentity,
    build_cache_key,
    build_code_fingerprint,
    build_input_fingerprint,
)
from wfc.version import DirtyRepositoryError
from wfc.registration import get_or_create_version
from wfc.registration import register_sample
from wfc.execution.claim import pre_run


# =============================================================================
# Helpers
# =============================================================================

def _projection_of(method_dir) -> str | None:
    """The contract projection the claim phase reads from a method directory.

    The claim phase fingerprints the registered copy's ``method.yaml``, so a
    test comparing fingerprints across a registration round-trip reads its
    projection from the same place.
    """
    return render_contract_projection(parse_method_yaml(Path(method_dir)))


def _seed_method(module_name: str = "versioning_mod", method_name: str = "v_method") -> int:
    """Insert a minimal Module + Method into the DB and return method.id."""
    from wfc.persistence import Module, Method
    with get_session() as session:
        mod = Module(name=module_name, description="versioning test module")
        session.add(mod)
        session.commit()
        session.refresh(mod)
        method = Method(name=method_name, module_id=mod.id, script_path="methods/v_method/run.py", env="fixture-env")
        session.add(method)
        session.commit()
        session.refresh(method)
        return method.id  # type: ignore[return-value]


# =============================================================================
# get_git_commit: dirty-tree guard
# =============================================================================

def _git(repo: Path, *args: str) -> str:
    """Run git in *repo* (raises on failure) and return its stdout."""
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


def _dirty_tracked_file(repo: Path) -> None:
    """Commit a tracked file in *repo*, then modify it without committing."""
    tracked = repo / "some_file.py"
    tracked.write_text("x = 1\n")
    _git(repo, "add", "some_file.py")
    _git(repo, "commit", "-q", "-m", "add some_file")
    tracked.write_text("x = 2\n")


def _untracked_outputs(repo: Path) -> None:
    """Write run outputs and a database no one commits: untracked paths."""
    for rel in (".runs/workspace/R2_fqc_cycD1/Rep2_siRNA/default/output.csv",
                ".runs/workspace/R2_fqc_scr50/Rep2_siRNA/default/output.csv",
                ".wfc/workflow.db"):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("data\n")


@workflow(
    purpose="get_git_commit raises DirtyRepositoryError when the working tree has uncommitted changes"
)
def test_dirty_tree_raises(git_project):
    口 = Step(
        step_num=1,
        name="Modify a tracked file",
        purpose="A real repository whose working tree differs from HEAD")
    _dirty_tracked_file(git_project)

    口 = Step(
        step_num=2,
        name="Call get_git_commit",
        purpose="A dirty tree is refused, naming the file")
    from wfc.version import get_git_commit
    with pytest.raises(DirtyRepositoryError, match="uncommitted changes") as refusal:
        get_git_commit(git_project)
    assert "some_file.py" in str(refusal.value)


@workflow(
    purpose="get_git_commit succeeds when only untracked files are present — "
            "run outputs and other untracked paths must not trigger DirtyRepositoryError"
)
def test_untracked_files_do_not_block_run(git_project):
    """Untracked files must be ignored: they do not change the commit.

    Run outputs nobody commits show up as untracked in git status. They
    must never prevent a pipeline from executing.
    """
    口 = Step(
        step_num=1,
        name="Write untracked run outputs",
        purpose="A real repository whose only changes are untracked paths")
    _untracked_outputs(git_project)

    口 = Step(
        step_num=2,
        name="Call get_git_commit",
        purpose="It returns HEAD's SHA without raising")
    from wfc.version import get_git_commit
    head = _git(git_project, "rev-parse", "HEAD").strip()
    assert get_git_commit(git_project) == head


@workflow(
    purpose="get_git_commit raises DirtyRepositoryError when both tracked dirty files "
            "AND untracked files are present — the tracked changes are what matters"
)
def test_mixed_tracked_and_untracked_still_raises(git_project):
    """Untracked files alongside a modified tracked file must not suppress the error."""
    口 = Step(
        step_num=1,
        name="Modify a tracked file among untracked outputs",
        purpose="One modified tracked file and several untracked paths")
    _untracked_outputs(git_project)
    _dirty_tracked_file(git_project)

    口 = Step(
        step_num=2,
        name="Call get_git_commit",
        purpose="Verify DirtyRepositoryError is raised despite most changed "
                "paths being untracked")
    from wfc.version import get_git_commit
    with pytest.raises(DirtyRepositoryError, match="uncommitted changes"):
        get_git_commit(git_project)


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
            "the order in which sample identities are handed in"
)
def test_build_input_fingerprint_sorted():
    """Two calls with the sample identities in reversed order produce an identical fingerprint.

    This pins the sorted() call in the digest — removing it would silently
    break cache-key stability across different DB row orderings. Pure over
    literal parts: the expectation is hashlib over the sorted comma-join.
    """
    口 = Step(
        step_num=1,
        name="Two sample identities into one slot",
        purpose="Literal content hashes, chosen so the sorted order of their "
                "parts is the reverse of the order they are handed in")
    samp_a = SampleIdentity(input_slot="data", content_hash="b" * 32)
    samp_b = SampleIdentity(input_slot="data", content_hash="a" * 32)

    口 = Step(
        step_num=2,
        name="Fingerprint in both orderings",
        purpose="Call build_input_fingerprint with [a, b] and then [b, a]")
    fp_ab = build_input_fingerprint([], [samp_a, samp_b])
    fp_ba = build_input_fingerprint([], [samp_b, samp_a])

    口 = Step(
        step_num=3,
        name="Verify fingerprints match the literal",
        purpose="Confirm the order handed in does not alter the fingerprint, "
                "and that both equal the digest of the sorted comma-join")
    assert fp_ab == fp_ba
    assert fp_ab == hashlib.sha256(
        b"hash:data:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,"
        b"hash:data:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ).hexdigest()


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
        purpose="Compute a cache key from a fixed code fingerprint, params, input "
                "fingerprint, env fingerprint and method identity")
    code_fp = "b" * 64
    params = {"threshold": 0.5, "normalize": True}
    input_fp = "c" * 64
    env_fp = "a" * 32
    method_id = "mod_one.my_method"
    key_1 = build_cache_key(code_fp, params, input_fp, env_fp, method_id)
    key_2 = build_cache_key(code_fp, params, input_fp, env_fp, method_id)

    口 = Step(
        step_num=2,
        name="Verify idempotence",
        purpose="Confirm two identical calls produce the same 64-char hex key")
    assert key_1 == key_2
    # The composition: code + params as sorted-key JSON + input + env +
    # <module>.<method>, no delimiter, SHA256 — pinned as hashlib over
    # literal bytes.
    assert key_1 == hashlib.sha256(
        ("b" * 64 + '{"normalize": true, "threshold": 0.5}' + "c" * 64
         + "a" * 32 + "mod_one.my_method").encode()
    ).hexdigest()

    口 = Step(
        step_num=3,
        name="Verify sensitivity to each input",
        purpose="Confirm that changing code_fingerprint, params, input_fingerprint, "
                "env_fingerprint or the method's module-qualified identity each "
                "produces a distinct key")
    assert build_cache_key("d" * 64, params, input_fp, env_fp, method_id) != key_1
    assert build_cache_key(code_fp, {"threshold": 0.9}, input_fp, env_fp, method_id) != key_1
    assert build_cache_key(code_fp, params, "e" * 64, env_fp, method_id) != key_1
    assert build_cache_key(code_fp, params, input_fp, "f" * 32, method_id) != key_1
    assert build_cache_key(
        code_fp, params, input_fp, env_fp, "mod_two.my_method"
    ) != key_1


# =============================================================================
# register_sample: source stat capture
# =============================================================================

def _setup_dvc_config(project_root):
    """Write wf-canvas.toml with [dvc] section and initialize DVC cache."""
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
        purpose="DVC is required for sample registration")
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
    """Create a minimal registered method directory: a script and a contract.

    Both halves are part of a method's code identity, so a registered copy
    carrying no ``method.yaml`` is refused at fingerprint time.
    """
    method_dir = Path(project_dir) / "methods" / method_name
    method_dir.mkdir(parents=True, exist_ok=True)
    script = method_dir / f"{method_name}.py"
    if not script.exists():
        script.write_text("def main():\n    pass\n")
    contract = method_dir / "method.yaml"
    if not contract.exists():
        contract.write_text(
            "env: v_env\n"
            "inputs:\n"
            "  data:\n"
            "    required: false\n"
            "outputs:\n"
            "  result:\n"
            "    type: .csv\n"
        )
    return method_dir


@workflow(
    purpose="pre_run on a cache miss registers a new Run with version_id and cache_key set"
)
def test_pre_run_miss_creates_versioned_run(tmp_project):
    """First call -> ('NEW', run_id); Run row has version_id and cache_key populated."""
    口 = Step(
        step_num=1,
        name="Seed module, method, and source files",
        purpose="Insert the Module, Method and Sample rows pre_run requires and create method source dir")
    _seed_method(module_name="versioning_mod", method_name="v_method")
    _ensure_method_source(tmp_project, "v_method")
    seed_sample_row("samp_x")

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

    The returned ID is the newly-inserted audit row, not the cached source,
    so downstream code (the run-id sidecar writer) stamps sidecars with the
    audit row's ID, keeping DAG lineage wired through the *current* pipeline
    instead of leaking back into whatever pipeline produced the cached
    outputs.
    """
    口 = Step(
        step_num=1,
        name="Seed module, method, and source files",
        purpose="Insert the Module, Method and Sample rows both pre_run calls require and create method source dir")
    _seed_method(module_name="versioning_mod", method_name="v_method")
    _ensure_method_source(tmp_project, "v_method")
    seed_sample_row("samp_y")

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
    # Contract: CACHED returns the audit row's ID, not the source's.
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
            "render as connected paths in PathsView / the Descendants view"
)
def test_pre_run_hit_records_run_inputs_for_audit_row(tmp_project, monkeypatch):
    """Cache-hit audit rows must insert RunInput rows.

    Without them, any fan-out pipeline where some sample branches cache-hit
    shows disconnected orphan runs in the history tab — every cache-hit run
    loses its parent chain if ``run_inputs`` is populated only on the
    cache-miss path.
    """
    from wfc.persistence import RunInput

    口 = Step(
        step_num=1,
        name="Seed module, method, sample, and a fake upstream Run row",
        purpose="The audit-row lineage recording needs concrete source_run_ids to reference; "
                "the upstream records one output so the entry's omitted source slot resolves")
    _seed_method(module_name="versioning_mod", method_name="v_method")
    _ensure_method_source(tmp_project, "v_method")
    # The upstream is a run that ran: one recorded output, on a sample the
    # route registers, so the entry's omitted source slot resolves to it.
    upstream_id = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="v_upstream",
        module="versioning_mod", sample="samp_z",
        outputs={"result": ".csv"}).run_id

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
    # pre_run returns the audit row on CACHED, not the source.
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


@workflow(
    purpose="A consumer whose parent was served from cache keys identically "
            "to the same consumer whose parent ran fresh: the entry's "
            "omitted source slot resolves through the audit hop, and the "
            "audit row carries the source's cache key"
)
def test_consumer_key_is_the_same_whether_its_parent_ran_or_cache_hit(tmp_project):
    """A cache-hit audit row owns no output records of its own.

    Its outputs belong to ``cache_source_run_id``. A consumer entry that
    names no source slot has to resolve through that hop, or the first run
    of a pipeline would pass and every re-run would refuse its cached
    parents. And the key it composes has to come out the same either way,
    or a re-run recomputes the whole downstream graph.
    """
    口 = Step(step_num=1, name="Seed the method, sample, and run the parent fresh",
             purpose="The parent registers, records one output, and completes "
                     "-- the state a later identical claim resolves against")
    from wfc.persistence import RunOutput

    _seed_method(module_name="versioning_mod", method_name="v_method")
    _ensure_method_source(tmp_project, "v_method")
    seed_sample_row("samp_chain")
    commit = "5" * 40
    flag_parent, parent_id = pre_run(
        method_name="v_method", module_name="versioning_mod",
        sample="samp_chain", params={"stage": "up"}, git_commit=commit)
    assert flag_parent == "NEW"
    with get_session() as session:
        run = session.get(Run, parent_id)
        run.status = "completed"  # type: ignore[union-attr]
        session.add(run)
        session.add(RunOutput(
            run_id=parent_id, slot="result", output_name="result.csv",
            artifact_path=f".runs/{parent_id:08d}/result.csv",
            artifact_type="method_file"))
        session.commit()
    # The recorded output exists in the run archive, as the collect phase
    # leaves it: the hit rule reuses a run only when its outputs can be read.
    (tmp_project / f".runs/{parent_id:08d}/result.csv").write_text("x\n")

    口 = Step(step_num=2, name="Claim the consumer against the fresh parent",
             purpose="The entry names no source slot, so it resolves to the "
                     "parent's one recorded output")
    _, consumer_fresh = pre_run(
        method_name="v_method", module_name="versioning_mod",
        sample="samp_chain", params={"stage": "down"},
        parent_run_ids=[f"data:{parent_id}"], git_commit=commit)
    with get_session() as session:
        key_fresh = session.get(Run, consumer_fresh).cache_key  # type: ignore[union-attr]

    口 = Step(step_num=3, name="Re-run the parent — it cache-hits",
             purpose="The audit row references the source run and owns no "
                     "output records of its own")
    flag_again, audit_id = pre_run(
        method_name="v_method", module_name="versioning_mod",
        sample="samp_chain", params={"stage": "up"}, git_commit=commit)
    assert flag_again == "CACHED"
    with get_session() as session:
        audit = session.get(Run, audit_id)
    assert audit.cache_source_run_id == parent_id  # type: ignore[union-attr]

    口 = Step(step_num=4, name="Claim the consumer against the audit row",
             purpose="Same key means the re-run reuses the downstream result "
                     "instead of recomputing the graph below a cached node")
    _, consumer_cached = pre_run(
        method_name="v_method", module_name="versioning_mod",
        sample="samp_chain", params={"stage": "down"},
        parent_run_ids=[f"data:{audit_id}"], git_commit=commit)
    with get_session() as session:
        key_cached = session.get(Run, consumer_cached).cache_key  # type: ignore[union-attr]
    assert key_cached == key_fresh, (
        "a consumer keyed differently below a cached parent recomputes the "
        "whole downstream graph on every re-run"
    )


# =============================================================================
# pre_run: cache hit with deleted archive
# =============================================================================

@workflow(
    purpose="A completed run whose pre-archive output was in the deleted archive "
            "directory is not reused — pre_run returns NEW with a fresh run "
            "row instead of serving a hit whose outputs no longer exist"
)
def test_pre_run_deleted_archive_is_a_miss(tmp_project):
    """Matching completed row + its pre-archive output gone → ('NEW', fresh id).

    A row the archive pass has not reached is read from its run-archive
    path; with the archive deleted that output is in neither place, so
    serving the hit would hand downstream nodes paths that no longer resolve.
    """
    from wfc.persistence import project_root as get_project_root
    from wfc.layout import run_archive_dir

    口 = Step(
        step_num=1,
        name="Seed and complete a cacheable run",
        purpose="First pre_run registers the run and creates its archive dir; "
                "marking it completed makes it eligible for cache lookup")
    _seed_method(module_name="versioning_mod", method_name="v_method")
    _ensure_method_source(tmp_project, "v_method")
    seed_sample_row("samp_gone")
    commit = "d" * 40
    flag_1, run_id_1 = pre_run(
        method_name="v_method",
        module_name="versioning_mod",
        sample="samp_gone",
        params={"delta": 1.5},
        git_commit=commit)
    assert flag_1 == "NEW"
    from wfc.persistence import RunOutput
    artifact = run_archive_dir(get_project_root(), run_id_1) / "out.csv"
    artifact.write_text("x\n")
    with get_session() as session:
        run = session.get(Run, run_id_1)
        run.status = "completed"  # type: ignore[union-attr]
        session.add(run)
        session.add(RunOutput(
            run_id=run_id_1, slot="out", output_name="out.csv",
            artifact_path=str(artifact), artifact_type="method_file"))
        session.commit()

    口 = Step(
        step_num=2,
        name="Delete the run's archive directory",
        purpose="Simulate pruned or manually removed outputs behind a "
                "still-matching Run row")
    archive_dir = run_archive_dir(get_project_root(), run_id_1)
    assert archive_dir.is_dir()
    shutil.rmtree(archive_dir)

    口 = Step(
        step_num=3,
        name="Identical pre_run call",
        purpose="A cache_key match whose archive is gone must register fresh "
                "work, not serve the hit")
    flag_2, run_id_2 = pre_run(
        method_name="v_method",
        module_name="versioning_mod",
        sample="samp_gone",
        params={"delta": 1.5},
        git_commit=commit)

    口 = Step(
        step_num=4,
        name="Verify miss semantics",
        purpose="NEW flag, a fresh running row with no cache_source_run_id, "
                "and a recreated archive dir")
    assert flag_2 == "NEW"
    assert run_id_2 != run_id_1
    with get_session() as session:
        fresh = session.get(Run, run_id_2)
    assert fresh is not None
    assert fresh.status == "running"
    assert fresh.cache_source_run_id is None
    assert run_archive_dir(get_project_root(), run_id_2).is_dir()


# =============================================================================
# The canonical hit lookup: one rule for reuse
# =============================================================================

@workflow(
    purpose="The claim phase and the check_cache verb answer through one lookup: "
            "a completed run with the identical fingerprint and a present archive "
            "is a hit; a key that differs on one axis is a miss; a run that shares "
            "the candidate's lineage (same parents, same params) under a different "
            "key is a miss"
)
def test_cache_hit_lookup_is_the_one_rule(tmp_project, capsys, monkeypatch):
    """Fingerprint match is the one definition of a cache hit."""
    from wfc.cli import cli_main
    from wfc.execution.claim import lookup_cache_hit

    # Seed a completed run with lineage and archive: pre_run registers the
    # candidate (cache key, RunInput lineage, archive dir); marking it
    # completed makes it a hit candidate.
    _seed_method(module_name="versioning_mod", method_name="v_method")
    _ensure_method_source(tmp_project, "v_method")
    upstream_id = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="v_upstream",
        module="versioning_mod", sample="samp_one",
        outputs={"result": ".csv"}).run_id
    capsys.readouterr()  # the route's registration output; the verb's is read below
    parent_link = f"data:{upstream_id}"
    flag, run_id = pre_run(
        method_name="v_method",
        module_name="versioning_mod",
        sample="samp_one",
        params={"k": 1},
        parent_run_ids=[parent_link],
        git_commit="e" * 40)
    assert flag == "NEW"
    with get_session() as session:
        run = session.get(Run, run_id)
        run.status = "completed"  # type: ignore[union-attr]
        session.add(run)
        session.commit()
        cache_key = run.cache_key  # type: ignore[union-attr]

    # Identical fingerprint is a hit: the lookup returns the completed run for
    # its own cache key.
    assert lookup_cache_hit(
        "v_method", "versioning_mod", "samp_one", cache_key) == run_id

    # The verb answers through the same lookup: check_cache computes the key
    # the claim phase would and prints the run id the claim phase would reuse.
    hit_argv = ["check_cache", "--method", "v_method",
                "--module", "versioning_mod", "--sample", "samp_one",
                "--params", '{"k": 1}', "--parent-run-id", parent_link]
    assert cli_main(hit_argv) == 0
    assert capsys.readouterr().out.strip() == str(run_id)

    # One axis changed is a miss: same parents, different params -- the key
    # differs on one axis and the verb prints NONE.
    miss_argv = ["check_cache", "--method", "v_method",
                 "--module", "versioning_mod", "--sample", "samp_one",
                 "--params", '{"k": 2}', "--parent-run-id", parent_link]
    assert cli_main(miss_argv) == 0
    assert capsys.readouterr().out.strip() == "NONE"

    # Same lineage under a different key is a miss: edit the registered
    # source -- the candidate keeps its parents and params (a lineage match)
    # but its code fingerprint, and so its key, differs; the verb prints NONE.
    (tmp_project / "methods" / "v_method" / "v_method.py").write_text(
        "def main():\n    return 2\n")
    assert cli_main(hit_argv) == 0
    assert capsys.readouterr().out.strip() == "NONE"


@workflow(
    purpose="Two modules registering a method of the same name never share a "
            "cache entry: the key is over <module>.<method> and the hit lookup "
            "reaches the method row through its module"
)
def test_two_modules_same_method_name_do_not_share_a_cache_entry(tmp_project):
    """A method name alone does not identify a method, and a key is over identity.

    The two modules share ``methods/shared_name/`` -- the registry is keyed by
    method name until the per-module layout lands -- so their code fingerprint,
    params, env and sample are all identical. The module-qualified key
    component is the only thing keeping the second module's step from being
    served the first module's outputs.
    """
    from wfc.execution.claim import lookup_cache_hit

    口 = Step(step_num=1, name="Seed two modules holding a same-named method",
             purpose="Rows are seeded directly: the registration guard refuses "
                     "exactly this collision once it lands, and the defect being "
                     "witnessed is a claim-phase one")
    _seed_method(module_name="mod_alpha", method_name="shared_name")
    _seed_method(module_name="mod_beta", method_name="shared_name")
    _ensure_method_source(tmp_project, "shared_name")
    seed_sample_row("samp_shared")

    口 = Step(step_num=2, name="Claim and complete the step under the first module",
             purpose="A completed run with a present archive is the one thing a "
                     "cache hit can be served from")
    flag_alpha, run_alpha = pre_run(
        method_name="shared_name", module_name="mod_alpha",
        sample="samp_shared", params={"k": 1}, git_commit="a" * 40)
    assert flag_alpha == "NEW"
    with get_session() as session:
        run = session.get(Run, run_alpha)
        run.status = "completed"  # type: ignore[union-attr]
        session.add(run)
        session.commit()
        key_alpha = run.cache_key  # type: ignore[union-attr]

    口 = Step(step_num=3, name="Claim the same-named method under the second module",
             purpose="Everything the key was over before is identical; only the "
                     "module differs, and that has to be enough")
    flag_beta, run_beta = pre_run(
        method_name="shared_name", module_name="mod_beta",
        sample="samp_shared", params={"k": 1}, git_commit="a" * 40)
    assert flag_beta == "NEW", (
        "the second module's step was served the first module's outputs"
    )
    with get_session() as session:
        key_beta = session.get(Run, run_beta).cache_key  # type: ignore[union-attr]

    口 = Step(step_num=4, name="Both halves of the rule",
             purpose="The keys differ, and the lookup refuses to cross modules "
                     "even when handed the other module's key")
    assert key_alpha != key_beta
    assert lookup_cache_hit(
        "shared_name", "mod_alpha", "samp_shared", key_alpha) == run_alpha
    assert lookup_cache_hit(
        "shared_name", "mod_beta", "samp_shared", key_alpha) is None


# =============================================================================
# build_code_fingerprint: determinism and edge cases
# =============================================================================

# A method's declared contract is part of its code identity, and the renderer
# lives in Contracts because Identity imports no ``wfc`` module -- the
# fingerprint takes the rendering as a value. The tests in this section drive
# the SCRIPT axis, so they hold one contract fixed.
_PROJECTION = render_contract_projection({
    "inputs": {"data": {"required": False}},
    "outputs": {"result": {"type": ".csv"}},
    "executor": "python",
    "script": None,
    "gpus": False,
})


def test_build_code_fingerprint_deterministic(tmp_path):
    """Same directory contents -> same fingerprint; different contents -> different."""
    # Create a method source directory with two .py files
    src_dir = tmp_path / "method_a"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def run():\n    return 1\n")
    (src_dir / "helper.py").write_text("def helper():\n    pass\n")

    fp1 = build_code_fingerprint(src_dir, _PROJECTION)
    fp2 = build_code_fingerprint(src_dir, _PROJECTION)
    assert fp1 == fp2
    assert len(fp1) == 64

    # Modify a file -> fingerprint changes
    (src_dir / "helper.py").write_text("def helper():\n    return 42\n")
    fp3 = build_code_fingerprint(src_dir, _PROJECTION)
    assert fp3 != fp1


def test_build_code_fingerprint_empty_dir_raises(tmp_path):
    """Directory with no recognized script files raises ValueError."""
    empty_dir = tmp_path / "empty_method"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="no recognized script files"):
        build_code_fingerprint(empty_dir, _PROJECTION)

    # A dir with only non-script files (data/config) is equally unrecognized.
    (empty_dir / "notes.txt").write_text("not a script")
    with pytest.raises(ValueError, match="no recognized script files"):
        build_code_fingerprint(empty_dir, _PROJECTION)


def test_build_code_fingerprint_without_a_contract_raises(tmp_path):
    """A registered copy holding no ``method.yaml`` has no identity to fingerprint.

    The contract decides what a run produces and where its outputs are found,
    so a method with none is refused rather than fingerprinted under a
    stand-in marker: a marker would give a contract-less method a silent
    identity that two different methods could share.
    """
    src_dir = tmp_path / "contractless_method"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def run():\n    return 1\n")

    with pytest.raises(ValueError, match="no registered contract"):
        build_code_fingerprint(src_dir, None)


@workflow(
    purpose="Code fingerprint covers all recognized script extensions "
            "(.py/.R/.r/.sh) — an R-only method dir fingerprints, editing the "
            ".R script changes the digest, and a pure-.py dir hashes to the "
            "plain sorted relpath:content digest over its .py files"
)
def test_build_code_fingerprint_recognized_extensions(tmp_path):
    """Cache correctness for R/bash methods + Python invariance."""
    import hashlib

    # --- R-only dir: fingerprint returned; editing the .R changes it ---
    r_dir = tmp_path / "r_method"
    r_dir.mkdir()
    (r_dir / "r_method.R").write_text('x <- Sys.getenv("WFC_RUN_DIR")\n')
    fp_r1 = build_code_fingerprint(r_dir, _PROJECTION)
    assert len(fp_r1) == 64

    (r_dir / "r_method.R").write_text('y <- Sys.getenv("WFC_INPUT_PATHS")\n')
    fp_r2 = build_code_fingerprint(r_dir, _PROJECTION)
    assert fp_r2 != fp_r1

    # --- bash script also participates in the digest ---
    (r_dir / "helper.sh").write_text("echo helper\n")
    fp_r3 = build_code_fingerprint(r_dir, _PROJECTION)
    assert fp_r3 != fp_r2

    # --- Python invariance: a pure-.py dir hashes to the .py-only digest  ---
    # --- with the contract projection folded in last, so recognizing other---
    # --- script extensions leaves the script side of the digest unchanged.---
    py_dir = tmp_path / "py_method"
    py_dir.mkdir()
    (py_dir / "main.py").write_text("def run():\n    return 1\n")
    (py_dir / "helper.py").write_text("def helper():\n    pass\n")

    # Inline reimplementation of the digest (sorted .py rglob,
    # "relpath:content" hash updates, then "contract:<projection>").
    old_hasher = hashlib.sha256()
    for py_file in sorted(
        py_dir.rglob("*.py"), key=lambda p: p.relative_to(py_dir).as_posix()
    ):
        rel = py_file.relative_to(py_dir).as_posix()
        old_hasher.update(f"{rel}:{py_file.read_text(encoding='utf-8')}".encode("utf-8"))
    old_hasher.update(f"contract:{_PROJECTION}".encode("utf-8"))

    assert build_code_fingerprint(py_dir, _PROJECTION) == old_hasher.hexdigest()


def test_build_code_fingerprint_missing_dir_raises(tmp_path):
    """Non-existent directory raises ValueError."""
    with pytest.raises(ValueError, match="does not exist"):
        build_code_fingerprint(tmp_path / "nonexistent", _PROJECTION)


# =============================================================================
# Code fingerprint stable across unrelated file changes
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
    fp_before = build_code_fingerprint(method_dir, _PROJECTION)

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
    fp_after = build_code_fingerprint(method_dir, _PROJECTION)
    assert fp_before == fp_after


# =============================================================================
# Version lookup by fingerprint: same fingerprint, different commits
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
# Source snapshot — register_method copies source and fingerprint isolation
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
    # A method must name a built container env. fixture-env is the record
    # tmp_project writes into .wfc/envs.json (a manifest lookup, no Docker)
    # — this test is about source copying, not envs.
    (src_dir / "method.yaml").write_text(
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  output:\n    type: .csv\n"
        "params: {}\nexecutor: python\n"
        "env: fixture-env\n"
    )

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
    purpose="Re-registering a method after one of its scripts was renamed in "
            "the source purges the stale script from the snapshot, so the code "
            "fingerprint no longer covers it; the snapshot's data file and "
            "method.yaml stay"
)
def test_reregistration_purges_stale_snapshot_script(tmp_project):
    """``registration:snapshot-purge``: snapshot = exactly the registered set."""
    口 = Step(
        step_num=1,
        name="Register a method with two scripts",
        purpose="The snapshot holds the main script, a helper and method.yaml; "
                "a data file is placed beside them")
    init_project(tmp_project, init_git=True)
    src_dir = tmp_project / "workspace" / "purge_method"
    src_dir.mkdir(parents=True)
    (src_dir / "purge_method.py").write_text("def main():\n    return 1\n")
    (src_dir / "helpers_old.py").write_text("def helper():\n    return 2\n")
    (src_dir / "method.yaml").write_text(
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  output:\n    type: .csv\n"
        "params: {}\nexecutor: python\n"
        "env: fixture-env\n"
    )
    register_module(name="purge_mod", contracts=[], description="snapshot purge")
    register_method(method_dir=src_dir, module_name="purge_mod")
    registered_dir = tmp_project / "methods" / "purge_method"
    assert (registered_dir / "helpers_old.py").exists()
    (registered_dir / "lookup.csv").write_text("k,v\n1,2\n")
    fp_before = build_code_fingerprint(registered_dir, _projection_of(registered_dir))

    口 = Step(
        step_num=2,
        name="Rename the helper in the source and register again",
        purpose="The registered set is now the main script and the renamed helper")
    (src_dir / "helpers_old.py").rename(src_dir / "helpers_new.py")
    register_method(method_dir=src_dir, module_name="purge_mod")

    口 = Step(
        step_num=3,
        name="Inspect the snapshot and the fingerprint",
        purpose="The stale script is gone, the data file and method.yaml stay, "
                "and the fingerprint covers exactly the registered set")
    assert not (registered_dir / "helpers_old.py").exists()
    assert (registered_dir / "helpers_new.py").exists()
    assert (registered_dir / "lookup.csv").exists()
    assert (registered_dir / "method.yaml").exists()
    fp_after = build_code_fingerprint(registered_dir, _projection_of(registered_dir))
    assert fp_after != fp_before
    assert fp_after == build_code_fingerprint(src_dir, _projection_of(src_dir))


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
    # A method must name a built container env. fixture-env is the record
    # tmp_project writes into .wfc/envs.json — no Docker needed.
    (src_dir / "method.yaml").write_text(
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  output:\n    type: .csv\n"
        "params: {}\nexecutor: python\n"
        "env: fixture-env\n"
    )

    口 = Step(
        step_num=2,
        name="Register method and compute initial fingerprint",
        purpose="Register copies source to methods/fp_method/; fingerprint is from that copy")
    register_module(name="fp_mod", contracts=[], description="fingerprint test")
    register_method(method_dir=src_dir, module_name="fp_mod")

    registered_dir = tmp_project / "methods" / "fp_method"
    fp_before = build_code_fingerprint(registered_dir, _projection_of(registered_dir))

    口 = Step(
        step_num=3,
        name="Modify original source file",
        purpose="Change the working-tree copy that is NOT the registered copy")
    script.write_text("def main():\n    return 999  # changed\n")

    口 = Step(
        step_num=4,
        name="Recompute fingerprint from registered copy",
        purpose="Fingerprint must be unchanged because it reads from methods/, not workspace/")
    fp_after = build_code_fingerprint(registered_dir, _projection_of(registered_dir))
    assert fp_before == fp_after, (
        f"Fingerprint changed after modifying original source: {fp_before} != {fp_after}"
    )
