"""``wfc cache push``: push every output, sample and blob awaiting a push, now.

The verb pushes to the real local remote ``wfc init`` configures
(``tests.fixtures.routes.project_archive_dir``); no transport is faked.

Declared fixture deviations:

- ``_set_push`` writes a push status and attempt count onto real rows: the
  pending, failed, exhausted and deferred-before-a-remote states a project
  reaches over time, which a test cannot wait for.
- ``_drop_cache_object`` removes one entry's local object by hand, the
  real-means failure a push meets when the bytes are gone (as in
  ``test_blob_push``).
- ``_remote_off`` / ``_remote_on`` move ``.dvc/config`` aside and back: a
  project with no remote configured.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from axiom_annotations import Step, workflow
from sqlalchemy import update
from sqlmodel import select

from tests.fixtures.routes import completed_run, create_sample_csv, project_archive_dir
from tests.harness.scenario import Behavior
from wfc import layout
from wfc.persistence import Blob, PushStatus, RunOutput, Sample, get_session
from wfc.storage import (
    KIND_ENV_FINGERPRINT,
    KIND_PIPELINE_DOCUMENT,
    archive_outputs,
    store_env_content,
)
from wfc.storage.push_worker import PUSH_MAX_ATTEMPTS

_MODELS = {"output": RunOutput, "sample": Sample, "blob": Blob}


def _set_push(source: str, content_hash: str, status: str, attempts: int = 0) -> None:
    """Declared deviation: set one record's push status and attempt count."""
    model = _MODELS[source]
    column = model.hash if model is Blob else model.content_hash
    with get_session() as session:
        session.execute(update(model).where(column == content_hash)
                        .values(push_status=status, push_attempts=attempts))
        session.commit()


def _statuses() -> dict[tuple[str, str], tuple[str, int]]:
    """Every record's (status, attempts), keyed by (source, hash)."""
    out = {}
    with get_session() as session:
        for source, model in _MODELS.items():
            for row in session.exec(select(model)).all():
                h = row.hash if model is Blob else row.content_hash
                if h:
                    out[(source, h)] = (row.push_status, row.push_attempts)
    return out


def _drop_cache_object(project: Path, md5: str) -> None:
    """Declared deviation: remove an entry's local object by hand."""
    entry = layout.dvc_cache_entry(project, md5)
    os.chmod(entry, stat.S_IWRITE)
    entry.unlink()


def _remote_off(project: Path) -> None:
    """Declared deviation: no remote configured."""
    (project / ".dvc" / "config").rename(project / ".dvc" / "config.off")


def _remote_on(project: Path) -> None:
    (project / ".dvc" / "config.off").rename(project / ".dvc" / "config")


def _remote_has(project: Path, md5: str) -> bool:
    return (project_archive_dir(project) / "files" / "md5" / md5[:2] / md5[2:]).is_file()


@workflow(purpose="wfc cache push over pending, failed, exhausted and "
                  "deferred-before-a-remote outputs, samples and blobs, one of "
                  "them made to fail: with no remote it refuses and changes "
                  "nothing; with one it pushes every entry but the failing one, "
                  "reports that one with its kind and reason, and exits 1")
def test_cache_push_pushes_every_waiting_entry_and_reports_failures(
    cli, tmp_project, monkeypatch,
):
    _ = Step(step_num=1, name="Records in every waiting state",
             purpose="Two outputs, two samples and three blobs through "
                     "production; one blob's local bytes are lost")
    run = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="m1", module="mod", sample="s",
        outputs={"a": ".txt", "b": ".txt"},
        output_files={"a": "a.txt", "b": "b.txt"},
        behavior=Behavior(outputs={"a": "push alpha\n", "b": "push beta\n"}),
    )
    archive_outputs(tmp_project, run_id=run.run_id)
    with get_session() as session:
        out_a, out_b = sorted(session.exec(
            select(RunOutput.content_hash).where(RunOutput.run_id == run.run_id)).all())
    create_sample_csv(tmp_project, "push_two", num_rows=2)
    create_sample_csv(tmp_project, "push_four", num_rows=4)
    with get_session() as session:
        smp_a, smp_b = sorted(session.exec(
            select(Sample.content_hash)
            .where(Sample.name.in_(["push_two", "push_four"]))).all())
    blob_env = store_env_content("push env\n", tmp_project, kind=KIND_ENV_FINGERPRINT)
    blob_doc = store_env_content('{"push": 1}', tmp_project, kind=KIND_PIPELINE_DOCUMENT)
    blob_lost = store_env_content("lost env\n", tmp_project, kind=KIND_ENV_FINGERPRINT)
    _drop_cache_object(tmp_project, blob_lost)

    _set_push("output", out_a, PushStatus.pending.value)
    _set_push("output", out_b, PushStatus.deferred.value)
    _set_push("sample", smp_a, PushStatus.failed.value, PUSH_MAX_ATTEMPTS)  # exhausted
    _set_push("sample", smp_b, PushStatus.deferred.value)
    _set_push("blob", blob_env, PushStatus.deferred.value)
    _set_push("blob", blob_doc, PushStatus.failed.value, 2)
    _set_push("blob", blob_lost, PushStatus.pending.value)

    _ = Step(step_num=2, name="No remote: refused, nothing changed",
             purpose="The verb needs a remote and says so before touching a row")
    before = _statuses()
    _remote_off(tmp_project)
    refused = cli("cache", "push")
    _remote_on(tmp_project)
    assert refused.returncode != 0
    assert "remote" in (refused.stderr + refused.stdout).lower()
    assert _statuses() == before

    _ = Step(step_num=3, name="Push with the remote",
             purpose="Deferred rows go pending, exhausted rows are re-armed, "
                     "ticks run until nothing pushable is left")
    r = cli("cache", "push")

    assert r.returncode == 1, r.stdout + r.stderr
    after = _statuses()
    pushed = [("output", out_a), ("output", out_b), ("sample", smp_a),
              ("sample", smp_b), ("blob", blob_env), ("blob", blob_doc)]
    for key in pushed:
        assert after[key][0] == PushStatus.pushed.value, (key, after[key])
        assert _remote_has(tmp_project, key[1]), key
    assert after[("blob", blob_lost)][0] == PushStatus.failed.value
    assert not _remote_has(tmp_project, blob_lost)

    assert "2 output(s), 2 sample(s)" in r.stdout, r.stdout
    assert "1 failed" in r.stdout, r.stdout
    (failure_line,) = [ln for ln in r.stdout.splitlines() if blob_lost in ln]
    assert KIND_ENV_FINGERPRINT in failure_line
    with get_session() as session:
        reason = session.get(Blob, blob_lost).push_error
    assert reason and reason.splitlines()[0] in failure_line
