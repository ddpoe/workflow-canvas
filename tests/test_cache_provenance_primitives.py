"""Cache-integrity & provenance primitives (Docker-free).

These exercise the cache/DVC primitives directly against an archived run's
content — faster and sharper than re-running a pipeline, and needing no Docker.
They live in the default suite (not tests/integration/) because they never
touch a container.

Covered invariants:
  - corruption/tamper: mutating a cache file is detected by restore_from_cache,
    which replaces it from a clean source (hash-verified checkout).
  - missing entry: deleting the cache entry makes resolve_input FAIL cleanly
    (returns None) rather than serving wrong/absent bytes.
  - remote-pull: with the local entry pruned but present on a real local-FS DVC
    remote, resolve_input REMOTE-PULLs byte-identical content and repopulates
    the local cache.
  - one copy: once archived, the output leaves the run archive and the cache
    entry holds its bytes.
"""

import configparser
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from wfc.storage import resolve_input
from wfc.persistence import get_session, Method, Run, RunOutput
from wfc.identity import hash_path
from wfc.storage import archive_outputs, restore_from_cache
from wfc.storage.cache import _cache_path


# =============================================================================
# Helpers
# =============================================================================

def _wire_local_remote(project_dir):
    """Init a real DVC repo with a local-FS remote; return the remote dir."""
    remote_dir = project_dir.parent / f"{project_dir.name}-remote-storage"
    remote_dir.mkdir(exist_ok=True)
    from dvc.repo import Repo
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
    return remote_dir


def _seed_archived_output(tmp_project, payload: bytes):
    """Create a completed Run + RunOutput with archived content; return (run_id, hash, staging).

    Builds a minimal Method/Run/RunOutput chain, writes a staging artifact, and
    runs the real archive pass so content_hash + cache entry exist exactly as a
    pipeline run would leave them.
    """
    from wfc.persistence import Module
    with get_session() as session:
        module = Module(name="cmod", description="x")
        session.add(module)
        session.commit()
        session.refresh(module)
        method = Method(name="cmeth", module_id=module.id, env="container:demo")
        session.add(method)
        session.commit()
        session.refresh(method)
        run = Run(method_id=method.id, status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        # Where collect writes a run's outputs: its run archive.
        from wfc.layout import run_archive_dir
        staging = run_archive_dir(tmp_project, run.id) / "out.csv"
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(payload)
        ro = RunOutput(
            run_id=run.id, slot="out", output_name="out.csv",
            artifact_path=str(staging), artifact_type="method_file",
        )
        session.add(ro)
        session.commit()
        run_id = run.id
    archive_outputs(tmp_project, run_id=run_id)
    with get_session() as session:
        ro = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).first()
        content_hash = ro.content_hash
    return run_id, content_hash, staging


def _setup_dvc(project_root):
    """Write [dvc] config + init local cache so archive/resolve have somewhere to write."""
    remote_dir = project_root.parent / f"{project_root.name}-dvc-remote"
    remote_dir.mkdir(parents=True, exist_ok=True)
    config_path = project_root / ".wfc" / "wf-canvas.toml"
    db_path = (project_root / ".wfc" / "wfc.db").as_posix()
    config_path.write_text(
        f'[database]\nurl = "sqlite:///{db_path}"\n\n'
        f'[project]\nname = "test"\n\n'
        f'[dvc]\nremote_type = "local"\n'
        f'remote_path = "{remote_dir.as_posix()}"\nauto_init = true\n'
    )
    from wfc.storage import init_dvc
    init_dvc(project_root, {"url": str(remote_dir)})


# =============================================================================
# Cache-integrity invariants
# =============================================================================

@workflow(purpose="A changed restored view is detected and rebuilt as a read-only "
                  "view of its intact cache entry at the same path")
def test_corruption_detected_and_replaced(tmp_project):
    import os

    from wfc.storage import links
    from wfc.storage.cache import open_dirs

    _setup_dvc(tmp_project)
    _ = Step(step_num=1, name="Archive a known output", purpose="Populate the cache with known bytes")
    run_id, content_hash, _ = _seed_archived_output(tmp_project, b"clean-bytes-v1\n")
    cache_path = _cache_path(tmp_project, content_hash)
    assert cache_path.exists() and hash_path(cache_path) == content_hash
    hardlinks = links.probe_pair(cache_path.parent, tmp_project).tier == links.TIER_HARDLINK

    _ = Step(step_num=2, name="Restore to a workspace path",
             purpose="The restore is a read-only view of the cache object")
    dest = tmp_project / "checkout.csv"
    assert restore_from_cache(content_hash, dest, tmp_project) is True
    assert dest.read_bytes() == b"clean-bytes-v1\n"
    assert not os.access(dest, os.W_OK), "a restored view is read-only"
    if hardlinks:
        assert os.path.samefile(dest, cache_path), "the view is the cache object's inode"

    _ = Step(step_num=3, name="Change the view",
             purpose="Other bytes saved at its path as a new file, as an editor "
                     "that saves by rename does; the cache object is untouched")
    open_dirs(dest.parent)
    edited = dest.with_name(dest.name + ".edited")
    edited.write_bytes(b"TAMPERED\n")
    links.replace_over(edited, dest)
    assert hash_path(dest) != content_hash
    assert cache_path.read_bytes() == b"clean-bytes-v1\n"

    _ = Step(step_num=4, name="Restore again",
             purpose="restore_from_cache finds the hash mismatch and renames a "
                     "new read-only view over the changed one")
    assert restore_from_cache(content_hash, dest, tmp_project) is True
    assert dest.read_bytes() == b"clean-bytes-v1\n", "the changed view was not rebuilt"
    assert not os.access(dest, os.W_OK), "the rebuilt view is read-only"
    assert not os.access(cache_path, os.W_OK), "the cache object stays read-only"
    if hardlinks:
        assert os.path.samefile(dest, cache_path), "rebuilt as a view, not a copy"


@workflow(purpose="Deleting a cache entry makes resolve_input refuse the output "
                  "naming its content hash and the re-run, not serve garbage")
def test_missing_cache_entry_fails_cleanly(tmp_project):
    _setup_dvc(tmp_project)
    _ = Step(step_num=1, name="Archive then delete the cache entry",
             purpose="Remove the local cache object with no remote configured")
    run_id, content_hash, _ = _seed_archived_output(tmp_project, b"to-be-deleted\n")
    cache_path = _cache_path(tmp_project, content_hash)
    import os, stat
    os.chmod(cache_path, stat.S_IWRITE)
    cache_path.unlink()
    assert not cache_path.exists()

    _ = Step(step_num=2, name="resolve_input must FAIL cleanly",
             purpose="No local entry + never pushed -> a refusal naming the "
                     "hash and the re-run (clean fail, no wrong bytes)")
    from wfc.storage import InputUnavailableError
    with pytest.raises(InputUnavailableError) as refusal:
        resolve_input(run_id)
    assert content_hash in str(refusal.value)
    assert f"Re-run run {run_id}" in str(refusal.value)


@workflow(
    purpose="A missing local entry is REMOTE-PULLed from a real local-FS DVC "
            "remote with byte-identical content; local cache repopulates",
)
def test_remote_pull_restores_identical_bytes(tmp_project):
    _ = Step(step_num=1, name="Archive + push to a real local-FS DVC remote",
             purpose="Populate the remote so a pruned local entry can be pulled back")
    _setup_dvc(tmp_project)
    payload = b"remote-payload-v1\n"
    run_id, content_hash, _ = _seed_archived_output(tmp_project, payload)
    remote_dir = _wire_local_remote(tmp_project)
    from wfc.storage import push as remote_push
    remote_push([content_hash], tmp_project)
    # The push worker records a successful push on the row; this test pushes
    # through the transport primitive directly, so it records it the same way.
    from tests.fixtures.fakes.shortcuts import flip_push_status
    flip_push_status(run_id, "pushed")
    assert any(f.is_file() for f in remote_dir.rglob("*")), "remote did not receive the pushed object"

    _ = Step(step_num=2, name="Prune local cache entry",
             purpose="Force resolve_input down the REMOTE-PULL branch")
    cache_path = _cache_path(tmp_project, content_hash)
    import os, stat
    os.chmod(cache_path, stat.S_IWRITE)
    cache_path.unlink()
    assert not cache_path.exists()

    _ = Step(step_num=3, name="resolve_input pulls from remote",
             purpose="Returns the repopulated local cache path with byte-identical content")
    resolved = resolve_input(run_id)
    assert resolved is not None, "resolve_input failed to REMOTE-PULL"
    assert Path(resolved).read_bytes() == payload, "pulled bytes differ from original"
    assert cache_path.exists(), "local cache not repopulated after pull"


@workflow(purpose="Once archived, the cache entry is the output's only local copy: "
                  "the run-archive file is gone and the entry holds its bytes")
def test_archived_output_leaves_the_cache_as_its_only_copy(tmp_project):
    _setup_dvc(tmp_project)
    _ = Step(step_num=1, name="Archive an output",
             purpose="archive_outputs copies it into the cache, commits the hash, "
                     "then removes it from the run archive")
    payload = b"staging-vs-cache\n"
    run_id, content_hash, staging = _seed_archived_output(tmp_project, payload)

    _ = Step(step_num=2, name="Assert the cache holds the one copy",
             purpose="The run-archive file is gone; the entry holds the bytes")
    assert not staging.exists(), "the run-archive copy outlived the archive pass"
    cache_path = _cache_path(tmp_project, content_hash)
    assert cache_path.read_bytes() == payload
    assert hash_path(cache_path) == content_hash
