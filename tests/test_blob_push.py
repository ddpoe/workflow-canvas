"""The blob table's pushes: the push worker's third source beside outputs and samples.

A blob row is entered by Storage when it stores bytes no output or sample row
owns (an env's content blob, a pipeline document). These witnesses run the
push worker's own tasks against the real local remote ``wfc init``
configures (``tests.fixtures.routes.project_archive_dir``); no transport is
faked.

Declared fixture deviations:

- ``_drop_cache_object`` removes an entry's local object by hand after a
  production store, the real-means failure a push meets when the bytes are
  gone (as in ``test_push_per_entry``);
- ``_age_blob`` dates a blob row 30 days back, because a test cannot wait
  out the orphan reset's window.
"""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axiom_annotations import workflow
from sqlmodel import select

from tests.fixtures.conftest import fixture_env_blob
from tests.fixtures.fakes import stub_docker_image_content, stub_docker_image_inspect
from tests.fixtures.routes import project_archive_dir
from wfc import layout
from wfc.persistence import Blob, PushStatus, get_session
from wfc.storage import KIND_ENV_FINGERPRINT, store_env_content
from wfc.storage.push_worker import (
    PUSH_MAX_ATTEMPTS,
    _mark_in_flight,
    _push_entries,
    _push_worker_tick,
    _record_push_outcomes,
    _reset_orphan_pushes,
    _snapshot_push_rows,
)


def _remote_has(project: Path, md5: str) -> bool:
    return (project_archive_dir(project) / "files" / "md5" / md5[:2] / md5[2:]).is_file()


def _drop_cache_object(project: Path, md5: str) -> None:
    """Declared deviation: remove an entry's local object by hand."""
    entry = layout.dvc_cache_entry(project, md5)
    os.chmod(entry, stat.S_IWRITE)
    entry.unlink()


def _age_blob(content_hash: str, days: int) -> None:
    """Declared deviation: date a blob row back past the orphan-reset window."""
    with get_session() as session:
        row = session.get(Blob, content_hash)
        row.stored_at = datetime.now(UTC) - timedelta(days=days)
        session.add(row)
        session.commit()


def _blob(content_hash: str) -> Blob:
    with get_session() as session:
        row = session.get(Blob, content_hash)
        assert row is not None, content_hash
        session.expunge(row)
        return row


def _register_local_byo(project: Path, monkeypatch, name: str):
    """Register a byo env on a local image through production registration."""
    from wfc import environments as envs_mod

    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, "sha256:" + "d" * 64)
    return envs_mod.register(name=name, backend="byo",
                             source={"image": "docker://local/blob-env:latest"},
                             project_dir=project)


@workflow(purpose="Registering an env enters its content blob as an "
                  "env-fingerprint row, pending with a remote configured; one "
                  "worker tick pushes it to the local remote and records it "
                  "pushed with a time; registering the same content again "
                  "leaves the pushed row as it is")
def test_worker_pushes_a_registered_envs_blob_row(tmp_project, monkeypatch):
    fixture_md5 = fixture_env_blob(tmp_project)
    record = _register_local_byo(tmp_project, monkeypatch, "blob-env")
    md5 = record.env_fingerprint
    assert md5 != fixture_md5

    row = _blob(md5)
    assert row.kind == KIND_ENV_FINGERPRINT
    assert row.push_status == PushStatus.pending.value
    assert row.size and row.stored_at is not None
    assert not _remote_has(tmp_project, md5)

    # One tick pushes both registered envs' rows: this one and the
    # project's fixture env's.
    assert _push_worker_tick(tmp_project) == (2, 0)

    row = _blob(md5)
    assert row.push_status == PushStatus.pushed.value
    assert row.pushed_at is not None and row.push_error is None
    assert _remote_has(tmp_project, md5)
    assert _blob(fixture_md5).push_status == PushStatus.pushed.value

    # The same image content under another name: insert if absent, so the
    # one row stays pushed and the next tick has nothing to do.
    again = _register_local_byo(tmp_project, monkeypatch, "blob-env-copy")
    assert again.env_fingerprint == md5
    assert _blob(md5).push_status == PushStatus.pushed.value
    assert _push_worker_tick(tmp_project) == (0, 0)
    with get_session() as session:
        assert {b.hash for b in session.exec(select(Blob)).all()} == {md5, fixture_md5}


@workflow(purpose="Launching a pipeline freezes its document and keeps the "
                  "frozen file's canonical JSON under its hash as a "
                  "pipeline-document row, pending with a remote configured; "
                  "one worker tick pushes it to the local remote and records "
                  "it pushed with a time")
def test_worker_pushes_a_launched_pipelines_document_row(tmp_project):
    import json

    from wfc.execution.lifecycle import prepare_launch
    from wfc.identity import canonical_json_bytes, canonical_json_hash
    from wfc.storage import KIND_PIPELINE_DOCUMENT

    source = tmp_project / "doc.json"
    document = {"name": "blob-doc", "nodes": [], "links": [], "samples": ["s"]}
    source.write_text(json.dumps(document), encoding="utf-8")
    launch = prepare_launch(tmp_project, source, document, document, "blob-doc-pid")

    # The hash is of the frozen file the launch wrote, as claims read it.
    frozen = json.loads(launch.frozen_doc.read_text(encoding="utf-8"))
    digest = launch.pipeline_doc_hash
    assert digest == canonical_json_hash(frozen)
    assert (layout.dvc_cache_entry(tmp_project, digest).read_bytes()
            == canonical_json_bytes(frozen))
    row = _blob(digest)
    assert (row.kind, row.push_status) == (KIND_PIPELINE_DOCUMENT, PushStatus.pending.value)
    assert not _remote_has(tmp_project, digest)

    # The tick pushes the document's row and the project's fixture env's.
    assert _push_worker_tick(tmp_project) == (2, 0)
    row = _blob(digest)
    assert row.push_status == PushStatus.pushed.value and row.pushed_at is not None
    assert _remote_has(tmp_project, digest)
    assert _remote_has(tmp_project, fixture_env_blob(tmp_project))


@workflow(purpose="Two push workers on one database over the same blob row: "
                  "only the first claims it in flight, the second's tick finds "
                  "nothing, and the second's stale snapshot can neither set the "
                  "pushed row back in flight nor record it failed")
def test_two_workers_never_overwrite_each_others_blob_status(tmp_project):
    md5 = store_env_content("two workers\n", tmp_project, kind=KIND_ENV_FINGERPRINT)
    # The project's fixture env's row is pending beside it, claimed and
    # pushed by the same worker.
    pending = {md5: KIND_ENV_FINGERPRINT,
               fixture_env_blob(tmp_project): KIND_ENV_FINGERPRINT}

    # Both workers snapshot before either claims.
    _, _, blobs_a = _snapshot_push_rows()
    _, _, blobs_b = _snapshot_push_rows()
    assert blobs_a == blobs_b == pending

    # Worker A claims the rows; worker B's claim of the same snapshot gets nothing.
    claimed_a = _mark_in_flight({}, {}, blobs_a)
    claimed_b = _mark_in_flight({}, {}, blobs_b)
    assert claimed_a == pending
    assert claimed_b == {}
    assert _blob(md5).push_status == PushStatus.in_flight.value

    # A whole tick by worker B while A's push is in flight leaves it alone.
    assert _push_worker_tick(tmp_project) == (0, 0)
    assert _blob(md5).push_status == PushStatus.in_flight.value

    # Worker A pushes and records.
    errors = _push_entries(set(claimed_a), set(), tmp_project, claimed_a)
    assert errors == {h: None for h in pending}
    assert _record_push_outcomes({}, {}, errors, claimed_a) == len(pending)
    pushed = _blob(md5)
    assert pushed.push_status == PushStatus.pushed.value

    # Worker B, still holding its stale snapshot, records a failure and
    # claims again: neither applies to a row that is no longer in flight
    # or awaiting a push.
    assert _record_push_outcomes({}, {}, {md5: "remote went away"}, blobs_b) == 0
    assert _mark_in_flight({}, {}, blobs_b) == {}
    after = _blob(md5)
    assert after.push_status == PushStatus.pushed.value
    assert after.push_attempts == 0 and after.push_error is None
    assert after.pushed_at == pushed.pushed_at
    assert _remote_has(tmp_project, md5)


@workflow(purpose="Before a pipeline starts, the orphan reset re-arms a recent "
                  "blob row whose pushes were exhausted (pending, attempts "
                  "back to zero) and leaves an old one exhausted; the next tick "
                  "pushes the re-armed row only")
def test_orphan_reset_rearms_a_recent_failed_blob_not_an_old_one(tmp_project):
    recent = store_env_content("recent blob\n", tmp_project, kind=KIND_ENV_FINGERPRINT)
    old = store_env_content("old blob\n", tmp_project, kind=KIND_ENV_FINGERPRINT)

    # Both lose their bytes, and every attempt the worker allows fails.
    _drop_cache_object(tmp_project, recent)
    _drop_cache_object(tmp_project, old)
    # The first tick also pushes the project's fixture env's row, whose
    # bytes are intact; no later tick has a row to push.
    for attempt in range(PUSH_MAX_ATTEMPTS):
        assert _push_worker_tick(tmp_project) == (1 if attempt == 0 else 0, 2)
    assert _blob(fixture_env_blob(tmp_project)).push_status == PushStatus.pushed.value
    for md5 in (recent, old):
        row = _blob(md5)
        assert (row.push_status, row.push_attempts) == (
            PushStatus.failed.value, PUSH_MAX_ATTEMPTS)
        assert md5 in row.push_error
    assert _push_worker_tick(tmp_project) == (0, 0)  # exhausted: not retried

    _age_blob(old, days=30)
    assert _reset_orphan_pushes(tmp_project) == 1

    rearmed, left = _blob(recent), _blob(old)
    assert (rearmed.push_status, rearmed.push_attempts) == (PushStatus.pending.value, 0)
    assert (left.push_status, left.push_attempts) == (
        PushStatus.failed.value, PUSH_MAX_ATTEMPTS)

    # The recent bytes are stored again (insert if absent keeps the row);
    # the next tick pushes the re-armed row and does not touch the old one.
    assert store_env_content("recent blob\n", tmp_project, kind=KIND_ENV_FINGERPRINT) == recent
    assert _push_worker_tick(tmp_project) == (1, 0)
    assert _blob(recent).push_status == PushStatus.pushed.value
    assert _remote_has(tmp_project, recent)
    assert _blob(old).push_attempts == PUSH_MAX_ATTEMPTS
    assert not _remote_has(tmp_project, old)
