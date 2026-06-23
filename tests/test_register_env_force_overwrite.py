"""E2E test: --force overwrites an existing manifest entry (US-3, Tier 3).

Drives the full CLI path: argparse → _cli_register_env → wfc.envs.register
→ manifest write. Mocks the docker_runner boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dflow.core.decorators import workflow, Step


@workflow(
    purpose="wfc register-env on an existing env errors actionably (exit "
            "non-zero, message names env and points at --force, manifest "
            "unchanged); --force rebuilds and overwrites the manifest "
            "entry with the new digest. Methods referencing the env are "
            "NOT touched — they resolve through the manifest at run-step "
            "time and pick up the new digest automatically."
)
def test_register_env_force_overwrites_existing(tmp_path, monkeypatch, capsys):
    from wfc import envs as envs_mod
    from wfc import docker_runner
    from wfc.cli import cli_main

    口 = Step(step_num=1, name="Seed manifest with an existing env",
             purpose="Existing entry has an OLD digest; we will rebuild and "
                     "expect it to be replaced with a NEW digest only on --force.")
    (tmp_path / ".wfc").mkdir()
    old_digest = "a" * 64
    seed_manifest = {
        "schema_version": 1,
        "envs": {
            "image-io": {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": f"image-io@sha256:{old_digest}",
                "env_fingerprint": "00" * 16,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-05-01T00:00:00Z",
            }
        },
    }
    (tmp_path / ".wfc" / "envs.json").write_text(json.dumps(seed_manifest))

    # cd into project so _resolve_project_dir_for_envs finds it.
    monkeypatch.chdir(tmp_path)

    new_digest = "b" * 64
    monkeypatch.setattr(docker_runner, "build", lambda d, t: None)
    monkeypatch.setattr(docker_runner, "image_inspect", lambda r: f"sha256:{new_digest}")

    口 = Step(step_num=2, name="Run CLI without --force, expect non-zero exit",
             purpose="Default behavior must refuse to clobber; message must "
                     "name the env and point at --force.")
    rc = cli_main(["register-env", "image-io", "--backend", "pixi"])
    captured = capsys.readouterr()
    assert rc != 0
    assert "image-io" in captured.err
    assert "--force" in captured.err

    # Manifest unchanged.
    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text())
    assert manifest["envs"]["image-io"]["container"] == f"image-io@sha256:{old_digest}"

    口 = Step(step_num=3, name="Re-run with --force, expect overwrite",
             purpose="Manifest container field flips to the new digest; "
                     "old image is intentionally NOT deleted from the "
                     "docker daemon (ADR-019 #7: users prune manually).")
    rc = cli_main(["register-env", "image-io", "--backend", "pixi", "--force"])
    assert rc == 0

    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text())
    new_entry = manifest["envs"]["image-io"]
    assert new_entry["container"] == f"image-io@sha256:{new_digest}"
    # Different env_fingerprint (digest changed).
    assert new_entry["env_fingerprint"] != "00" * 16


def test_register_env_dry_run_skips_docker_and_manifest(tmp_path, monkeypatch):
    """US-5: --dry-run short-circuits before any docker subprocess and
    never writes the manifest."""
    from wfc import docker_runner
    from wfc.cli import cli_main

    (tmp_path / ".wfc").mkdir()
    monkeypatch.chdir(tmp_path)

    # Any call to docker_runner is a failure.
    def _fail(*a, **kw):
        raise AssertionError("--dry-run must not invoke docker_runner")

    monkeypatch.setattr(docker_runner, "build", _fail)
    monkeypatch.setattr(docker_runner, "image_inspect", _fail)
    monkeypatch.setattr(docker_runner, "pull", _fail)

    rc = cli_main([
        "register-env", "image-io", "--backend", "pixi", "--dry-run",
    ])
    assert rc == 0
    # Manifest must NOT exist (we never wrote one).
    assert not (tmp_path / ".wfc" / "envs.json").exists()
    # Dockerfile was written.
    assert (tmp_path / ".wfc" / "build" / "image-io" / "Dockerfile").exists()
