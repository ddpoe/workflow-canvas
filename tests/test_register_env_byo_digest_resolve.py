"""Subsystem test: wfc.environments.register BYO branch (Tier 2).

A registry image is recorded by its registry digest (the ``RepoDigests``
entry for its repository), never by ``.Id``: on the classic image store
``.Id`` is the config digest, which no registry serves. Every scenario here
gives ``.Id`` and the registry digest different values, as classic Docker
does.

  (a) Image not local → inspect misses, pull, inspect; the registry digest
      is recorded as ``docker://<orig>@sha256:<registry digest>``.
  (b) Image already local → pull NOT called.
  (c) A supplied digest equal to the registry digest → accepted.
  (d) A supplied digest the registry digest disagrees with (the ``.Id``,
      say) → refused, no record.
  (e) A ``local/`` image (the demo's shape) has no RepoDigests → recorded
      on its image ID, never pulled.

No variant invokes a push function (wfc has none).
"""

from __future__ import annotations

import json

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import (
    canned_process,
    fake_subprocess_run,
    stub_docker_build,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_repo_digest,
)

#: The classic store's two digests for one pulled image.
IMAGE_ID = "c" * 64
REGISTRY_DIGEST = "d" * 64


@workflow(
    purpose="BYO register-env with a floating-tag input ref pulls the "
            "image (when not local) and records the registry digest for "
            "its repository, not the image ID, as "
            "'docker://<orig>@sha256:<registry digest>' — the ref Docker "
            "is handed runs on the classic store"
)
def test_register_env_byo_floating_tag_not_local_pulls(tmp_path, monkeypatch):
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    pull_calls = []
    inspect_calls = []
    repo_digest_calls = []

    # First inspect raises (image not local), pull succeeds, second inspect
    # finds the image.
    def fake_inspect(ref):
        inspect_calls.append(ref)
        if len(inspect_calls) == 1:
            raise RuntimeError("Error: No such image: reg/img:latest")
        return f"sha256:{IMAGE_ID}"

    def fake_repo_digest(ref, repository):
        repo_digest_calls.append((ref, repository))
        return f"sha256:{REGISTRY_DIGEST}"

    stub_docker_image_inspect(monkeypatch, fake_inspect)
    stub_docker_repo_digest(monkeypatch, fake_repo_digest)
    stub_docker_pull(monkeypatch, pull_calls.append)
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
    # The registry digest is asked for the image's own repository.
    assert repo_digest_calls == [("reg/img:latest", "reg/img")]

    # Manifest entry stores docker://<orig-image>@sha256:<registry digest>
    assert record.container == f"docker://reg/img@sha256:{REGISTRY_DIGEST}"
    assert record.backend == "byo"
    # What docker run is handed: the registry ref, which every store runs.
    assert envs_mod.daemon_ref(record.container) == (
        f"reg/img@sha256:{REGISTRY_DIGEST}"
    )


@workflow(
    purpose="BYO register-env with an already-local image SKIPS docker pull "
            "(only inspects, then reads the registry digest) — wfc does not "
            "waste a network round-trip when the image is resolvable locally"
)
def test_register_env_byo_already_local_skips_pull(tmp_path, monkeypatch):
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    pull_calls = []
    inspect_calls = []

    def fake_inspect(ref):
        inspect_calls.append(ref)
        return f"sha256:{IMAGE_ID}"

    stub_docker_image_inspect(monkeypatch, fake_inspect)
    stub_docker_repo_digest(monkeypatch, f"sha256:{REGISTRY_DIGEST}")
    stub_docker_pull(monkeypatch, pull_calls.append)

    record = envs_mod.register(
        name="cellpose",
        backend="byo",
        source={"image": "docker://reg/img:v1"},
        project_dir=tmp_path,
    )

    # No pull, single inspect.
    assert pull_calls == []
    assert inspect_calls == ["reg/img:v1"]
    assert record.container == f"docker://reg/img@sha256:{REGISTRY_DIGEST}"


@workflow(
    purpose="BYO register-env with a supplied @sha256 pin equal to the "
            "registry digest is accepted even though the image ID differs, "
            "and the record carries that pin"
)
def test_register_env_byo_supplied_registry_digest_accepted(tmp_path, monkeypatch):
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    stub_docker_image_inspect(monkeypatch, f"sha256:{IMAGE_ID}")
    stub_docker_repo_digest(monkeypatch, f"sha256:{REGISTRY_DIGEST}")
    stub_docker_pull(monkeypatch, None)

    envs_mod.register(
        name="cellpose",
        backend="byo",
        source={"image": f"docker://reg/img@sha256:{REGISTRY_DIGEST}"},
        project_dir=tmp_path,
    )

    stored = envs_mod.get("cellpose", tmp_path)
    assert stored.container == f"docker://reg/img@sha256:{REGISTRY_DIGEST}"


@workflow(
    purpose="BYO register-env with a supplied @sha256 digest that differs "
            "from the image's registry digest (here the image ID, which "
            "classic Docker reports as .Id) is refused with a RuntimeError "
            "naming both digests, and no manifest record is written — "
            "digest pinning never silently swaps the image"
)
def test_register_env_byo_supplied_digest_mismatch_refused(tmp_path, monkeypatch):
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    stub_docker_image_inspect(monkeypatch, f"sha256:{IMAGE_ID}")
    stub_docker_repo_digest(monkeypatch, f"sha256:{REGISTRY_DIGEST}")
    stub_docker_pull(monkeypatch, None)

    with pytest.raises(RuntimeError) as excinfo:
        envs_mod.register(
            name="cellpose",
            backend="byo",
            source={"image": f"docker://reg/img@sha256:{IMAGE_ID}"},
            project_dir=tmp_path,
        )

    message = str(excinfo.value)
    assert f"sha256:{IMAGE_ID}" in message
    assert f"sha256:{REGISTRY_DIGEST}" in message
    assert envs_mod.get("cellpose", tmp_path) is None


@workflow(
    purpose="BYO register-env of a locally built local/ image (the demo's "
            "shape), which has no RepoDigests, records its image ID and is "
            "never pulled; Docker is handed the bare image ID"
)
def test_register_env_byo_local_image_records_image_id(tmp_path, monkeypatch):
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    stub_docker_image_inspect(monkeypatch, f"sha256:{IMAGE_ID}")
    stub_docker_repo_digest(
        monkeypatch,
        AssertionError("a local/ image has no registry digest to read"),
    )
    stub_docker_pull(monkeypatch, AssertionError("wfc never pulls local/"))

    record = envs_mod.register(
        name="demo-env",
        backend="byo",
        source={"image": "docker://local/wfc-demo-env:latest"},
        project_dir=tmp_path,
    )

    assert record.container == f"docker://local/wfc-demo-env@sha256:{IMAGE_ID}"
    assert envs_mod.daemon_ref(record.container) == f"sha256:{IMAGE_ID}"


def test_register_env_byo_missing_local_image_is_refused_not_pulled(
    tmp_path, monkeypatch,
):
    """A local/ image missing from the daemon is refused by name; no pull."""
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()

    stub_docker_image_inspect(monkeypatch,
                              RuntimeError("Error: No such image"))
    stub_docker_pull(monkeypatch, AssertionError("wfc never pulls local/"))

    with pytest.raises(RuntimeError, match="local/wfc-demo-env:latest"):
        envs_mod.register(
            name="demo-env",
            backend="byo",
            source={"image": "docker://local/wfc-demo-env:latest"},
            project_dir=tmp_path,
        )
    assert envs_mod.get("demo-env", tmp_path) is None


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


# --- repo_digest: choosing the RepoDigests entry (Tier 1) -------------------


def _repo_digests(monkeypatch, stdout: str, *, returncode: int = 0) -> list:
    """Fake ``docker image inspect`` answering *stdout*; return the argvs."""
    calls: list = []
    answer = canned_process(returncode=returncode, stdout=stdout,
                            stderr="Error: No such image")

    def handler(cmd, *args, **kwargs):
        calls.append(cmd)
        return answer(cmd, *args, **kwargs)

    fake_subprocess_run(monkeypatch, handler, only="docker")
    return calls


@pytest.mark.parametrize("repository, entries, expected", [
    # The entry for the image's own repository, among others.
    ("ghcr.io/org/tool",
     ["ghcr.io/org/other@sha256:" + "1" * 64,
      "ghcr.io/org/tool@sha256:" + "2" * 64],
     "sha256:" + "2" * 64),
    # Docker Hub repositories are listed in their familiar form.
    ("rocker/r-ver", ["rocker/r-ver@sha256:" + "3" * 64], "sha256:" + "3" * 64),
    ("docker.io/rocker/r-ver", ["rocker/r-ver@sha256:" + "4" * 64],
     "sha256:" + "4" * 64),
    ("library/alpine", ["alpine@sha256:" + "5" * 64], "sha256:" + "5" * 64),
    ("docker.io/library/alpine", ["alpine@sha256:" + "6" * 64],
     "sha256:" + "6" * 64),
])
def test_repo_digest_chooses_the_entry_for_the_repository(
    monkeypatch, repository, entries, expected,
):
    from wfc.environments import docker as docker_runner

    calls = _repo_digests(monkeypatch, json.dumps(entries) + "\n")

    assert docker_runner.repo_digest("some-ref:tag", repository) == expected
    assert calls[0][:4] == ["docker", "image", "inspect", "some-ref:tag"]


@pytest.mark.parametrize("stdout", [
    "[]\n",
    "null\n",
    '["ghcr.io/org/other@sha256:' + "1" * 64 + '"]\n',
])
def test_repo_digest_without_a_matching_entry_names_the_image(monkeypatch, stdout):
    from wfc.environments import docker as docker_runner

    _repo_digests(monkeypatch, stdout)

    with pytest.raises(RuntimeError, match="ghcr.io/org/tool"):
        docker_runner.repo_digest("ghcr.io/org/tool:v1", "ghcr.io/org/tool")


def test_repo_digest_surfaces_docker_errors(monkeypatch):
    from wfc.environments import docker as docker_runner

    _repo_digests(monkeypatch, "", returncode=1)

    with pytest.raises(RuntimeError, match="No such image"):
        docker_runner.repo_digest("reg/img:v1", "reg/img")
