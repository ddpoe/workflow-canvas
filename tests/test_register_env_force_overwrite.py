"""E2E test: --force overwrites an existing manifest entry (Tier 3).

Drives the full CLI path: argparse → _cli_register_env →
wfc.environments.verbs.register_env → wfc.environments.register → manifest
write. Mocks the docker_runner boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.fakes import (
    stub_docker_build,
    stub_docker_image_inspect,
    stub_readiness_probes,
)

from axiom_annotations import workflow, Step


@workflow(
    purpose="On a host with no Docker, wfc register-env on an existing env "
            "errors actionably (exit non-zero, message names env and points "
            "at --force, manifest unchanged), and --force reaches the Docker "
            "gate's not-runnable message; with Docker up, --force rebuilds "
            "and overwrites the manifest "
            "entry with the new digest. Methods referencing the env are "
            "NOT touched — they resolve through the manifest at run-step "
            "time and pick up the new digest automatically."
)
def test_register_env_force_overwrites_existing(tmp_path, monkeypatch, capsys):
    from wfc import environments as envs_mod
    from wfc.environments import docker as docker_runner
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

    # A real project (marker included) with the cwd inside it, so the
    # canonical resolver's upward walk finds it; the per-process cache is
    # cleared so the walk actually runs.
    (tmp_path / ".wfc" / "wf-canvas.toml").write_text('[project]\nname = "t"\n')
    monkeypatch.chdir(tmp_path)
    from wfc.persistence import reset_engine
    reset_engine()

    # File-mode source.
    (tmp_path / "pixi.lock").write_text("version: 6\n", encoding="utf-8")

    new_digest = "b" * 64
    stub_docker_build(monkeypatch, lambda d, t: None)
    stub_docker_image_inspect(monkeypatch, f"sha256:{new_digest}")

    口 = Step(step_num=2, name="Run CLI without --force on a host with no Docker",
             purpose="Default behavior must refuse to clobber; message must "
                     "name the env and point at --force. The refusal is the "
                     "verb's own, reported before Docker is asked.")
    stub_readiness_probes(monkeypatch, docker="fail")
    rc = cli_main([
        "register-env", "image-io", "--from", "pixi.lock", "--backend", "pixi",
    ])
    captured = capsys.readouterr()
    assert rc != 0
    assert "image-io" in captured.err
    assert "--force" in captured.err

    # Manifest unchanged.
    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text())
    assert manifest["envs"]["image-io"]["container"] == f"image-io@sha256:{old_digest}"

    口 = Step(step_num=3, name="Re-run with --force on a host with no Docker",
             purpose="A request that passes every check of its own stops at "
                     "the Docker gate with the not-runnable message; the "
                     "manifest is unchanged.")
    rc = cli_main([
        "register-env", "image-io", "--from", "pixi.lock", "--backend", "pixi",
        "--force",
    ])
    captured = capsys.readouterr()
    assert rc != 0
    assert "This project isn't ready to run" in captured.err
    assert "wfc doctor" in captured.err
    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text())
    assert manifest["envs"]["image-io"]["container"] == f"image-io@sha256:{old_digest}"

    口 = Step(step_num=4, name="Re-run with --force and Docker up, expect overwrite",
             purpose="Manifest container field flips to the new digest; "
                     "old image is intentionally NOT deleted from the "
                     "docker daemon (users prune manually).")
    stub_readiness_probes(monkeypatch, docker="ok")
    rc = cli_main([
        "register-env", "image-io", "--from", "pixi.lock", "--backend", "pixi",
        "--force",
    ])
    assert rc == 0

    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text())
    new_entry = manifest["envs"]["image-io"]
    assert new_entry["container"] == f"docker://local/image-io@sha256:{new_digest}"
    # Different env_fingerprint (digest changed).
    assert new_entry["env_fingerprint"] != "00" * 16
