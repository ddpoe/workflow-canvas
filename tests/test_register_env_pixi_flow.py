"""Subsystem test: wfc.environments.register for the pixi backend (Tier 2).

Mocks wfc.environments.docker.build + image_inspect to keep the test
deterministic and offline. Asserts the resulting manifest entry has the
digest-pinned ref shape ``docker://local/<name>@sha256:<hex>``,
populated env_fingerprint, and built_from_lock. The Dockerfile is
written to .wfc/build/<name>/Dockerfile as a side effect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiom_annotations import workflow

from tests.fixtures.fakes import stub_docker_build, stub_docker_image_inspect


@workflow(
    purpose="wfc.environments.register('image-io', 'pixi', ...) builds the image, "
            "writes the Dockerfile to .wfc/build/<name>/Dockerfile, resolves "
            "the digest-pinned ref under the local/ namespace "
            "('docker://local/<name>@sha256:<hex>'), and persists the "
            "manifest entry with env_fingerprint + built_from_lock populated"
)
def test_register_env_pixi_e2e_mocked(tmp_path, monkeypatch):
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    digest_hex = "c" * 64

    build_calls = []

    def fake_build(dockerfile_dir, tag):
        build_calls.append((Path(dockerfile_dir), tag))

    def fake_inspect(ref):
        return f"sha256:{digest_hex}"

    stub_docker_build(monkeypatch, fake_build)
    stub_docker_image_inspect(monkeypatch, fake_inspect)

    # Source payload: verbatim lock/toml/freeze content that register()
    # stages into the build context. The lock names the requested env so
    # validate_lock_for_env passes before any docker work.
    pixi_lock = "version: 6\nenvironments:\n  image-io:\n    packages: {}\n"
    pixi_toml = (
        '[project]\nname = "image-io"\n'
        'channels = ["conda-forge"]\nplatforms = ["linux-64"]\n'
    )
    pip_freeze = "numpy==1.26.4\npandas==2.2.0\n"

    record = envs_mod.register(
        name="image-io",
        backend="pixi",
        source={
            "pixi_lock_content": pixi_lock,
            "pixi_toml_content": pixi_toml,
            "pip_freeze_content": pip_freeze,
        },
        project_dir=tmp_path,
    )

    # Manifest entry on disk
    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text(encoding="utf-8"))
    assert "image-io" in manifest["envs"]
    entry = manifest["envs"]["image-io"]

    # Digest-pinned ref under the local/ namespace — the same
    # docker://<host>/<path>@sha256 shape validate_container_ref enforces.
    assert entry["container"] == f"docker://local/image-io@sha256:{digest_hex}"

    # env_fingerprint is populated and is a 32-char md5 hex.
    assert entry["env_fingerprint"]
    assert len(entry["env_fingerprint"]) == 32
    assert all(c in "0123456789abcdef" for c in entry["env_fingerprint"])

    # built_from_lock populated for pixi.
    assert entry["built_from_lock"] == "pixi.lock"
    assert entry["built_at"]
    assert entry["backend"] == "pixi"

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
    assert called_tag == "local/image-io:_wfc-build"

    # The returned record matches what was persisted.
    assert record.container == entry["container"]
    assert record.env_fingerprint == entry["env_fingerprint"]

    # Build context was staged on disk with the submitted bytes — the mocked
    # docker build cannot see a build context missing pixi.toml, so assert
    # each staged source file exists and carries exactly what was passed in.
    build_dir = tmp_path / ".wfc" / "build" / "image-io"
    assert (build_dir / "pixi.lock").read_text(encoding="utf-8") == pixi_lock
    assert (build_dir / "pixi.toml").read_text(encoding="utf-8") == pixi_toml
    assert (build_dir / "pip-freeze.txt").read_text(encoding="utf-8") == pip_freeze

    # Produce→attach closure: the freshly written manifest entry must resolve
    # through the production attach gate that method registration runs, which
    # rejects an env whose ref shape does not match what register() writes.
    from wfc.environments import check_method_env
    assert check_method_env("image-io", tmp_path) == "image-io"


@workflow(
    purpose="register() with a staged pixi.lock that lacks the requested env "
            "name fails before any docker work — the error lists the lock's "
            "actual environment keys, no Dockerfile is staged, no docker "
            "build runs, and no manifest entry is written"
)
def test_register_env_rejects_env_name_missing_from_lock_before_docker(
    tmp_path, monkeypatch
):
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    build_calls = []
    stub_docker_build(monkeypatch,
                      lambda dockerfile_dir, tag: build_calls.append(tag))
    stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)

    lock = "version: 6\nenvironments:\n  default:\n    packages: {}\n"
    with pytest.raises(ValueError) as exc:
        envs_mod.register(
            name="myname",
            backend="pixi",
            source={
                "pixi_lock_content": lock,
                "pixi_toml_content": '[project]\nname = "smoke"\n',
                "pip_freeze_content": "",
            },
            project_dir=tmp_path,
        )

    msg = str(exc.value)
    assert "myname" in msg
    assert "default" in msg

    # Failed BEFORE any docker work or disk side effects.
    assert build_calls == []
    assert not (tmp_path / ".wfc" / "build" / "myname").exists()
    assert not (tmp_path / ".wfc" / "envs.json").exists()
