"""Subsystem test: wfc.environments.register BYO branch (Tier 2).

Three variants:

  (a) Image not local → pull + inspect; manifest stores
      ``docker://<orig>@sha256:<digest>``.
  (b) Image already local → pull NOT called, only inspect.
  (c) A supplied digest the daemon disagrees with → refused, no record.

No variant invokes a push function (wfc has none).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiom_annotations import workflow

from tests.fixtures.fakes import (
    stub_docker_build,
    stub_docker_image_inspect,
    stub_docker_pull,
)


@workflow(
    purpose="BYO register-env with a floating-tag input ref pulls the "
            "image (when not local), inspects the digest, and stores the "
            "manifest container value as 'docker://<orig>@sha256:<hex>' — "
            "scheme preserved, digest appended"
)
def test_register_env_byo_floating_tag_not_local_pulls(tmp_path, monkeypatch):
    from wfc import environments as envs_mod

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

    stub_docker_image_inspect(monkeypatch, fake_inspect)
    stub_docker_pull(monkeypatch, fake_pull)
    # Ensure build is NEVER called for byo
    stub_docker_build(
        monkeypatch,
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
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    digest_hex = "e" * 64

    pull_calls = []
    inspect_calls = []

    def fake_inspect(ref):
        inspect_calls.append(ref)
        return f"sha256:{digest_hex}"

    def fake_pull(ref):
        pull_calls.append(ref)

    stub_docker_image_inspect(monkeypatch, fake_inspect)
    stub_docker_pull(monkeypatch, fake_pull)

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


@workflow(
    purpose="BYO register-env with a supplied @sha256 digest that the local "
            "daemon resolves to a different digest is refused with a "
            "RuntimeError naming both digests, and no manifest record is "
            "written — digest pinning never silently swaps the image"
)
def test_register_env_byo_supplied_digest_mismatch_refused(tmp_path, monkeypatch):
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    supplied, resolved = "a" * 64, "b" * 64
    stub_docker_image_inspect(monkeypatch, f"sha256:{resolved}")
    stub_docker_pull(monkeypatch, None)

    with pytest.raises(RuntimeError) as excinfo:
        envs_mod.register(
            name="cellpose",
            backend="byo",
            source={"image": f"docker://reg/img@sha256:{supplied}"},
            project_dir=tmp_path,
        )

    message = str(excinfo.value)
    assert f"sha256:{supplied}" in message
    assert f"sha256:{resolved}" in message
    assert envs_mod.get("cellpose", tmp_path) is None


def test_register_env_byo_rejects_base_image(tmp_path, monkeypatch):
    """--base-image makes no sense for byo (no Dockerfile to override)."""
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    with pytest.raises(ValueError, match="base-image"):
        envs_mod.register(
            name="cellpose",
            backend="byo",
            source={"image": "docker://reg/img:v1"},
            base_image="docker://other/base@sha256:" + ("a" * 64),
            project_dir=tmp_path,
        )
