"""Subsystem test: wfc.envs.register for the pixi backend (US-1, Tier 2).

Mocks wfc.docker_runner.build + image_inspect to keep the test
deterministic and offline. Asserts the resulting manifest entry has the
local-only ref shape ``<name>@sha256:<hex>`` (no docker:// scheme),
populated env_fingerprint, and built_from_lock. The Dockerfile is
written to .wfc/build/<name>/Dockerfile as a side effect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dflow.core.decorators import workflow


@workflow(
    purpose="wfc.envs.register('image-io', 'pixi', ...) builds the image, "
            "writes the Dockerfile to .wfc/build/<name>/Dockerfile, resolves "
            "the local-only digest-pinned ref ('<name>@sha256:<hex>' — "
            "NO docker:// scheme for generator backends), and persists the "
            "manifest entry with env_fingerprint + built_from_lock populated"
)
def test_register_env_pixi_e2e_mocked(tmp_path, monkeypatch):
    from wfc import envs as envs_mod
    from wfc import docker_runner

    (tmp_path / ".wfc").mkdir()

    digest_hex = "c" * 64

    build_calls = []

    def fake_build(dockerfile_dir, tag):
        build_calls.append((Path(dockerfile_dir), tag))

    def fake_inspect(ref):
        return f"sha256:{digest_hex}"

    monkeypatch.setattr(docker_runner, "build", fake_build)
    monkeypatch.setattr(docker_runner, "image_inspect", fake_inspect)

    record = envs_mod.register(
        name="image-io",
        backend="pixi",
        source={"pixi_env": "image-io"},
        project_dir=tmp_path,
    )

    # Manifest entry on disk
    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text(encoding="utf-8"))
    assert "image-io" in manifest["envs"]
    entry = manifest["envs"]["image-io"]

    # Local-only ref shape (NO docker:// prefix per ADR-019 amendment).
    assert entry["container"] == f"image-io@sha256:{digest_hex}"
    assert not entry["container"].startswith("docker://")

    # env_fingerprint is populated and is a 32-char md5 hex.
    assert entry["env_fingerprint"]
    assert len(entry["env_fingerprint"]) == 32
    assert all(c in "0123456789abcdef" for c in entry["env_fingerprint"])

    # built_from_lock populated for pixi.
    assert entry["built_from_lock"] == "pixi.lock"
    assert entry["built_at"]
    assert entry["backend"] == "pixi"

    # No base_image_used field — dropped per ADR-019 amendment.
    assert "base_image_used" not in entry

    # Dockerfile was written
    dockerfile_path = tmp_path / ".wfc" / "build" / "image-io" / "Dockerfile"
    assert dockerfile_path.exists()
    text = dockerfile_path.read_text(encoding="utf-8")
    assert "# syntax=docker/dockerfile:1.4" in text

    # docker build was called once
    assert len(build_calls) == 1
    called_dir, called_tag = build_calls[0]
    assert called_dir == (tmp_path / ".wfc" / "build" / "image-io").resolve() \
        or called_dir == tmp_path / ".wfc" / "build" / "image-io"
    assert called_tag == "image-io:_wfc-build"

    # The returned record matches what was persisted.
    assert record.container == entry["container"]
    assert record.env_fingerprint == entry["env_fingerprint"]
