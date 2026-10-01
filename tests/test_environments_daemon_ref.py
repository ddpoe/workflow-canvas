"""Tier 2 test: the string Docker is handed for a recorded env.

A locally built env is recorded as ``docker://local/<name>@sha256:<image
ID>``. The ``name@sha256:`` slot means a registry digest, which only
Docker's containerd image store also resolves as an image ID; the classic
store pulls ``local/<name>`` and fails. The daemon ref therefore names a
local env by its bare image ID, ``sha256:<hex>``, and keeps a byo registry
image as ``<host>/<path>@sha256:<hex>``.
"""
from __future__ import annotations

import random
import re

from axiom_annotations import workflow

from tests.fixtures.conftest import write_env_record


_BARE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_REGISTRY_REF = re.compile(
    r"[a-z0-9][a-z0-9.\-]*(?::\d+)?/[a-z0-9][a-z0-9._/\-]*@sha256:[0-9a-f]{64}")


def _generated_records(rng: random.Random, count: int) -> list[dict]:
    """Generate env record specs across backends, names, hosts and digests.

    Args:
        rng: Seeded generator, so a failure reproduces.
        count: Number of specs to generate.

    Returns:
        Keyword dicts for ``write_env_record``, each with ``name``,
        ``backend``, ``digest`` and ``image`` (``None`` for the local repo
        production's pixi/conda build records).
    """
    hosts = ["ghcr.io", "docker.io", "quay.io", "registry.example.org:5000",
             "localhost:5000", "local.registry"]
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    specs = []
    for i in range(count):
        name = rng.choice("abcdefghijklmnopqrstuvwxyz") + "".join(
            rng.choice(alphabet + "-_") for _ in range(rng.randint(0, 12)))
        name = f"{name}{i}"
        digest = "".join(rng.choice("0123456789abcdef") for _ in range(64))
        backend = rng.choice(["pixi", "conda", "byo"])
        if backend == "byo" and rng.random() < 0.7:
            path = "/".join(
                "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8)))
                for _ in range(rng.randint(1, 3)))
            image = f"{rng.choice(hosts)}/{path}"
        else:
            # pixi/conda builds, and the demo's byo shape, live under local/.
            image = None
        specs.append({"name": name, "backend": backend, "digest": digest,
                      "image": image})
    return specs


@workflow(purpose="For generated local/ and byo env records, the string handed "
                  "to Docker is sha256:<hex> for a local env and "
                  "<host>/<path>@sha256:<hex> for a registry image, never "
                  "local/...@sha256:")
def test_docker_is_handed_the_image_id_or_the_registry_ref(tmp_path):
    from wfc.environments import daemon_ref
    from wfc.environments import get as env_record

    specs = _generated_records(random.Random(20260930), 60)
    for spec in specs:
        write_env_record(tmp_path, spec["name"], backend=spec["backend"],
                         digest=spec["digest"], image=spec["image"])

    seen_local = seen_registry = 0
    for spec in specs:
        record = env_record(spec["name"], tmp_path)
        handed = daemon_ref(record.container)

        assert not handed.startswith("local/"), handed
        assert not handed.startswith("docker://"), handed
        if spec["image"] is None:
            seen_local += 1
            assert _BARE_ID.fullmatch(handed), handed
            assert handed == f"sha256:{spec['digest']}"
        else:
            seen_registry += 1
            assert _REGISTRY_REF.fullmatch(handed), handed
            assert handed == f"{spec['image']}@sha256:{spec['digest']}"
    assert seen_local and seen_registry
