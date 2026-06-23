"""Subsystem test: wfc.envs.register BYO branch (US-2, Tier 2).

Two variants:

  (a) Image not local → pull + inspect; manifest stores
      ``docker://<orig>@sha256:<digest>``.
  (b) Image already local → pull NOT called, only inspect.

Neither variant invokes any push function (there is no push function in
v1 per ADR-019 2026-05-17 amendment).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dflow.core.decorators import workflow


@workflow(
    purpose="BYO register-env with a floating-tag input ref pulls the "
            "image (when not local), inspects the digest, and stores the "
            "manifest container value as 'docker://<orig>@sha256:<hex>' — "
            "scheme preserved, digest appended"
)
def test_register_env_byo_floating_tag_not_local_pulls(tmp_path, monkeypatch):
    from wfc import envs as envs_mod
    from wfc import docker_runner

    (tmp_path / ".wfc").mkdir()

    digest_hex = "d" * 64

    pull_calls = []
    inspect_calls = []

    # First inspect raises (image not local), pull succeeds, second inspect
    # returns the digest.
    def fake_inspect(ref):
        inspect_calls.append(ref)
        if len(inspect_calls) == 1:
            raise RuntimeError("Error: No such image: reg/img:latest")
        return f"sha256:{digest_hex}"

    def fake_pull(ref):
        pull_calls.append(ref)

    monkeypatch.setattr(docker_runner, "image_inspect", fake_inspect)
    monkeypatch.setattr(docker_runner, "pull", fake_pull)
    # Ensure build is NEVER called for byo
    monkeypatch.setattr(
        docker_runner, "build",
        lambda *a, **kw: pytest.fail("docker_runner.build must not be called for byo"),
    )

    record = envs_mod.register(
        name="cellpose",
        backend="byo",
        source={"image": "docker://reg/img:latest"},
        project_dir=tmp_path,
    )

    # docker pull was called exactly once with the daemon-side ref (no docker:// prefix).
    assert pull_calls == ["reg/img:latest"]
    assert len(inspect_calls) == 2

    # Manifest entry stores docker://<orig-image>@sha256:<digest>
    assert record.container == f"docker://reg/img@sha256:{digest_hex}"
    assert record.backend == "byo"


@workflow(
    purpose="BYO register-env with an already-local image SKIPS docker pull "
            "(only inspects) — wfc does not waste a network round-trip when "
            "the image is resolvable locally"
)
def test_register_env_byo_already_local_skips_pull(tmp_path, monkeypatch):
    from wfc import envs as envs_mod
    from wfc import docker_runner

    (tmp_path / ".wfc").mkdir()

    digest_hex = "e" * 64

    pull_calls = []
    inspect_calls = []

    def fake_inspect(ref):
        inspect_calls.append(ref)
        return f"sha256:{digest_hex}"

    def fake_pull(ref):
        pull_calls.append(ref)

    monkeypatch.setattr(docker_runner, "image_inspect", fake_inspect)
    monkeypatch.setattr(docker_runner, "pull", fake_pull)

    record = envs_mod.register(
        name="cellpose",
        backend="byo",
        source={"image": "docker://reg/img:v1"},
        project_dir=tmp_path,
    )

    # No pull, single inspect.
    assert pull_calls == []
    assert inspect_calls == ["reg/img:v1"]
    assert record.container == f"docker://reg/img@sha256:{digest_hex}"


def test_register_env_byo_rejects_base_image(tmp_path, monkeypatch):
    """--base-image makes no sense for byo (no Dockerfile to override)."""
    from wfc import envs as envs_mod

    (tmp_path / ".wfc").mkdir()

    with pytest.raises(ValueError, match="base-image"):
        envs_mod.register(
            name="cellpose",
            backend="byo",
            source={"image": "docker://reg/img:v1"},
            base_image="docker://other/base@sha256:" + ("a" * 64),
            project_dir=tmp_path,
        )
