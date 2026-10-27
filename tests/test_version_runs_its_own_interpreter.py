"""A method version runs with the interpreter of the image it recorded.

The env is registered through production ``register`` with the Docker
boundary stubbed, re-registered under another backend, and the method is
registered against each image. A node pinned to the older version is driven
through production ``run_step``; only the container launch is substituted
(the harness's stub rung), so the docker argv dispatch built is observed.
"""

from __future__ import annotations

import json
import subprocess

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import stub_docker_build, stub_docker_image_content, stub_docker_image_inspect
from tests.harness import Scenario, build_project, drive_target, node, selector, wire

ENV = "image-io"
PIXI_DIGEST = "a" * 64
CONDA_DIGEST = "c" * 64
PIXI_PYTHON = f"/opt/.pixi/envs/{ENV}/bin/python"
CONDA_PYTHON = "/opt/conda/bin/python"

PIXI_LOCK = "version: 6\nenvironments:\n  image-io:\n    packages: {}\n"
PIXI_TOML = ('[project]\nname = "image-io"\n'
             'channels = ["conda-forge"]\nplatforms = ["linux-64"]\n')
EXPLICIT_LIST = "@EXPLICIT\nhttps://x/python-3.11.0-h.conda#0a\n"
PIP_FREEZE = "numpy==1.26.4\n"


def _register_env(project_root, monkeypatch, backend: str, digest: str):
    """Register ENV under *backend* through production, built as *digest*."""
    from wfc import environments as envs_mod

    source = ({"pixi_lock_content": PIXI_LOCK, "pixi_toml_content": PIXI_TOML,
               "pip_freeze_content": PIP_FREEZE} if backend == "pixi"
              else {"explicit_list_content": EXPLICIT_LIST,
                    "pip_freeze_content": PIP_FREEZE})
    stub_docker_build(monkeypatch, None)
    stub_docker_image_content(monkeypatch,
                              lambda ref: {"layers": [f"layer-of-{ref}"]})
    stub_docker_image_inspect(monkeypatch, f"sha256:{digest}")
    return envs_mod.register(name=ENV, backend=backend, source=source,
                             project_dir=project_root, force=True)


def _register_method(project, node_id: str):
    """Register the node's method again through production; return its newest entry."""
    from tests.harness.project import method_source_dir
    from wfc.registration import register_method, version_entry

    spec = project.scenario.node_by_id(node_id)
    register_method(method_dir=method_source_dir(project.root, spec),
                    module_name=spec.module, assume_yes=True)
    return version_entry(spec.module, spec.method_name)


@workflow(purpose="A method version registered against an env's pixi image "
                  "runs, pinned, with the pixi interpreter after the env is "
                  "re-registered as conda and the method registered again: "
                  "dispatch takes the interpreter from the version's own "
                  "image, never from the env's current record")
def test_pinned_version_runs_the_interpreter_of_its_own_image(
    git_project, monkeypatch,
):
    pid = "pipe-own-interpreter"
    project = build_project(
        Scenario(nodes=[selector(), node("a", inputs=[wire("sel")])],
                 samples=["S1"], pipeline_id=pid, env_name=ENV,
                 env_backend="pixi"),
        root=git_project, monkeypatch=monkeypatch)
    root = project.root

    口 = Step(step_num=1, name="Register env E as pixi and method M (v1)",
             purpose="The method's version records the pixi image")
    _register_env(root, monkeypatch, "pixi", PIXI_DIGEST)
    v1 = _register_method(project, "a")
    assert v1.image_ref.endswith(f"sha256:{PIXI_DIGEST}")

    口 = Step(step_num=2, name="Re-register E as conda and M again (v2)",
             purpose="The env's current record and the newest version now "
                     "name the conda image and its interpreter")
    _register_env(root, monkeypatch, "conda", CONDA_DIGEST)
    v2 = _register_method(project, "a")
    assert v2.identity != v1.identity
    assert v2.image_ref.endswith(f"sha256:{CONDA_DIGEST}")

    口 = Step(step_num=3, name="Pin the node to v1 and run it",
             purpose="The pipeline document pins v1; the target runs through "
                     "production run_step with the container launch stubbed")
    document = json.loads(project.pipeline_json.read_text())
    next(n for n in document["nodes"] if n["id"] == "a")["version"] = v1.identity
    project.pipeline_json.write_text(json.dumps(document, indent=2))
    # The user commits the pinned document, as the run's clean-tree check asks.
    subprocess.run(["git", "commit", "-q", "-am", "pin a to v1"], cwd=root,
                   check=True, capture_output=True)
    obs = drive_target(project, "a", monkeypatch=monkeypatch)

    口 = Step(step_num=4, name="The argv carries v1's image and interpreter",
             purpose="The docker argv names the pixi image and runs the "
                     "script under the pixi interpreter, not the conda one")
    cmd = obs.dispatch_cmd(("a", "S1", "default"))
    assert f"sha256:{PIXI_DIGEST}" in cmd
    assert PIXI_PYTHON in cmd
    assert CONDA_PYTHON not in cmd
