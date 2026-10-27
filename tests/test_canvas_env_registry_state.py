"""The canvas says where each env's image lives.

``GET /api/registry/envs`` gives each env row a ``registry`` object: the
current image's state (``pushed``, ``not_pushed``, ``none`` for no registry,
``missing``, or ``unknown`` when Docker does not answer), the base, the
count of older images method versions record that are not pushed, and the
count of versions whose older image is gone. The method Register dry run
ends with a pre-check saying where the image the new version records lives.
Neither asks the registry.

Projects come from the scenario harness (``build_project``: one env, two
methods whose versions are registered through production); further images
are registered through production ``register``, the base set through
``set_registry_url`` and pushes made by ``wfc push-env``. Docker and the
registry are faked at the ``wfc.environments.docker`` wrappers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_push,
    stub_docker_read_manifest,
    stub_docker_repo_digest,
    stub_docker_tag,
)
from tests.fixtures.routes import canvas_client
from tests.harness import Scenario, build_project, node, selector, wire
from wfc.environments.docker import ImageNotFoundError

ENV = "image-io"
BASE = "ghcr.io/lab/tools-project"
#: The harness's first image (a v1 and b v1 record it), one since removed
#: from this machine (b v2), and the current one (a v2).
OLDER, GONE, CURRENT = "c" * 64, "1" * 64, "3" * 64
#: A byo image recorded by its own registry's digest.
BYO_REF = f"ghcr.io/vendor/aligner@sha256:{'b' * 64}"
PIXI_LOCK = "version: 6\nenvironments:\n  image-io:\n    packages: {}\n"
PIXI_TOML = ('[project]\nname = "image-io"\n'
             'channels = ["conda-forge"]\nplatforms = ["linux-64"]\n')


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _never_the_registry(ref):
    raise AssertionError(f"the registry must not be asked ({ref})")


def _held(*image_ids: str):
    """A daemon inspect that holds exactly *image_ids*."""
    def inspect(ref: str) -> str:
        for image_id in image_ids:
            if image_id in ref:
                return f"sha256:{image_id}"
        raise ImageNotFoundError(f"Error: No such image: {ref}")
    return inspect


def _register_image(root: Path, monkeypatch, image_id: str) -> None:
    """Register ENV through production as the image *image_id*, unpushed."""
    from wfc import environments as envs_mod

    stub_docker_build(monkeypatch, lambda build_dir, tag, **kwargs: None)
    stub_docker_image_content(monkeypatch,
                              lambda ref: {"layers": [f"layer-of-{ref}"]})
    stub_docker_image_inspect(monkeypatch, f"sha256:{image_id}")
    envs_mod.register(
        name=ENV, backend="pixi", project_dir=root, force=True, push=False,
        source={"pixi_lock_content": PIXI_LOCK, "pixi_toml_content": PIXI_TOML,
                "pip_freeze_content": "numpy==1.26.4\n"})


def _register_method(project, node_id: str) -> None:
    from tests.harness.project import method_source_dir
    from wfc.registration import register_method

    spec = project.scenario.node_by_id(node_id)
    register_method(method_dir=method_source_dir(project.root, spec),
                    module_name=spec.module, assume_yes=True)


def _three_images(git_project, monkeypatch):
    """One env whose versions record three images: older, gone, current."""
    project = build_project(
        Scenario(nodes=[selector(), node("a", inputs=[wire("sel")]),
                        node("b", inputs=[wire("sel")])],
                 samples=["S1"], pipeline_id="pipe-registry-chip", env_name=ENV,
                 env_backend="pixi"),
        root=git_project, monkeypatch=monkeypatch)
    for image_id, node_id in ((GONE, "b"), (CURRENT, "a")):
        _register_image(project.root, monkeypatch, image_id)
        _register_method(project, node_id)
    return project


def _set_base(root: Path) -> None:
    from wfc.persistence import set_registry_url

    set_registry_url(root, BASE)
    _git(root, "add", "--", ".wfc/wf-canvas.toml")
    _git(root, "commit", "-q", "-m", "set the registry base")


def _chip(client) -> dict:
    rows = client.get("/api/registry/envs").json()["envs"]
    assert [row["spec"] for row in rows] == [ENV]
    return rows[0]["registry"]


@workflow(purpose="Each Envs row says where its images are "
                  "from the captures folders and the local daemon, never the "
                  "registry: no registry, unknown with Docker down, missing "
                  "with the image gone (older versions counted), not pushed "
                  "with a base set (an older image not pushed counted), and "
                  "pushed after wfc push-env, the gone version still counted; "
                  "a byo image named by its own registry, not the base")
def test_envs_rows_say_where_each_image_lives(git_project, monkeypatch):
    from wfc.cli import cli_main

    project = _three_images(git_project, monkeypatch)
    root = project.root
    stub_docker_read_manifest(monkeypatch, _never_the_registry)
    client = canvas_client(root, monkeypatch)

    口 = Step(step_num=1, name="No registry base",
             purpose="The current image is here: no registry; the gone "
                     "image's version is counted, nothing counts as unpushed")
    stub_docker_image_inspect(monkeypatch, _held(OLDER, CURRENT))
    assert _chip(client) == {"state": "none", "base": None, "older_not_pushed": 0,
                             "older_missing": 1,
                             "recreate": f"wfc register-env {ENV} --force",
                             "own_registry": None, "reason": None}

    口 = Step(step_num=2, name="Docker does not answer",
             purpose="The chip says unknown and counts nothing it cannot tell")
    stub_docker_image_inspect(monkeypatch, RuntimeError(
        "Cannot connect to the Docker daemon"))
    chip = _chip(client)
    assert (chip["state"], chip["older_missing"]) == ("unknown", 0)

    口 = Step(step_num=3, name="Every image gone from this machine",
             purpose="missing; the three versions recording older images "
                     "are counted")
    stub_docker_image_inspect(monkeypatch, _held())
    chip = _chip(client)
    assert (chip["state"], chip["older_missing"]) == ("missing", 3)

    口 = Step(step_num=4, name="A base set, nothing pushed",
             purpose="not pushed, naming the base; the older image still "
                     "here counts as not pushed")
    _set_base(root)
    stub_docker_image_inspect(monkeypatch, _held(OLDER, CURRENT))
    assert _chip(client) == {"state": "not_pushed", "base": BASE,
                             "older_not_pushed": 1, "older_missing": 1,
                             "recreate": f"wfc register-env {ENV} --force",
                             "own_registry": None, "reason": None}

    口 = Step(step_num=5, name="After wfc push-env --all",
             purpose="pushed with no daemon question needed; the gone "
                     "image's version is still counted")
    stub_docker_tag(monkeypatch)
    stub_docker_push(monkeypatch, FAKE_PUSHED_DIGEST)
    assert cli_main(["push-env", "--all", "--yes"]) == 1  # the gone image
    assert _chip(client) == {"state": "pushed", "base": BASE,
                             "older_not_pushed": 0, "older_missing": 1,
                             "recreate": f"wfc register-env {ENV} --force",
                             "own_registry": None, "reason": None}

    口 = Step(step_num=6, name="The env re-registered as a byo registry image",
             purpose="pushed, naming the image's own registry ref rather "
                     "than the project's base")
    from wfc import environments as envs_mod

    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, f"sha256:{'e' * 64}")
    stub_docker_repo_digest(monkeypatch, BYO_REF.split("@")[1])
    stub_docker_pull(monkeypatch, None)
    envs_mod.register(name=ENV, backend="byo", project_dir=root, force=True,
                      push=False, source={"image": f"docker://{BYO_REF}"})
    chip = _chip(client)
    assert (chip["state"], chip["base"], chip["own_registry"]) == (
        "pushed", BASE, BYO_REF)
