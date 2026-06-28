"""Tests for ADR-018 push-status schema + push worker (Tasks 1, 7).

Schema tests verify the four push columns on RunOutput/Sample and the
PushStatus enum.  Worker tests use a fake ``wfc.remote.push`` to drive
the tick function deterministically without spinning real network calls.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from axiom_annotations import workflow, task, Step
from sqlmodel import select

from wfc.database import get_session
from wfc.models import PushStatus, Run, RunOutput, Sample


def test_push_status_enum_values():
    """PushStatus has exactly the five lifecycle states (ADR-018)."""
    assert PushStatus.pending.value == "pending"
    assert PushStatus.in_flight.value == "in_flight"
    assert PushStatus.pushed.value == "pushed"
    assert PushStatus.failed.value == "failed"
    assert PushStatus.deferred.value == "deferred"


def test_run_output_has_push_columns():
    """RunOutput carries the four ADR-018 push-tracking columns."""
    cols = {c.name for c in RunOutput.__table__.columns}
    assert "push_status" in cols
    assert "pushed_at" in cols
    assert "push_attempts" in cols
    assert "push_error" in cols


def test_sample_has_push_columns():
    """Sample carries the four ADR-018 push-tracking columns."""
    cols = {c.name for c in Sample.__table__.columns}
    assert "push_status" in cols
    assert "pushed_at" in cols
    assert "push_attempts" in cols
    assert "push_error" in cols


def test_run_output_default_push_status_deferred():
    """RunOutput.push_status defaults to ``deferred`` (no-remote terminal).

    The orchestrator flips this to ``pending`` at insert time when a
    remote is configured; rows created in standalone / no-remote runs
    stay in the deferred terminal forever.
    """
    ro = RunOutput(run_id=1, artifact_type="method_file")
    assert ro.push_status == PushStatus.deferred.value
    assert ro.push_attempts == 0
    assert ro.pushed_at is None
    assert ro.push_error is None


def test_sample_default_push_status_deferred():
    """Sample.push_status defaults to ``deferred`` (same contract as RunOutput)."""
    s = Sample(
        name="t",
        source_path="x",
        registered_path="y",
        file_type="csv",
    )
    assert s.push_status == PushStatus.deferred.value
    assert s.push_attempts == 0
    assert s.pushed_at is None


# =============================================================================
# Task 7 worker tests (Tier 2)
# =============================================================================


class _FakeResult:
    """Minimal TransferResult stand-in with .failed list."""
    def __init__(self, failed=None):
        self.succeeded = []
        self.failed = failed or []


class _FailedObj:
    def __init__(self, value):
        self.value = value


@pytest.fixture
def _make_run(tmp_project):
    """Create a Run row so RunOutput.run_id FK is satisfied."""
    from wfc.models import Module, Method, Run

    def _make(finished_at=None):
        with get_session() as session:
            module = Module(name="mod", path="mod.py")
            session.add(module)
            session.commit()
            session.refresh(module)
            method = Method(name="m1", module_id=module.id, env="container:demo")
            session.add(method)
            session.commit()
            session.refresh(method)
            run = Run(
                method_id=method.id,
                sample="s",
                pipeline_id="p1",
                status="completed",
                finished_at=finished_at or datetime.now(timezone.utc),
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            return run.id

    return _make


@workflow(purpose="Push worker tick promotes pending rows to pushed on success")
def test_push_worker_tick_promotes_pending(tmp_project, _make_run, monkeypatch):
    """Single tick with a successful fake push -> rows go to ``pushed``."""
    _ = Step(step_num=1, name="Insert pending RunOutput",
             purpose="Seed a pending push row")
    run_id = _make_run()
    with get_session() as session:
        ro = RunOutput(
            run_id=run_id,
            artifact_type="method_file",
            content_hash="a" * 32,
            push_status=PushStatus.pending.value,
        )
        session.add(ro)
        session.commit()

    _ = Step(step_num=2, name="Mock remote.push to succeed",
             purpose="Fake the DVC API with a no-failures TransferResult")
    monkeypatch.setattr("wfc.cli.remote_push" if False else "wfc.remote.push",
                        lambda hashes, pd: _FakeResult(failed=[]))

    _ = Step(step_num=3, name="Tick the worker",
             purpose="Run a single tick synchronously")
    from wfc.cli import _push_worker_tick
    pushed, remaining = _push_worker_tick(tmp_project)

    assert pushed >= 1
    assert remaining == 0
    with get_session() as session:
        rows = session.exec(select(RunOutput)).all()
    assert rows[0].push_status == PushStatus.pushed.value
    assert rows[0].pushed_at is not None


@workflow(purpose="Push worker tick increments push_attempts on failure")
def test_push_worker_tick_retries_on_failure(tmp_project, _make_run, monkeypatch):
    """Failed push -> push_attempts++, push_error set, status=failed."""
    _ = Step(step_num=1, name="Seed pending row", purpose="One row to push")
    run_id = _make_run()
    with get_session() as session:
        ro = RunOutput(
            run_id=run_id,
            artifact_type="method_file",
            content_hash="b" * 32,
            push_status=PushStatus.pending.value,
        )
        session.add(ro)
        session.commit()

    _ = Step(step_num=2, name="Mock remote.push to raise",
             purpose="Simulate a transient DVC error")
    def _boom(hashes, pd):
        raise RuntimeError("network down")
    monkeypatch.setattr("wfc.remote.push", _boom)

    _ = Step(step_num=3, name="Tick the worker",
             purpose="Single failed tick")
    from wfc.cli import _push_worker_tick
    pushed, remaining = _push_worker_tick(tmp_project)

    assert pushed == 0
    assert remaining == 1
    with get_session() as session:
        rows = session.exec(select(RunOutput)).all()
    assert rows[0].push_status == PushStatus.failed.value
    assert rows[0].push_attempts == 1
    assert "network down" in (rows[0].push_error or "")


@workflow(purpose="register_sample standalone routes synchronous push")
def test_register_sample_standalone_pushes_directly(tmp_project, monkeypatch):
    """When WFC_PIPELINE_ID is unset, register_sample pushes synchronously."""
    _ = Step(step_num=1, name="Configure DVC + .dvc/config",
             purpose="Make has_remote_configured return True")
    # Write wf-canvas.toml with [dvc] url
    cfg = tmp_project / ".wfc" / "wf-canvas.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    db = (tmp_project / ".wfc" / "wfc.db").as_posix()
    cfg.write_text(
        f'[database]\nurl = "sqlite:///{db}"\n[project]\nname = "t"\n'
        f'[pixi]\nroot = ".pixi"\n[dvc]\nurl = "{(tmp_project / "remote").as_posix()}"\n'
    )
    from wfc.provenance import init_dvc
    init_dvc(tmp_project, {"url": str(tmp_project / "remote")})

    _ = Step(step_num=2, name="Stub wfc.remote.push to record + succeed",
             purpose="Capture the synchronous call")
    calls = []
    def _record(hashes, pd):
        calls.append(list(hashes))
        return _FakeResult(failed=[])
    monkeypatch.setattr("wfc.remote.push", _record)
    # Ensure WFC_PIPELINE_ID is unset
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)

    _ = Step(step_num=3, name="Register a sample",
             purpose="Synchronous path -> push_status pushed")
    src = tmp_project / "src.csv"
    src.write_text("a,b\n1,2\n")
    from wfc.cli import register_sample
    register_sample(name="s1", source_path=src, project_root=tmp_project)

    assert len(calls) == 1, "standalone path should call remote.push exactly once"
    with get_session() as session:
        s = session.exec(select(Sample)).first()
    assert s.push_status == PushStatus.pushed.value
    assert s.pushed_at is not None


@workflow(purpose="register_sample in-pipeline enqueues onto worker")
def test_register_sample_in_pipeline_enqueues(tmp_project, monkeypatch):
    """When WFC_PIPELINE_ID is set, register_sample marks pending, no sync push."""
    _ = Step(step_num=1, name="Configure DVC", purpose="DVC ready")
    cfg = tmp_project / ".wfc" / "wf-canvas.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    db = (tmp_project / ".wfc" / "wfc.db").as_posix()
    cfg.write_text(
        f'[database]\nurl = "sqlite:///{db}"\n[project]\nname = "t"\n'
        f'[pixi]\nroot = ".pixi"\n[dvc]\nurl = "{(tmp_project / "remote").as_posix()}"\n'
    )
    from wfc.provenance import init_dvc
    init_dvc(tmp_project, {"url": str(tmp_project / "remote")})

    _ = Step(step_num=2, name="Set WFC_PIPELINE_ID",
             purpose="Activate the in-pipeline branch")
    monkeypatch.setenv("WFC_PIPELINE_ID", "fake-pipe")
    calls = []
    monkeypatch.setattr("wfc.remote.push", lambda h, pd: calls.append(list(h)))

    _ = Step(step_num=3, name="Register the sample",
             purpose="Should NOT call remote.push synchronously")
    src = tmp_project / "src.csv"
    src.write_text("a,b\n1,2\n")
    from wfc.cli import register_sample
    register_sample(name="s1", source_path=src, project_root=tmp_project)

    assert calls == [], "in-pipeline path must not call remote.push synchronously"
    with get_session() as session:
        s = session.exec(select(Sample)).first()
    assert s.push_status == PushStatus.pending.value
    assert s.pushed_at is None


def test_prune_dvc_cache_skips_unpushed_when_remote_configured(tmp_project, _make_run):
    """ADR-018 Task 7: prune skips cache entries whose row has pushed_at IS NULL."""
    # Configure .dvc/config so has_remote_configured returns True.
    (tmp_project / ".dvc").mkdir(parents=True, exist_ok=True)
    (tmp_project / ".dvc" / "config").write_text(
        '[core]\nremote = default\n[remote "default"]\nurl = /tmp/x\n'
    )
    # Create a cache entry + a RunOutput row with pushed_at=None.
    cache_dir = tmp_project / ".dvc" / "cache" / "files" / "md5"
    h = "c" * 32
    entry = cache_dir / h[:2] / h[2:]
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("data")
    run_id = _make_run()
    with get_session() as session:
        ro = RunOutput(
            run_id=run_id, artifact_type="method_file",
            content_hash=h, push_status=PushStatus.pending.value, pushed_at=None,
        )
        session.add(ro)
        session.commit()

    from wfc.provenance import prune_dvc_cache
    deleted = prune_dvc_cache(tmp_project, all_entries=True, dry_run=False)
    assert entry.exists(), "entry referencing unpushed row must be preserved"
    assert entry not in deleted

    # --force overrides the guard.
    deleted2 = prune_dvc_cache(tmp_project, all_entries=True, dry_run=False, force=True)
    assert not entry.exists()
