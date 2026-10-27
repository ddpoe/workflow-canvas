"""``wfc cache prune --include-local`` keeps every entry a record names until it is pushed.

The records are run output and sample rows, blob rows, every run's and method
version's env fingerprint, every run's pipeline document hash, and the env
manifest's hashes the CLI hands in. An entry one of them names is kept unless
it is recorded pushed; nothing unpushed is removed, with or without a remote,
with or without ``--force``. Entries no record names, and named entries that
are recorded pushed, are removed.

Declared fixture deviations:

- ``_set_status`` writes a generated push status onto real rows. The states
  are the property's input; a test cannot make the remote accept or refuse
  one entry on demand.
- ``_without_remote`` removes ``.dvc/config``, the state of a project whose
  archive was never wired (as ``test_provenance`` does).
- ``_forget_blob_row`` deletes a registered env's blob row: the state an env
  registered by an earlier release is in (its blob cached, no row, and no
  backfill). Deleting a run's pipeline document row is a defensive state:
  no production writer reaches it, since the claim always enters that row.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from axiom_annotations import workflow
from sqlalchemy import update
from sqlmodel import select

from tests.fixtures.fakes import (
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_interactive_prompt,
)
from tests.fixtures.routes import completed_run, create_sample_csv
from tests.harness.scenario import Behavior
from wfc import layout
from wfc.persistence import Blob, PushStatus, RunOutput, Sample, get_session
from wfc.storage import (
    KIND_ENV_FINGERPRINT,
    KIND_PIPELINE_DOCUMENT,
    archive_outputs,
    cache_prune,
    prune_dvc_cache,
    store_env_content,
)

_STATES = [s.value for s in (PushStatus.pending, PushStatus.deferred, PushStatus.failed,
                             PushStatus.in_flight, PushStatus.pushed)]
_MODELS = {"output": RunOutput, "sample": Sample}


def _set_status(source: str, content_hash: str, status: str) -> None:
    """Declared deviation: give every row of one record a generated push status."""
    with get_session() as session:
        if source == "blob":
            session.execute(update(Blob).where(Blob.hash == content_hash)
                            .values(push_status=status))
        else:
            model = _MODELS[source]
            session.execute(update(model).where(model.content_hash == content_hash)
                            .values(push_status=status))
        session.commit()


def _without_remote(project: Path) -> None:
    """Declared deviation: the project has no remote configured."""
    (project / ".dvc" / "config").unlink()


def _forget_blob_row(content_hash: str) -> None:
    """Declared deviation: a cached env-fingerprint or pipeline-document blob with no row."""
    with get_session() as session:
        session.delete(session.get(Blob, content_hash))
        session.commit()


def _local(project: Path, content_hash: str) -> bool:
    return layout.dvc_cache_entry(project, content_hash).exists()


def _records(project: Path, monkeypatch) -> tuple[list[tuple[str, str]], str, str]:
    """Build one record of each kind through production, plus two bare entries.

    Returns:
        ``(records, old_env, unnamed)``: each record as ``(source, hash)``;
        an env blob only the manifest names (no blob row); an entry nothing
        names.
    """
    run = completed_run(
        project, monkeypatch=monkeypatch, method="m1", module="mod", sample="s",
        outputs={"a": ".txt", "b": ".txt"},
        output_files={"a": "a.txt", "b": "b.txt"},
        behavior=Behavior(outputs={"a": "alpha\n", "b": "beta\n"}),
    )
    archive_outputs(project, run_id=run.run_id)
    with get_session() as session:
        outputs = sorted(session.exec(
            select(RunOutput.content_hash).where(RunOutput.run_id == run.run_id)).all())
    create_sample_csv(project, "s_two", num_rows=2)
    create_sample_csv(project, "s_five", num_rows=5)
    with get_session() as session:
        samples = sorted(session.exec(
            select(Sample.content_hash).where(Sample.name.in_(["s_two", "s_five"]))).all())
    env_blob = store_env_content("env content\n", project, kind=KIND_ENV_FINGERPRINT)
    doc_blob = store_env_content('{"nodes":[]}', project, kind=KIND_PIPELINE_DOCUMENT)
    old_env = store_env_content("an earlier release's env\n", project)
    unnamed = store_env_content("nothing names this\n", project)
    records = ([("output", h) for h in outputs] + [("sample", h) for h in samples]
               + [("blob", env_blob), ("blob", doc_blob)])
    for _, h in records + [("", old_env), ("", unnamed)]:
        assert _local(project, h), h
    return records, old_env, unnamed


def _generate(rng: random.Random, records) -> dict[str, str]:
    states = {h: rng.choice(_STATES) for _, h in records}
    for (source, h) in records:
        _set_status(source, h, states[h])
    return states


@pytest.mark.parametrize("remote,force", [(True, False), (True, True),
                                          (False, False), (False, True)])
@workflow(purpose="Over generated push states of output, sample and blob records "
                  "plus an env blob only the manifest names: the prune's selection "
                  "never holds an entry a record names that is not recorded "
                  "pushed, and holds every unnamed and every named-pushed one; "
                  "the real prune, with or without a remote and --force, removes "
                  "exactly that (with no remote and no --force it refuses); the "
                  "kept-unpushed line names wfc cache push only when a remote "
                  "is configured, and otherwise says a remote is needed first")
def test_prune_never_removes_an_unpushed_entry(tmp_project, monkeypatch, capsys,
                                               remote, force):
    records, old_env, unnamed = _records(tmp_project, monkeypatch)
    rng = random.Random(f"{remote}-{force}")

    for _ in range(20):
        states = _generate(rng, records)
        selected = {p.parent.name + p.name
                    for p in prune_dvc_cache(tmp_project, dry_run=True,
                                             env_hashes={old_env})}
        for _, h in records:
            assert (h in selected) == (states[h] == PushStatus.pushed.value), (h, states[h])
        assert old_env not in selected
        assert unnamed in selected

    states = _generate(rng, records)
    states[records[0][1]] = PushStatus.pushed.value  # at least one of each fate
    _set_status(*records[0], PushStatus.pushed.value)
    states[records[1][1]] = PushStatus.pending.value
    _set_status(*records[1], PushStatus.pending.value)
    if not remote:
        _without_remote(tmp_project)
    if not force:
        stub_interactive_prompt(monkeypatch, "y")
    capsys.readouterr()

    rc = cache_prune(include_local=True, force=force, env_hashes={old_env})

    out = capsys.readouterr().out
    if remote:
        assert "unpushed cache entr" in out and "run `wfc cache push`" in out, out
    elif force:
        assert "unpushed cache entr" in out, out
        assert "a remote is needed first" in out, out
        assert "wfc cache push" not in out, out
    if not remote and not force:
        assert rc == 1  # the unreachable-remote guard refuses; nothing removed
        assert all(_local(tmp_project, h) for _, h in records)
        assert _local(tmp_project, unnamed)
        return
    assert rc == 0
    for _, h in records:
        assert _local(tmp_project, h) == (states[h] != PushStatus.pushed.value), (h, states[h])
    assert _local(tmp_project, old_env)
    assert not _local(tmp_project, unnamed)


@workflow(purpose="A run's env fingerprint, its method version's env fingerprint "
                  "and its pipeline document hash, each with no blob row and not "
                  "among the manifest hashes handed in: the run and method-version "
                  "records alone keep them through a forced prune --include-local, "
                  "while an entry nothing names goes")
def test_prune_keeps_hashes_only_run_and_version_records_name(tmp_project, monkeypatch):
    from wfc.persistence import MethodVersion, Run

    run = completed_run(tmp_project, monkeypatch=monkeypatch, method="m1",
                        module="mod", sample="s")
    with get_session() as session:
        row = session.get(Run, run.run_id)
        version = session.get(MethodVersion, row.version_id)
        named = {row.env_fingerprint, version.env_fingerprint, row.pipeline_doc_hash}
    assert None not in named
    for h in named:
        assert _local(tmp_project, h), h
        _forget_blob_row(h)
    unnamed = store_env_content("nothing names this\n", tmp_project)

    rc = cache_prune(include_local=True, force=True, env_hashes=())

    assert rc == 0
    for h in named:
        assert _local(tmp_project, h), f"{h} was pruned though a record names it"
    assert not _local(tmp_project, unnamed)


@workflow(purpose="wfc cache prune --include-local with an env registered and no "
                  "run naming its fingerprint: the env manifest's hashes reach the "
                  "prune, so the env blob is kept while an unnamed entry goes, and "
                  "the message counts the unpushed entries kept and names "
                  "wfc cache push")
def test_cli_prune_keeps_the_env_manifests_blobs(cli, tmp_project, monkeypatch):
    from wfc import environments as envs_mod

    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, "sha256:" + "e" * 64)
    record = envs_mod.register(name="kept-env", backend="byo",
                               source={"image": "docker://local/kept-env:latest"},
                               project_dir=tmp_project)
    env_blob = record.env_fingerprint
    _forget_blob_row(env_blob)
    unnamed = store_env_content("nothing names this\n", tmp_project)

    r = cli("cache", "prune", "--include-local", "--force")

    assert r.returncode == 0, r.stderr
    assert _local(tmp_project, env_blob), "the manifest's env blob was pruned"
    assert not _local(tmp_project, unnamed)
    # Every local hash the manifest names (this env's, the fixture env's),
    # plus any unpushed blob row registration entered.
    manifest = envs_mod.load_manifest(tmp_project)
    named = {rec.get(k) for rec in manifest["envs"].values()
             for k in ("env_fingerprint", "source_fingerprint")}
    with get_session() as session:
        named |= {b.hash for b in session.exec(select(Blob)).all()
                  if b.push_status != PushStatus.pushed.value}
    kept = len({h for h in named if h and _local(tmp_project, h)})
    assert kept >= 1
    assert f"Kept {kept} unpushed cache entr" in r.stdout, r.stdout
    assert "wfc cache push" in r.stdout
