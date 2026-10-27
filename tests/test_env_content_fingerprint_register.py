"""Subsystem test: register records the image's content, not its name.

Two byo registrations of images that differ in name, registry and
registry digest, but whose content inspect reports the same layers, Env,
User and WorkingDir, record one ``env_fingerprint``. The content inspect
reads no names, tags or labels, so a relabelled or re-pushed copy of an
image is the same env.
"""

from __future__ import annotations

import hashlib

from axiom_annotations import workflow

from tests.fixtures.fakes import (
    FAKE_IMAGE_CONTENT,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_repo_digest,
)


@workflow(
    purpose="Two byo registrations through register, of images under "
            "different names, registries and digests whose content inspect "
            "reports identical layers and config, record the same "
            "env_fingerprint: the md5 of the content blob, inspected on the "
            "ref Docker starts each env from"
)
def test_identical_content_under_different_names_records_one_fingerprint(
        tmp_project, monkeypatch):
    from wfc import environments as envs_mod
    from wfc.environments.fingerprint import capture_env_content

    digests = {"ghcr.io/lab/tools": "a" * 64, "quay.io/other/renamed": "b" * 64}
    content_refs: list[str] = []

    def content_of(ref):
        content_refs.append(ref)
        return dict(FAKE_IMAGE_CONTENT)

    stub_docker_image_inspect(monkeypatch, "sha256:" + "f" * 64)
    stub_docker_repo_digest(
        monkeypatch, lambda ref, repository: f"sha256:{digests[repository]}")
    stub_docker_pull(monkeypatch)
    stub_docker_image_content(monkeypatch, content_of)

    first = envs_mod.register(
        name="tools", backend="byo",
        source={"image": "docker://ghcr.io/lab/tools:1.0"},
        project_dir=tmp_project,
    )
    second = envs_mod.register(
        name="renamed", backend="byo",
        source={"image": "docker://quay.io/other/renamed:latest"},
        project_dir=tmp_project,
    )

    expected = hashlib.md5(
        capture_env_content(FAKE_IMAGE_CONTENT).encode("utf-8")).hexdigest()
    assert first.env_fingerprint == second.env_fingerprint == expected
    assert first.container != second.container
    # Each record step inspected the digest-pinned ref its env runs from.
    assert content_refs == [
        f"ghcr.io/lab/tools@sha256:{'a' * 64}",
        f"quay.io/other/renamed@sha256:{'b' * 64}",
    ]
