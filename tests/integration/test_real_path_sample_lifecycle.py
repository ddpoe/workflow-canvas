"""A sample's whole life, driven the way a user drives it.

Every stage here goes through the CLI a researcher actually types --
``wfc init``, ``wfc register-sample``, ``wfc restore-sample``,
``wfc run-pipeline``. Nothing hand-writes a row, nothing stages a file
under ``data/samples/``, and no ``.dvc/config`` is wired by hand. The point
is a real path with no shortcuts: a user never inserts a sample row.

Three tests plus one pinned expectation:

- :func:`test_register_push_delete_restore` -- the storage round trip. No
  Docker, no method runs. Register from outside the project, prove the
  bytes reached the local cache *and* the archive, delete the local cache
  entry and prove the restore pulls them back, then delete the entry, the
  archive object and the restored copy and prove the refusal fires.
  A directory sample then makes the same round trip in stages 5 and 6:
  registered, its local entry deleted, restored as an identical tree.
- :func:`test_a_directory_sample_reaches_the_archive` -- the remote half of
  directory registration: the directory's content hash is addressed in the
  archive.
- :func:`test_pipeline_restores_a_sample_it_never_staged` -- the pipeline
  round trip, with Docker. A pipeline materializes a sample file no
  fixture ever wrote, and does it again after the local cache entry is
  gone.
- :func:`test_a_pipeline_refuses_an_unreachable_sample_before_any_run_row`
  -- the refusal. No container runs, because the engine is never
  started; Docker is still needed to build the fixture methods' image.
  It is the previous test's step 5 with one field changed: delete the
  cache entry on a *pushed* row and the pipeline runs (the cold start);
  delete it on a row whose push never landed and the pipeline refuses
  before any run row exists.

**Overlap with** ``tests/test_remote_integration.py::test_restore_sample_pulls_a_local_miss``
**is deliberate.** That test covers the **Python API** route
(``register_sample`` / ``restore_sample`` called directly) over a
``.dvc/config`` written by hand. This one covers the **CLI** route over a
remote ``wfc init --archive`` configured. They are two paths that can
drift apart -- an argument-parsing change, a project-root resolution
change or an exit-code change breaks one and not the other -- so they get
two witnesses rather than one contorted to cover both.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from tests.conftest import requires_docker
from tests.fixtures.conftest import (
    create_sample_csv,
    project_archive_dir,
    run_cli,
    sample_source_dir,
    stub_readiness_probes,
)


# run_pipeline's wfc_root is unused (kept for its frozen signature); the
# framework root is passed so the invocation matches the CLI's shape.
_WFC_ROOT = Path(__file__).resolve().parent.parent.parent


def launched_pipeline_ids(project: Path) -> set[str]:
    """The pipeline ids ``prepare_launch`` has minted a run directory for.

    ``prepare_launch`` is what writes a pipeline's run directory, its log
    directories and its frozen document -- everything a run leaves behind
    before the engine is handed anything. Run *rows* come later, from
    ``run-step`` inside the engine, so this is what a "nothing was
    launched" assertion has to read.

    Args:
        project: The project root.

    Returns:
        The directory names under ``.runs/pipelines/``.
    """
    pipelines = project / ".runs" / "pipelines"
    if not pipelines.exists():
        return set()
    return {p.name for p in pipelines.iterdir() if p.is_dir()}


def force_unlink(path: Path) -> None:
    """Delete a file that may be marked read-only.

    DVC protects cache entries by clearing the write bit, and a workspace
    copy restored from one inherits it (``copy2`` preserves mode). On
    Windows the read-only attribute blocks ``unlink`` outright.

    Args:
        path: The file to remove.
    """
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.unlink()


def cache_entry(project: Path, content_hash: str) -> Path:
    """Return a content hash's cache entry, spelled out rather than resolved.

    The literal ``.dvc/cache/files/md5/<h[:2]>/<h[2:]>`` layout is part of
    what these tests assert: a helper that drifted with the code would
    take the assertion with it.

    Args:
        project: The project root.
        content_hash: The 32-char md5.

    Returns:
        The cache entry path. It may not exist.
    """
    return (project / ".dvc" / "cache" / "files" / "md5"
            / content_hash[:2] / content_hash[2:])


def archive_object(archive: Path, content_hash: str) -> Path:
    """Return a content hash's object in a local-filesystem DVC archive.

    Args:
        archive: The archive (DVC remote) root.
        content_hash: The 32-char md5.

    Returns:
        The archive object path. It may not exist.
    """
    return archive / "files" / "md5" / content_hash[:2] / content_hash[2:]


def sample_row(name: str):
    """Read one ``samples`` row back out of the project database.

    Args:
        name: The sample name.

    Returns:
        A detached ``Sample`` instance, or ``None`` when the name has no
        row.
    """
    from wfc.persistence import Sample, get_session

    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == name)).first()
        if row is not None:
            session.expunge(row)
        return row


@pytest.fixture
def uninitialised_project(git_project, monkeypatch):
    """A git repo with the wfc environment pinned but no project in it yet.

    ``tmp_project`` would have run ``init_project`` already; these tests
    run ``wfc init`` themselves because the init route is part of what
    they cover.

    Yields:
        The project root, not yet initialised.
    """
    from wfc.persistence import reset_engine

    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL",
                       f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    # A standalone registration pushes synchronously and lands
    # push_status=pushed; inside a pipeline it would enqueue onto the push
    # worker and land pending. The user typing `wfc register-sample` is in
    # the first case.
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
    # init's closing health summary shells out to `docker info` with a
    # 15-second timeout. This test needs no daemon.
    stub_readiness_probes(monkeypatch)
    reset_engine()
    yield git_project
    reset_engine()


def init_through_the_cli(project: Path) -> Path:
    """Run ``wfc init --archive <sibling> --yes`` and return the archive.

    The archive is named explicitly and sits beside the project: left to
    itself ``init_project`` resolves ``~/.wfc/archives/<project>`` and
    ``init_dvc`` pre-creates it, so an unattended test run would leave a
    directory in the developer's home.

    Args:
        project: The project root to initialise.

    Returns:
        The archive directory.
    """
    from wfc.persistence import reset_engine

    archive = project_archive_dir(project)
    result = run_cli("init", "--dir", str(project),
                     "--archive", str(archive), "--yes")
    assert result.returncode == 0, (
        f"wfc init exited {result.returncode}: {result.stderr}"
    )
    reset_engine()
    return archive


@workflow(
    purpose=(
        "a user registers a sample through the CLI and its bytes reach the "
        "local cache and the archive; with the local entry gone the restore "
        "pulls them back, and with the archive object gone too it refuses"
    ),
    inputs="wfc init --archive, then wfc register-sample over a file the "
           "user keeps outside the project",
    outputs="a pushed row, a real-copy restore, and exit 1 when the content "
            "is nowhere wfc can reach",
)
def test_register_push_delete_restore(uninitialised_project):
    """``wfc init`` -> ``register-sample`` -> ``restore-sample``, no shortcuts."""
    from wfc.identity import hash_directory, hash_file
    from wfc.persistence import PushStatus

    project = uninitialised_project

    口 = Step(step_num=1, name="Initialise the project through the CLI",
             purpose="wfc init --archive is the route a user takes to a "
                     "configured remote; nothing here writes .dvc/config by "
                     "hand")
    archive = init_through_the_cli(project)
    dvc_config = (project / ".dvc" / "config").read_text()
    assert '[remote "default"]' in dvc_config, (
        f"wfc init left no remote named 'default' in .dvc/config:\n{dvc_config}"
    )
    assert archive.is_dir(), f"wfc init did not create the archive at {archive}"

    口 = Step(step_num=2, name="Register a file the user keeps outside the project",
             purpose="Registration caches the source's bytes, pushes them to "
                     "the archive and records where a restore will later "
                     "materialize them -- it copies nothing into data/samples/",
             critical="data/samples/<name>/ must still be empty afterwards. "
                      "A fixture that finds the file there is reading "
                      "something registration did not write")
    payload = b"id,value\n1,42\n2,43\n"
    source = sample_source_dir(project) / "file_sample.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)

    result = run_cli("register-sample", "--name", "file_sample",
                     "--source", str(source))
    assert result.returncode == 0, (
        f"wfc register-sample exited {result.returncode}: {result.stderr}"
    )

    row = sample_row("file_sample")
    assert row is not None, "register-sample wrote no samples row"
    assert row.content_hash == hash_file(source), (
        f"row content_hash {row.content_hash} does not describe the source "
        f"({hash_file(source)})"
    )
    assert source.read_bytes() == payload, (
        "registration moved or rewrote the user's source file; copy mode "
        "must leave it exactly where the user put it"
    )
    entry = cache_entry(project, row.content_hash)
    assert entry.exists(), f"registration cached nothing at {entry}"
    remote_object = archive_object(archive, row.content_hash)
    assert remote_object.exists(), (
        f"registration pushed nothing to the archive at {remote_object}"
    )
    assert row.push_status == PushStatus.pushed.value, (
        f"a standalone registration with a remote configured must settle "
        f"push_status=pushed, got {row.push_status!r} "
        f"(push_error={row.push_error!r})"
    )
    registered_path = project / row.registered_path
    workspace_dir = registered_path.parent
    assert not workspace_dir.exists() or not list(workspace_dir.iterdir()), (
        f"registration materialized {list(workspace_dir.iterdir())} under "
        f"{workspace_dir}; data/samples/ is written by restore_sample alone"
    )

    口 = Step(step_num=3, name="Delete the local cache entry, then restore",
             purpose="With the entry gone and the archive object still there, "
                     "the restore has to pull it back before it can copy it "
                     "to the recorded path -- the one deletion that forces a "
                     "real remote round trip")
    force_unlink(entry)
    result = run_cli("restore-sample", "--name", "file_sample")
    assert result.returncode == 0, (
        f"wfc restore-sample exited {result.returncode}: {result.stderr}"
    )
    assert entry.exists(), (
        f"the restore did not repopulate the cache entry at {entry}"
    )
    assert registered_path.read_bytes() == payload, (
        f"the restore did not put the source's bytes at {registered_path}"
    )

    口 = Step(step_num=4, name="Delete the entry, the archive object and the copy",
             purpose="With the content in neither the cache nor the archive "
                     "and no workspace copy to fall back on, the restore has "
                     "nothing to reach for and must say so by name")
    force_unlink(entry)
    force_unlink(remote_object)
    force_unlink(registered_path)
    result = run_cli("restore-sample", "--name", "file_sample")
    assert result.returncode == 1, (
        f"wfc restore-sample exited {result.returncode} over content that is "
        f"nowhere; it must exit 1. stderr: {result.stderr}"
    )
    assert "file_sample" in result.stderr, (
        f"the refusal does not name the sample:\n{result.stderr}"
    )
    assert row.content_hash in result.stderr, (
        f"the refusal does not name the content hash:\n{result.stderr}"
    )

    口 = Step(step_num=5, name="Register a directory sample",
             purpose="A directory registers like a file: one row, one "
                     "content-addressed cache entry, which for a directory is "
                     "a .dir manifest beside one object per member file",
             critical="The archive half is claimed on its own in "
                      "test_a_directory_sample_reaches_the_archive")
    dir_source = sample_source_dir(project) / "dir_sample"
    (dir_source / "nested").mkdir(parents=True, exist_ok=True)
    members = {"a.csv": b"id,value\n1,a\n", "b.csv": b"id,value\n2,b\n",
               "nested/c.csv": b"id,value\n3,c\n"}
    for rel, data in members.items():
        (dir_source / rel).write_bytes(data)

    result = run_cli("register-sample", "--name", "dir_sample",
                     "--source", str(dir_source))
    assert result.returncode == 0, (
        f"wfc register-sample exited {result.returncode} over a directory: "
        f"{result.stderr}"
    )
    dir_row = sample_row("dir_sample")
    assert dir_row is not None, "register-sample wrote no row for a directory"
    assert dir_row.content_hash == hash_directory(dir_source), (
        f"row content_hash {dir_row.content_hash} does not describe the "
        f"directory ({hash_directory(dir_source)})"
    )
    dir_entry = cache_entry(project, dir_row.content_hash)
    assert dir_entry.is_file(), (
        f"a directory sample's cache entry at {dir_entry} should be its "
        f".dir manifest file"
    )
    member_entries = [cache_entry(project, hash_file(dir_source / rel))
                      for rel in members]
    assert all(e.is_file() for e in member_entries), (
        f"a member file has no object of its own in the cache: "
        f"{[str(e) for e in member_entries if not e.is_file()]}"
    )
    assert dir_row.push_status == PushStatus.pushed.value, (
        f"the directory sample did not reach the archive "
        f"(push_error={dir_row.push_error!r})"
    )

    口 = Step(step_num=6, name="Delete the directory's local entry, then restore",
             purpose="The manifest and every member object leave the local "
                     "cache; the restore pulls them back from the archive and "
                     "materializes a tree identical to the source",
             critical="A restored tree that differs from the source by one "
                      "file, one byte or one extra entry fails here")
    for entry_path in [dir_entry, *member_entries]:
        force_unlink(entry_path)
    result = run_cli("restore-sample", "--name", "dir_sample")
    assert result.returncode == 0, (
        f"wfc restore-sample exited {result.returncode} over a directory: "
        f"{result.stderr}"
    )
    restored = project / dir_row.registered_path
    restored_tree = {p.relative_to(restored).as_posix(): p.read_bytes()
                     for p in restored.rglob("*") if p.is_file()}
    assert restored_tree == members, (
        f"the restored tree at {restored} is not the source tree: "
        f"{sorted(restored_tree)} vs {sorted(members)}"
    )
    assert hash_directory(restored) == dir_row.content_hash


@workflow(
    purpose=(
        "a directory sample's bytes reach the archive, so a second machine "
        "can restore it"
    ),
    inputs="wfc register-sample over a directory, with a remote configured",
    outputs="the directory's content hash addressed in the archive",
)
def test_a_directory_sample_reaches_the_archive(uninitialised_project):
    """The remote half of directory registration.

    Deliberately narrow: the only claim that can fail here is the archive
    object's existence. The local half and the restore are asserted in
    :func:`test_register_push_delete_restore`.
    """
    project = uninitialised_project

    口 = Step(step_num=1, name="Initialise and register a directory sample",
             purpose="The setup is not the claim; it is only here so the "
                     "claim has something to be about")
    archive = init_through_the_cli(project)
    dir_source = sample_source_dir(project) / "dir_sample"
    dir_source.mkdir(parents=True, exist_ok=True)
    (dir_source / "a.csv").write_bytes(b"id,value\n1,a\n")
    (dir_source / "b.csv").write_bytes(b"id,value\n2,b\n")
    result = run_cli("register-sample", "--name", "dir_sample",
                     "--source", str(dir_source))
    assert result.returncode == 0, result.stderr
    row = sample_row("dir_sample")
    assert row is not None

    口 = Step(step_num=2, name="The archive holds the directory's content",
             purpose="Without this a directory sample exists on exactly one "
                     "machine and the project is not reproducible anywhere "
                     "else -- the whole reason registration pushes")
    remote_object = archive_object(archive, row.content_hash)
    assert remote_object.exists(), (
        f"a directory sample's content is not in the archive at "
        f"{remote_object} (push_status={row.push_status!r}, "
        f"push_error={row.push_error!r})"
    )


@pytest.mark.integration
@requires_docker
@workflow(
    purpose=(
        "a pipeline materializes a sample file no fixture ever staged, and "
        "does it again after the local cache entry is gone"
    ),
    inputs="a sample registered from outside data/, and a pipeline whose "
           "root step reads it",
    outputs="data/samples/<sample>/ populated by the run's restore rule, "
            "twice -- the second time from the archive",
)
def test_pipeline_restores_a_sample_it_never_staged(
    pipeline_factory, register_fixture_methods
):
    """The restore rule is the only thing that can have written this file.

    A pipeline test that stages its sample at its own ``registered_path``
    makes every restore take ``restore_from_cache``'s dest-already-valid
    skip, so the rule that materializes a sample is never exercised. Here
    ``data/samples/`` starts empty and the run has to fill it.
    """
    project = register_fixture_methods

    口 = Step(step_num=1, name="Register a sample from outside data/",
             purpose="The fixture stages the CSV beside the project and "
                     "registers that; data/samples/ is left for the run to "
                     "write",
             critical="If this precondition is false the test proves nothing "
                      "-- a file already at registered_path makes the restore "
                      "a no-op skip and the run would 'pass' having done "
                      "nothing")
    source = create_sample_csv(project, "sample_a", num_rows=3)
    payload = source.read_bytes()
    row = sample_row("sample_a")
    assert row is not None, "create_sample_csv left no samples row"
    registered_path = project / row.registered_path
    workspace_dir = registered_path.parent
    assert not workspace_dir.exists() or not list(workspace_dir.iterdir()), (
        f"{workspace_dir} is not empty before the run: "
        f"{list(workspace_dir.iterdir())}"
    )
    entry = cache_entry(project, row.content_hash)
    assert entry.exists(), f"registration cached nothing at {entry}"
    sentinel = project / "data" / "samples" / "sample_a" / ".sample_ready"

    口 = Step(step_num=2, name="Run a pipeline whose root step reads the sample",
             purpose="input_selector -> transform -> transform, through the "
                     "same run-pipeline verb a user types")
    pipeline_path = pipeline_factory(
        name="real_path_restore",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["sample_a"]},
            {"id": "transform_1", "method": "transform",
             "module": "test_pipeline", "params": {"suffix": "_one"}},
            {"id": "transform_2", "method": "transform",
             "module": "test_pipeline", "params": {"suffix": "_two"}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_1"},
            {"source": "transform_1", "target": "transform_2"},
        ],
        samples=[],
    )
    result = run_cli("run-pipeline", "--pipeline", str(pipeline_path),
                     "--project-root", str(project),
                     "--wfc-root", str(_WFC_ROOT), "--cores", "1")
    assert result.returncode == 0, (
        f"wfc run-pipeline exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    口 = Step(step_num=3, name="The sample file is there, and the run put it there",
             purpose="A file the fixture never wrote: the run's restore rule "
                     "read the DVC cache by content hash and copied the bytes "
                     "to the recorded path, then wrote its readiness sentinel")
    assert registered_path.exists(), (
        f"the pipeline completed but never materialized {registered_path}"
    )
    assert registered_path.read_bytes() == payload, (
        f"{registered_path} does not hold the registered source's bytes"
    )
    assert sentinel.exists(), (
        f"the restore rule left no readiness sentinel at {sentinel}"
    )

    口 = Step(step_num=4, name="Clear the cache entry, the copy and every sentinel",
             purpose="The local cache entry is the deletion that forces a "
                     "remote round trip; the archive still holds the object",
             critical=".sample_ready is the restore rule's own output:, so a "
                      "stale one makes Snakemake consider the rule satisfied "
                      "and skip the restore entirely. Leave it behind and the "
                      "re-run passes without running the thing under test")
    import shutil

    force_unlink(entry)
    force_unlink(registered_path)
    sentinel.unlink()
    shutil.rmtree(project / ".runs" / "sentinels", ignore_errors=True)
    assert not entry.exists() and not registered_path.exists()

    口 = Step(step_num=5, name="Re-run: the pipeline pulls the sample back",
             purpose="With the bytes only in the archive the run repopulates "
                     "the cache and the workspace from it, which is the cold "
                     "start a second machine is in")
    result = run_cli("run-pipeline", "--pipeline", str(pipeline_path),
                     "--project-root", str(project),
                     "--wfc-root", str(_WFC_ROOT), "--cores", "1")
    assert result.returncode == 0, (
        f"the re-run exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert entry.exists(), (
        f"the re-run did not pull the sample's content back into the cache "
        f"at {entry}"
    )
    assert registered_path.read_bytes() == payload, (
        f"the re-run did not re-materialize {registered_path}"
    )
    assert sentinel.exists(), (
        f"the restore rule did not run on the re-run -- no sentinel at "
        f"{sentinel}"
    )


@pytest.mark.integration
@requires_docker
@workflow(purpose="A pipeline over a sample whose content is in neither the "
                  "local cache nor the archive refuses at the CLI in one "
                  "message, naming the sample, its hash and the command that "
                  "fixes it, before any run row is written")
def test_a_pipeline_refuses_an_unreachable_sample_before_any_run_row(
    pipeline_factory, register_fixture_methods
):
    """The refusal a user reads instead of N job logs.

    No container runs: the point is that the engine is never started. Before this
    preflight the same situation surfaced as N Snakemake rules exiting
    non-zero, with the real diagnosis buried in job logs and run rows
    already written for every target.

    Its mirror is :func:`test_pipeline_restores_a_sample_it_never_staged`'s
    step 5, where the cache entry is deleted on a **pushed** row and the
    pipeline runs anyway -- the cold start, which this preflight has to
    stay silent about. The only difference between the two is
    ``push_status``.
    """
    from wfc.persistence import PushStatus, Run, Sample, get_session

    project = register_fixture_methods

    口 = Step(step_num=1, name="Register a sample, then put its content out of reach",
             purpose="The row is production's; only its content goes away -- "
                     "the cache entry is pruned (a dvc gc, a fresh checkout) "
                     "and the push never landed, so the archive has nothing "
                     "either")
    create_sample_csv(project, "gone_a", num_rows=3)
    row = sample_row("gone_a")
    entry = cache_entry(project, row.content_hash)
    assert entry.exists(), "registration cached nothing; the setup is wrong"
    with get_session() as session:
        stored = session.exec(
            select(Sample).where(Sample.name == "gone_a")
        ).first()
        stored.push_status = PushStatus.failed.value
        session.add(stored)
        session.commit()
    force_unlink(entry)

    口 = Step(step_num=2, name="Run the pipeline through the run-pipeline verb",
             purpose="The same command the working case uses, so the only "
                     "difference between passing and refusing is the "
                     "sample's reachability")
    launched_before = launched_pipeline_ids(project)
    pipeline_path = pipeline_factory(
        name="real_path_refusal",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["gone_a"]},
            {"id": "transform_1", "method": "transform",
             "module": "test_pipeline", "params": {"suffix": "_one"}},
        ],
        links=[{"source": "selector_1", "target": "transform_1"}],
        samples=[],
    )
    result = run_cli("run-pipeline", "--pipeline", str(pipeline_path),
                     "--project-root", str(project),
                     "--wfc-root", str(_WFC_ROOT), "--cores", "1")

    口 = Step(step_num=3, name="One message, and it is the whole repair",
             purpose="The sample, its content hash and the register-sample "
                     "line a user can type -- on stderr, not at the bottom "
                     "of a traceback and not inside a job log")
    assert result.returncode == 1, (
        f"the pipeline did not refuse (rc={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "gone_a" in result.stderr, result.stderr
    assert row.content_hash in result.stderr, result.stderr
    assert "wfc register-sample --name gone_a --source" in result.stderr, (
        result.stderr
    )
    assert "Traceback" not in result.stderr, result.stderr

    口 = Step(step_num=4, name="Nothing was launched",
             purpose="The refusal is at pipeline start, before prepare_launch "
                     "mints a pipeline id and freezes a document and before "
                     "the engine is handed anything",
             critical="The launch-artifact assertion is the one that bites. "
                      "Run rows are written by run-step inside the engine, so "
                      "asserting only 'no Run rows' passes even when the "
                      "refusal is moved AFTER prepare_launch -- it would be a "
                      "vacuous assertion pinning nothing.")
    with get_session() as session:
        assert session.exec(select(Run)).all() == []
    assert launched_pipeline_ids(project) == launched_before, (
        f"prepare_launch ran before the refusal: "
        f"{launched_pipeline_ids(project) - launched_before} was minted, "
        f"frozen and left behind for a run that never started"
    )
