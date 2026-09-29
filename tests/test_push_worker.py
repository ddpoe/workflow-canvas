"""Tests for the push-status schema + push worker.

Schema tests verify the four push columns on RunOutput/Sample and the
PushStatus enum.  Worker tests use a fake ``wfc.storage.transport.push`` to drive
the tick function deterministically without spinning real network calls.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from axiom_annotations import workflow, task, Step
from sqlmodel import select

from tests.fixtures.fakes import stub_transport
from tests.fixtures.routes import completed_run
from wfc.persistence import get_session, PushStatus, Run, RunOutput, Sample
from wfc.storage.cache import _cache_path


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
# worker tests (Tier 2)
# =============================================================================


class _FakeResult:
    """Minimal TransferResult stand-in with .failed list."""
    def __init__(self, failed=None):
        self.succeeded = []
        self.failed = failed or []


class _FailedObj:
    def __init__(self, value):
        self.value = value


def _archived_pending_output(tmp_project, monkeypatch) -> tuple[int, str]:
    """A completed run through the route, its one output archived, then pending.

    The five phases record the run and its output row; ``archive_outputs``
    gives the row a real content_hash and cache blob. The pending flip
    itself happens in run_step's record phase only when a remote is
    configured at run time (``wfc/execution/record.py``); it is set by hand
    here so a default-suite tick has a real, archived candidate to promote
    -- the one shortcut at these sites.

    Returns:
        ``(run_id, content_hash)`` of the archived output.
    """
    from wfc.storage import archive_outputs

    run = completed_run(tmp_project, monkeypatch=monkeypatch, method="m1",
                        module="mod", sample="s", pipeline_id="p1",
                        outputs={"output": ".parquet"})
    archive_outputs(tmp_project, run_id=run.run_id)
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == run.run_id)
        ).one()
        assert ro.content_hash is not None and len(ro.content_hash) == 32
        ro.push_status = PushStatus.pending.value
        session.add(ro)
        session.commit()
        return run.run_id, ro.content_hash


@workflow(purpose="Push worker tick promotes pending rows to pushed on success")
def test_push_worker_tick_promotes_pending(tmp_project, monkeypatch):
    """Single tick with a successful fake push -> rows go to ``pushed``."""
    _ = Step(step_num=1, name="Seed a pending push row via the production path",
             purpose="The route's five phases record the run and its output; "
                     "archive_outputs gives it a real content_hash + cache "
                     "blob before the pending flip")
    _archived_pending_output(tmp_project, monkeypatch)

    _ = Step(step_num=2, name="Mock remote.push to succeed",
             purpose="Fake the DVC API with a no-failures TransferResult")
    stub_transport(monkeypatch, push=lambda hashes, pd, repairs=None: _FakeResult(failed=[]))

    _ = Step(step_num=3, name="Tick the worker",
             purpose="Run a single tick synchronously")
    from wfc.storage.push_worker import _push_worker_tick
    pushed, remaining = _push_worker_tick(tmp_project)

    assert pushed >= 1
    assert remaining == 0
    with get_session() as session:
        rows = session.exec(select(RunOutput)).all()
    assert rows[0].push_status == PushStatus.pushed.value
    assert rows[0].pushed_at is not None


@workflow(purpose="The push worker's orphan reset returns the last week's "
                  "pending, in-flight and failed output and sample rows to "
                  "pending with zero attempts; older rows and every deferred "
                  "or pushed row are untouched")
def test_reset_orphan_pushes_requeues_recent_stuck_rows(tmp_project):
    """A worker that died mid-push leaves rows only the reset re-queues."""
    from datetime import timedelta
    from wfc.persistence import Method, Module

    now = datetime.now(timezone.utc)
    recent, old = now - timedelta(days=1), now - timedelta(days=30)
    stuck = [PushStatus.pending, PushStatus.in_flight, PushStatus.failed]
    settled = [PushStatus.deferred, PushStatus.pushed]

    _ = Step(step_num=1, name="Seed output and sample rows in every push state",
             purpose="Recent rows in every state, old rows in the stuck "
                     "states; outputs dated by their run's finish, samples "
                     "by their registration")
    with get_session() as session:
        module = Module(name="orphan_mod", path="mod.py")
        session.add(module)
        session.commit()
        session.refresh(module)
        method = Method(name="m1", module_id=module.id, env="container:demo")
        session.add(method)
        session.commit()
        session.refresh(method)
        run_ids = {}
        for age, finished_at in (("recent", recent), ("old", old)):
            run = Run(method_id=method.id, sample="s", pipeline_id="p1",
                      status="completed", finished_at=finished_at)
            session.add(run)
            session.commit()
            session.refresh(run)
            run_ids[age] = run.id
        seeded = [("recent", s) for s in stuck + settled] + [("old", s) for s in stuck]
        for age, status in seeded:
            label = f"{age}-{status.value}"
            session.add(RunOutput(
                run_id=run_ids[age], output_name=label,
                artifact_path=str(tmp_project / label),
                artifact_type="method_file",
                push_status=status.value, push_attempts=3,
            ))
            session.add(Sample(
                name=label, source_path=str(tmp_project / label),
                registered_path=str(tmp_project / label), file_type="csv",
                registration_mode="copy",
                push_status=status.value, push_attempts=3,
                registered_at=recent if age == "recent" else old,
            ))
        session.commit()

    _ = Step(step_num=2, name="Run the orphan reset",
             purpose="What the push worker's start does before its first tick")
    from wfc.storage.push_worker import _reset_orphan_pushes
    n = _reset_orphan_pushes(tmp_project)

    _ = Step(step_num=3, name="Only the recent stuck rows are re-queued",
             purpose="Recent stuck rows are pending with zero attempts; every "
                     "other row keeps its state and attempts")
    requeued = {f"recent-{s.value}" for s in stuck}
    with get_session() as session:
        outputs = [(r.output_name, r.push_status, r.push_attempts)
                   for r in session.exec(select(RunOutput)).all()]
        samples = [(r.name, r.push_status, r.push_attempts)
                   for r in session.exec(select(Sample)).all()]
    assert n == 2 * len(requeued)
    assert len(outputs) == len(samples) == len(seeded)
    for label, status, attempts in outputs + samples:
        if label in requeued:
            assert (status, attempts) == (PushStatus.pending.value, 0), label
        else:
            assert (status, attempts) == (label.split("-", 1)[1], 3), label


@workflow(purpose="Push worker tick increments push_attempts on failure")
def test_push_worker_tick_retries_on_failure(tmp_project, monkeypatch):
    """Failed push -> push_attempts++, push_error set, status=failed."""
    _ = Step(step_num=1, name="Seed pending row", purpose="One row to push")
    _archived_pending_output(tmp_project, monkeypatch)

    _ = Step(step_num=2, name="Mock remote.push to raise",
             purpose="Simulate a transient DVC error")
    def _boom(hashes, pd, repairs=None):
        raise RuntimeError("network down")
    stub_transport(monkeypatch, push=_boom)

    _ = Step(step_num=3, name="Tick the worker",
             purpose="Single failed tick")
    from wfc.storage.push_worker import _push_worker_tick
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
        f'[pixi]\nroot = ".pixi"\n[dvc]\nurl = "{(tmp_project.parent / f"{tmp_project.name}-remote").as_posix()}"\n'
    )
    from wfc.storage import init_dvc
    init_dvc(tmp_project, {"url": str(tmp_project.parent / f"{tmp_project.name}-remote")})

    _ = Step(step_num=2, name="Stub wfc.storage.transport.push to record + succeed",
             purpose="Capture the synchronous call")
    calls = []
    def _record(hashes, pd, repairs=None):
        calls.append(list(hashes))
        return _FakeResult(failed=[])
    stub_transport(monkeypatch, push=_record)
    # Ensure WFC_PIPELINE_ID is unset
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)

    _ = Step(step_num=3, name="Register a sample",
             purpose="Synchronous path -> push_status pushed")
    src = tmp_project / "src.csv"
    src.write_text("a,b\n1,2\n")
    from wfc.registration import register_sample
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
        f'[pixi]\nroot = ".pixi"\n[dvc]\nurl = "{(tmp_project.parent / f"{tmp_project.name}-remote").as_posix()}"\n'
    )
    from wfc.storage import init_dvc
    init_dvc(tmp_project, {"url": str(tmp_project.parent / f"{tmp_project.name}-remote")})

    _ = Step(step_num=2, name="Set WFC_PIPELINE_ID",
             purpose="Activate the in-pipeline branch")
    monkeypatch.setenv("WFC_PIPELINE_ID", "fake-pipe")
    calls = []
    stub_transport(monkeypatch, push=lambda h, pd, repairs=None: calls.append(list(h)))

    _ = Step(step_num=3, name="Register the sample",
             purpose="Should NOT call remote.push synchronously")
    src = tmp_project / "src.csv"
    src.write_text("a,b\n1,2\n")
    from wfc.registration import register_sample
    register_sample(name="s1", source_path=src, project_root=tmp_project)

    assert calls == [], "in-pipeline path must not call remote.push synchronously"
    with get_session() as session:
        s = session.exec(select(Sample)).first()
    assert s.push_status == PushStatus.pending.value
    assert s.pushed_at is None


def test_prune_dvc_cache_skips_unpushed_when_remote_configured(tmp_project, monkeypatch):
    """prune skips cache entries whose row has pushed_at IS NULL."""
    # A real cache entry through the archive pass (real md5 + read-only
    # cache blob) whose RunOutput row is pending with pushed_at=None.
    _, h = _archived_pending_output(tmp_project, monkeypatch)
    entry = _cache_path(tmp_project, h)
    assert entry.exists()
    # Configure .dvc/config so has_remote_configured returns True.
    (tmp_project / ".dvc").mkdir(parents=True, exist_ok=True)
    (tmp_project / ".dvc" / "config").write_text(
        '[core]\nremote = default\n[remote "default"]\nurl = /tmp/x\n'
    )

    from wfc.storage import prune_dvc_cache
    deleted = prune_dvc_cache(tmp_project, all_entries=True, dry_run=False)
    assert entry.exists(), "entry referencing unpushed row must be preserved"
    assert entry not in deleted

    # --force overrides the guard.
    deleted2 = prune_dvc_cache(tmp_project, all_entries=True, dry_run=False, force=True)
    assert not entry.exists()
