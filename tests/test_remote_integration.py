"""ADR-018 Task 6: wfc.remote adapter + ensure_dvc_ready rewrite.

Tier 2: pure-logic tests over has_remote_configured + ensure_dvc_ready.
Tier 3 (CI-load-bearing): real DVC DataCloud round-trip via a local-FS
remote — exercises the actual dvc.repo.Repo + dvc_data.hashfile.hash_info
import surface so we catch upstream API drift early.
"""

import configparser
import textwrap
import time
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from tests.conftest import requires_docker


# =============================================================================
# Tier 2: has_remote_configured
# =============================================================================

def test_has_remote_configured_no_config_returns_false(tmp_path):
    """No .dvc/config -> False."""
    from wfc.remote import has_remote_configured
    assert has_remote_configured(tmp_path) is False


def test_has_remote_configured_with_remote_returns_true(tmp_path):
    """A .dvc/config with [remote "name"] section -> True."""
    (tmp_path / ".dvc").mkdir()
    (tmp_path / ".dvc" / "config").write_text(
        "[core]\nremote = default\n"
        '[remote "default"]\nurl = /tmp/foo\n'
    )
    from wfc.remote import has_remote_configured
    assert has_remote_configured(tmp_path) is True


def test_has_remote_configured_empty_config_returns_false(tmp_path):
    """A .dvc/config without any remote section -> False."""
    (tmp_path / ".dvc").mkdir()
    (tmp_path / ".dvc" / "config").write_text("[core]\nautostage = true\n")
    from wfc.remote import has_remote_configured
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
    """ADR-018: remote_type='local' gate is gone; any URL scheme is accepted."""
    _write_wf(tmp_path, url)
    _write_dvc_cfg(tmp_path, url)
    from wfc.provenance import ensure_dvc_ready
    result = ensure_dvc_ready(tmp_path)
    assert result["url"] == url


# =============================================================================
# Tier 3 (CI-load-bearing): real DVC DataCloud round-trip
# =============================================================================

@workflow(
    purpose=(
        "ADR-018 US-3: real DVC DataCloud push+pull round-trip via local-FS remote"
    ),
    inputs="known bytes -> cache_file -> remote_push",
    outputs="pull retrieves byte-identical content from remote",
)
def test_remote_push_pull_round_trip(tmp_path):
    """Pushes a known file through wfc.remote.push then pulls it back.

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
    from wfc.provenance import cache_file, hash_path
    payload = project / "payload.txt"
    payload.write_bytes(b"hello-adr018")
    h = hash_path(payload)
    cache_file(payload, h, project, move=False)

    _ = Step(step_num=3, name="Push to remote",
             purpose="wfc.remote.push uploads via DataCloud.push")
    from wfc.remote import push as remote_push, pull as remote_pull
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
    assert restored.read_bytes() == b"hello-adr018"


# =============================================================================
# Tier 3 (US-1 acceptance): DAG advance is genuinely async w.r.t. push
# =============================================================================

# WFC_ROOT lets run_pipeline locate the wfc package for the Snakemake subprocess.
_WFC_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.integration
@requires_docker
@workflow(
    purpose=(
        "ADR-018 US-1: async push lets DAG advance before step 1's push completes"
    ),
    inputs="2-step linear pipeline + slow-stub wfc.remote.push (~2s sleep)",
    outputs="step 2 started before step 1's RunOutput.pushed_at; all rows reach 'pushed' after drain",
)
def test_async_push_does_not_block_dag_advance(
    pipeline_factory, register_fixture_methods, monkeypatch
):
    """Verifies the US-1 timing claim: DAG advances as soon as bytes are in
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
    remote_dir = project_dir / "remote_storage"
    remote_dir.mkdir()
    from dvc.repo import Repo
    # register_fixture_methods runs init_project(), which now auto-initializes
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

    _ = Step(step_num=3, name="Slow-stub wfc.remote.push (~2s sleep)",
             purpose="Force the worker tick to spend real wall-clock time per push batch")
    import wfc.remote as _wfc_remote

    class _FakeResult:
        succeeded: list = []
        failed: list = []

    def _slow_push(hashes, project_dir, *args, **kwargs):
        time.sleep(2.0)
        return _FakeResult()

    monkeypatch.setattr(_wfc_remote, "push", _slow_push)

    _ = Step(step_num=4, name="Run pipeline (timing-capture wrapper)",
             purpose="Run two transforms and let the worker drain before run_pipeline returns")
    from wfc.cli import run_pipeline
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
             purpose="DAG advance must not wait for remote.push -- the core US-1 claim")
    from wfc.database import get_session
    from wfc.models import Run, RunOutput
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
        f"{s1_pushed_at} -- DAG advance waited on push (US-1 regression)"
    )

    _ = Step(step_num=6, name="Assert finalize-drain pushed every RunOutput",
             purpose="No row left in pending/in_flight after run_pipeline returns")
    statuses = [r.push_status for r in all_outputs]
    assert all(s == "pushed" for s in statuses), (
        f"expected all RunOutput.push_status == 'pushed' after drain, got {statuses}"
    )
