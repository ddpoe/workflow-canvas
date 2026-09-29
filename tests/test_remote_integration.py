"""The DVC transport (wfc.storage.transport) + ensure_dvc_ready.

Tier 2: pure-logic tests over has_remote_configured + ensure_dvc_ready.
Tier 3 (CI-load-bearing): real DVC DataCloud round-trip via a local-FS
remote — exercises the actual dvc.repo.Repo + dvc_data.hashfile.hash_info
import surface so we catch upstream API drift early.
"""

import configparser
import subprocess
import textwrap
import time
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from tests.conftest import requires_docker
from tests.fixtures.fakes import stub_transport


# =============================================================================
# Tier 2: has_remote_configured
# =============================================================================

def test_has_remote_configured_no_config_returns_false(tmp_path):
    """No .dvc/config -> False."""
    from wfc.storage import has_remote_configured
    assert has_remote_configured(tmp_path) is False


def test_has_remote_configured_with_remote_returns_true(tmp_path):
    """A .dvc/config with [remote "name"] section -> True."""
    (tmp_path / ".dvc").mkdir()
    (tmp_path / ".dvc" / "config").write_text(
        "[core]\nremote = default\n"
        '[remote "default"]\nurl = /tmp/foo\n'
    )
    from wfc.storage import has_remote_configured
    assert has_remote_configured(tmp_path) is True


def test_has_remote_configured_empty_config_returns_false(tmp_path):
    """A .dvc/config without any remote section -> False."""
    (tmp_path / ".dvc").mkdir()
    (tmp_path / ".dvc" / "config").write_text("[core]\nautostage = true\n")
    from wfc.storage import has_remote_configured
    assert has_remote_configured(tmp_path) is False


# =============================================================================
# Tier 2: ensure_dvc_ready accepts ssh/s3 URLs
# =============================================================================

_BASE = textwrap.dedent("""\
    [database]
    url = "sqlite:///{db_path}"
    [project]
    name = "test"
    [pixi]
    root = ".pixi"
""")


def _write_wf(project_dir: Path, dvc_url: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".wfc").mkdir(parents=True, exist_ok=True)
    db = (project_dir / ".wfc" / "wfc.db").as_posix()
    (project_dir / ".wfc" / "wf-canvas.toml").write_text(
        _BASE.format(db_path=db) + f'\n[dvc]\nurl = "{dvc_url}"\n'
    )


def _write_dvc_cfg(project_dir: Path, url: str) -> None:
    (project_dir / ".dvc").mkdir(parents=True, exist_ok=True)
    (project_dir / ".dvc" / "config").write_text(
        f'[core]\nremote = default\n[remote "default"]\nurl = {url}\n'
    )


@pytest.mark.parametrize("url", ["s3://bucket/x", "ssh://user@host/path", "gs://b/p"])
def test_ensure_dvc_ready_accepts_non_local_urls(tmp_path, url):
    """Any remote URL scheme is accepted, not only local paths."""
    _write_wf(tmp_path, url)
    _write_dvc_cfg(tmp_path, url)
    from wfc.storage import ensure_dvc_ready
    result = ensure_dvc_ready(tmp_path)
    assert result["url"] == url


# =============================================================================
# Tier 3 (CI-load-bearing): real DVC DataCloud round-trip
# =============================================================================

@workflow(
    purpose=(
        "real DVC DataCloud push+pull round-trip via local-FS remote"
    ),
    inputs="known bytes -> cache_file -> remote_push",
    outputs="pull retrieves byte-identical content from remote",
)
def test_remote_push_pull_round_trip(tmp_path):
    """Pushes a known file through wfc.storage.transport.push then pulls it back.

    Safety net for DVC Python API drift -- if HashInfo moves or
    DataCloud.push changes shape, this fails.
    """
    _ = Step(step_num=1, name="Set up DVC repo + remote",
             purpose="Initialize a DVC repo with a local-FS remote")
    project = tmp_path / "proj"
    remote = tmp_path / "remote_storage"
    remote.mkdir()

    # Initialize a real DVC repo (no_scm=True so we don't need git).
    from dvc.repo import Repo
    Repo.init(str(project), no_scm=True)

    # Wire the remote into .dvc/config.
    cfg = project / ".dvc" / "config"
    parser = configparser.ConfigParser()
    parser.read(cfg)
    if not parser.has_section("core"):
        parser.add_section("core")
    parser.set("core", "remote", "default")
    parser.add_section('remote "default"')
    parser.set('remote "default"', "url", str(remote))
    with open(cfg, "w") as f:
        parser.write(f)

    _ = Step(step_num=2, name="Cache a known file",
             purpose="cache_file moves bytes into .dvc/cache/files/md5/")
    from wfc.identity import hash_path
    from wfc.storage import cache_file
    payload = project / "payload.txt"
    payload.write_bytes(b"hello-remote")
    h = hash_path(payload)
    cache_file(payload, h, project, move=False)

    _ = Step(step_num=3, name="Push to remote",
             purpose="wfc.storage.transport.push uploads via DataCloud.push")
    from wfc.storage import push as remote_push, pull as remote_pull
    remote_push([h], project)

    # Verify remote has the file (any DVC remote layout is OK).
    remote_files = list(remote.rglob("*"))
    assert any(f.is_file() for f in remote_files), (
        f"remote {remote} should contain at least one pushed file"
    )

    _ = Step(step_num=4, name="Prune local cache + pull",
             purpose="Removing local cache forces pull to fetch from remote")
    import os as _os, stat as _stat
    cache_root = project / ".dvc" / "cache" / "files" / "md5"
    # On Windows DVC marks cache files read-only.  Unlink each file
    # after chmod-ing it; tolerate already-gone files.
    for f in cache_root.rglob("*"):
        if f.is_file():
            try:
                _os.chmod(f, _stat.S_IWRITE)
                f.unlink()
            except (FileNotFoundError, PermissionError):
                pass

    remote_pull([h], project)

    # Re-hash from the local cache and verify identity.
    restored = cache_root / h[:2] / h[2:]
    assert restored.exists(), f"pull should have restored {restored}"
    assert restored.read_bytes() == b"hello-remote"


# =============================================================================
# Tier 3: sample restore pulls a local miss from a real remote
# =============================================================================

@workflow(
    purpose=(
        "A registered sample whose cache entry is only on the remote is "
        "pulled into the cache and restored byte-identical to its recorded "
        "path; with the entry in neither place the restore exits 1"
    ),
    inputs="a sample registered and pushed to a real local-filesystem DVC remote",
    outputs="the sample's bytes at its recorded path, then exit code 1",
)
def test_restore_sample_pulls_a_local_miss(tmp_project, monkeypatch):
    """``storage:sample-restore-pull`` against a real DVC remote, no stubs."""
    import os
    import stat
    from wfc.registration import register_sample
    from wfc.storage import restore_sample
    from wfc.persistence import PushStatus, Sample, get_session
    from wfc.storage import init_dvc
    from wfc.storage.cache import _cache_path

    def _force_unlink(p: Path) -> None:
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
        p.unlink()

    口 = Step(step_num=1, name="Configure a local-filesystem remote",
             purpose="Declare [dvc] url outside the project and mirror it "
                     "into .dvc/config, as wfc init does")
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
    remote = tmp_project.parent / f"{tmp_project.name}-remote"
    remote.mkdir()
    _write_wf(tmp_project, remote.as_posix())
    init_dvc(tmp_project, {"url": str(remote)})

    口 = Step(step_num=2, name="Register and push a sample",
             purpose="A standalone registration caches the bytes and pushes "
                     "them synchronously")
    payload = b"id,value\n1,42\n"
    src = tmp_project / "restore_me.csv"
    src.write_bytes(payload)
    register_sample(name="pull_sample", source_path=src, project_root=tmp_project)
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "pull_sample")).one()
        content_hash = row.content_hash
        registered_path = Path(row.registered_path)
        push_status = row.push_status
    assert push_status == PushStatus.pushed.value
    entry = _cache_path(tmp_project, content_hash)
    remote_object = remote / "files" / "md5" / content_hash[:2] / content_hash[2:]
    assert remote_object.exists()

    口 = Step(step_num=3, name="Remove the local cache entry",
             purpose="The entry is now only on the remote")
    _force_unlink(entry)

    口 = Step(step_num=4, name="Restore the sample",
             purpose="The restore pulls the entry into the cache, then copies "
                     "it to the recorded path")
    restore_sample("pull_sample", project_root=tmp_project)
    assert entry.exists()
    assert registered_path.read_bytes() == payload

    口 = Step(step_num=5, name="Restore with the entry in neither place",
             purpose="With the local entry, the remote object and the restored "
                     "copy gone, the restore exits 1")
    _force_unlink(entry)
    _force_unlink(remote_object)
    _force_unlink(registered_path)
    with pytest.raises(SystemExit) as excinfo:
        restore_sample("pull_sample", project_root=tmp_project)
    assert excinfo.value.code == 1


# =============================================================================
# Tier 3: DAG advance is genuinely async w.r.t. push
# =============================================================================

# run_pipeline's wfc_root is unused (kept for its frozen signature); the
# framework root is passed so the call matches the CLI's shape.
_WFC_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.integration
@requires_docker
@workflow(
    purpose=(
        "async push lets DAG advance before step 1's push completes"
    ),
    inputs="2-step linear pipeline + slow-stub wfc.storage.transport.push (~2s sleep)",
    outputs="step 2 started before step 1's RunOutput.pushed_at; all rows reach 'pushed' after drain",
)
def test_async_push_does_not_block_dag_advance(
    pipeline_factory, register_fixture_methods, monkeypatch
):
    """Verifies the timing claim: DAG advances as soon as bytes are in
    the local cache, not after the remote push completes.

    Shape: build a 2-step linear pipeline whose remote-push is slowed by a
    ~2s sleep stub.  After the run, query the Run rows ordered by
    started_at: step 2 must have started BEFORE step 1's RunOutput
    pushed_at timestamp.  After the finalize drain, every RunOutput row
    must be in the 'pushed' terminal state (drain works).
    """
    project_dir = register_fixture_methods

    _ = Step(step_num=1, name="Configure a local-FS DVC remote",
             purpose="Initialize .dvc/ + wire a remote pointing at a tmp dir")
    remote_dir = project_dir.parent / f"{project_dir.name}-remote-storage"
    remote_dir.mkdir()
    from dvc.repo import Repo
    # register_fixture_methods runs init_project(), which auto-initializes
    # .dvc/. Re-running Repo.init would raise InitError ('.dvc' exists), so only
    # init when the fixture hasn't already done so.
    if not (project_dir / ".dvc").exists():
        Repo.init(str(project_dir), no_scm=True)
    cfg = project_dir / ".dvc" / "config"
    parser = configparser.ConfigParser()
    parser.read(cfg)
    if not parser.has_section("core"):
        parser.add_section("core")
    parser.set("core", "remote", "default")
    if not parser.has_section('remote "default"'):
        parser.add_section('remote "default"')
    parser.set('remote "default"', "url", str(remote_dir))
    with open(cfg, "w") as f:
        parser.write(f)
    # .dvc/config is tracked, and a run refuses a dirty tree; commit the
    # remote the way wfc init leaves the project clean.
    subprocess.run(["git", "add", ".dvc/config"], cwd=project_dir,
                   check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "configure DVC remote"],
                   cwd=project_dir, check=True, capture_output=True)

    _ = Step(step_num=2, name="Build 2-step linear pipeline",
             purpose="input_selector -> transform_1 -> transform_2 over sample_a")
    from tests.fixtures.conftest import create_sample_csv as _mk
    _mk(project_dir, "sample_a", num_rows=3)
    pipeline_path = pipeline_factory(
        name="async_push_timing",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["sample_a"]},
            {"id": "transform_1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_one"}},
            {"id": "transform_2", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_two"}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_1"},
            {"source": "transform_1", "target": "transform_2"},
        ],
        samples=[],
    )

    _ = Step(step_num=3, name="Slow-stub wfc.storage.transport.push (~2s sleep)",
             purpose="Force the worker tick to spend real wall-clock time per push batch")
    import wfc.storage.transport as _wfc_transport

    class _FakeResult:
        succeeded: list = []
        failed: list = []

    def _slow_push(hashes, project_dir, *args, **kwargs):
        time.sleep(2.0)
        return _FakeResult()

    stub_transport(monkeypatch, push=_slow_push)

    _ = Step(step_num=4, name="Run pipeline (timing-capture wrapper)",
             purpose="Run two transforms and let the worker drain before run_pipeline returns")
    from wfc.execution import run_pipeline
    # archive=True so the deferred archive pass populates content_hash on each
    # RunOutput; without it the push worker filters every row out (content_hash
    # IS NOT NULL guard).
    run_pipeline(
        pipeline_path=str(pipeline_path),
        project_root=str(project_dir),
        wfc_root=str(_WFC_ROOT),
        cores=1,
        archive=True,
    )

    _ = Step(step_num=5, name="Assert step 2 started before step 1's push completed",
             purpose="DAG advance must not wait for remote.push -- the core claim")
    from wfc.persistence import get_session, Run, RunOutput
    with get_session() as session:
        runs = session.exec(
            select(Run).order_by(Run.started_at)  # type: ignore[arg-type]
        ).all()
        # Filter out system-node "runs" (input_selector); fixture method
        # runs have non-null method_id pointing at 'transform'.
        method_runs = [r for r in runs if r.method_id is not None]
        assert len(method_runs) >= 2, (
            f"expected 2 transform runs, got {len(method_runs)}: {[r.id for r in runs]}"
        )
        s1, s2 = method_runs[0], method_runs[1]
        s1_outputs = session.exec(
            select(RunOutput).where(RunOutput.run_id == s1.id)
        ).all()
        assert s1_outputs, f"step 1 (run {s1.id}) has no RunOutput rows"
        s1_pushed_at = s1_outputs[0].pushed_at
        all_outputs = session.exec(select(RunOutput)).all()

    assert s1.started_at is not None and s2.started_at is not None, (
        "Run.started_at not populated"
    )
    assert s1_pushed_at is not None, (
        f"step 1's RunOutput.pushed_at is null -- push never drained "
        f"(push_status={s1_outputs[0].push_status})"
    )
    assert s2.started_at < s1_pushed_at, (
        f"step 2 started at {s2.started_at} but step 1's push finished at "
        f"{s1_pushed_at} -- DAG advance must not block on the push draining"
    )

    _ = Step(step_num=6, name="Assert finalize-drain pushed every RunOutput",
             purpose="No row left in pending/in_flight after run_pipeline returns")
    statuses = [r.push_status for r in all_outputs]
    assert all(s == "pushed" for s in statuses), (
        f"expected all RunOutput.push_status == 'pushed' after drain, got {statuses}"
    )
