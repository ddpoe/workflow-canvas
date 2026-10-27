"""Storage's checked entry read: a sample, a pipeline document or an env blob by hash.

``wfc.storage.read_entry`` serves the entries no output row owns, the way the
output reader serves an output: a pushed entry pruned from the local cache is
pulled back, and an entry that is gone and was never pushed is refused,
naming its kind, its hash and the repair. ``entry_state`` is its probe for a
plan, which pulls nothing.

The run, its pipeline document, its sample and its env blob come from
``tests.fixtures.routes.completed_run``; the push and the prune are
``wfc cache push`` and ``wfc cache prune``'s own bodies.
"""

from __future__ import annotations

import os
import stat

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.routes import completed_run
from wfc import layout
from wfc.identity import hash_path
from wfc.persistence import Blob, PushStatus, RunInput, get_session
from wfc.storage import (
    KIND_ENV_FINGERPRINT,
    KIND_PIPELINE_DOCUMENT,
    KIND_SAMPLE,
    OUTPUT_LOCAL,
    OUTPUT_REMOTE,
    NotInCacheError,
    cache_prune,
    cache_push,
    entry_state,
    read_entry,
)


def _sample_read(run_id: int) -> tuple[str, str]:
    """The ``(name, hash)`` of the sample a run read, from its input row."""
    with get_session() as session:
        row = session.exec(select(RunInput).where(RunInput.run_id == run_id,
                                                  RunInput.sample_name.is_not(None))).one()
    assert row.content_hash is not None
    return row.sample_name, row.content_hash


@workflow(purpose="A pipeline document, a sample and an env blob that were pushed and "
                  "then pruned from the local cache are pulled back by Storage's "
                  "entry read with their bytes intact; an entry that is gone and was "
                  "never pushed is refused, naming its kind, hash and repair",
          inputs="One completed run on a project with a local DVC remote; wfc cache "
                 "push and wfc cache prune; a second sample registered after the push",
          outputs="Each pruned entry's path, hashing to its recorded hash; the "
                  "probe's remote then local answer; the named refusal")
def test_pushed_and_pruned_entries_are_pulled_back_and_an_unpushed_gone_one_is_refused(
    tmp_project, monkeypatch,
):
    """The entry read: pushed and pruned entries come back; an unpushed gone one is refused."""
    口 = Step(step_num=1, name="Run, push and prune",
             purpose="Every entry the run named reaches the remote, then leaves the "
                     "local cache")
    run = completed_run(tmp_project, monkeypatch=monkeypatch)
    doc_hash = run.run_row["pipeline_doc_hash"]
    env_hash = run.run_row["env_fingerprint"]
    sample, sample_hash = _sample_read(run.run_id)
    report = cache_push(tmp_project)
    assert not report.failures, report.failures
    assert cache_prune(include_local=True, force=True) == 0
    entries = [(doc_hash, KIND_PIPELINE_DOCUMENT, None), (env_hash, KIND_ENV_FINGERPRINT, None),
               (sample_hash, KIND_SAMPLE, sample)]
    for content_hash, _kind, _name in entries:
        assert not layout.dvc_cache_entry(tmp_project, content_hash).exists(), content_hash

    口 = Step(step_num=2, name="Probe, then read each pruned entry",
             purpose="The probe pulls nothing and says the read will pull; the read "
                     "pulls the entry back and serves its bytes")
    for content_hash, kind, name in entries:
        assert entry_state(content_hash, kind, name=name) == OUTPUT_REMOTE
        path = read_entry(content_hash, kind, name=name)
        assert hash_path(path) == content_hash
        assert entry_state(content_hash, kind, name=name) == OUTPUT_LOCAL

    口 = Step(step_num=3, name="An entry gone and never pushed",
             purpose="A second pipeline's document, stored after the push, awaits "
                     "its push; its cache object is then deleted (declared "
                     "deviation: no wfc verb drops an unpushed entry, which the "
                     "prune keeps)")
    late = completed_run(tmp_project, monkeypatch=monkeypatch, method="late",
                         pipeline_id="late-pipeline", name="late")
    late_hash = late.run_row["pipeline_doc_hash"]
    with get_session() as session:
        assert session.get(Blob, late_hash).push_status != PushStatus.pushed.value
    obj = layout.dvc_cache_entry(tmp_project, late_hash)
    os.chmod(obj, stat.S_IWRITE | stat.S_IREAD)
    obj.unlink()

    for probe in (entry_state, read_entry):
        with pytest.raises(NotInCacheError) as refused:
            probe(late_hash, KIND_PIPELINE_DOCUMENT)
        message = str(refused.value)
        assert "pipeline document" in message and late_hash in message
        assert "never pushed" in message and "Launch the pipeline again" in message
