"""Execution's env pre-flights: a lost local image is rebuilt before any claim.

``run_pipeline`` asks Environments' ``ensure_runnable`` once per distinct env
its nodes run in, before the launch; a standalone ``run-step`` asks for its
one env before Claim; a ``run-step`` the engine invokes (pipeline id set)
never asks. Envs are registered through production ``register`` with the
Docker boundary stubbed, and the daemon's contents are simulated at
``image_inspect``: a probe of an image the daemon holds returns it, any other
misses, and the rebuild's own inspect of the build tag returns the new image
ID. ``pull`` is armed to record: wfc never pulls a ``local/`` image.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.fakes import (
    fake_engine_process,
    stub_docker_build,
    stub_docker_image_inspect,
    stub_docker_pull,
)
from tests.harness import Phase, Scenario, build_project, drive_target, node, selector, wire
from wfc.environments.docker import ImageNotFoundError

OLD_DIGEST = "a" * 64
NEW_DIGEST = "b" * 64
PRESENT_DIGEST = "d" * 64
ENV = "image-io"
PRESENT_ENV = "present-env"
BUILD_TAG = f"local/{ENV}:_wfc-build"

PIXI_LOCK = "version: 6\nenvironments:\n  image-io:\n    packages: {}\n"
PIXI_TOML = ('[project]\nname = "image-io"\n'
             'channels = ["conda-forge"]\nplatforms = ["linux-64"]\n')
PIP_FREEZE = "numpy==1.26.4\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _register_envs(project: Path, monkeypatch, *, present_env: bool) -> None:
    """Register the scenario's pixi env (and a byo env) through production.

    The harness wrote the scenario env's record without a build context;
    ``register --force`` rebuilds it the way ``wfc register-env`` does, so
    the record, the staged context and the commit are production's.
    """
    from wfc import environments as envs_mod

    stub_docker_build(monkeypatch, None)
    stub_docker_image_inspect(monkeypatch, f"sha256:{OLD_DIGEST}")
    envs_mod.register(
        name=ENV, backend="pixi",
        source={"pixi_lock_content": PIXI_LOCK,
                "pixi_toml_content": PIXI_TOML,
                "pip_freeze_content": PIP_FREEZE},
        project_dir=project, force=True,
    )
    if present_env:
        stub_docker_image_inspect(monkeypatch, f"sha256:{PRESENT_DIGEST}")
        envs_mod.register(name=PRESENT_ENV, backend="byo",
                          source={"image": "docker://local/present:latest"},
                          project_dir=project)


def _daemon(monkeypatch, *, builds: list, pulls: list, probes: list) -> set:
    """Simulate a daemon that lost ENV's image but holds PRESENT_ENV's."""
    held = {f"sha256:{PRESENT_DIGEST}"}

    def inspect(ref):
        probes.append(ref)
        if ref == BUILD_TAG and builds:
            held.add(f"sha256:{NEW_DIGEST}")
            return f"sha256:{NEW_DIGEST}"
        if ref in held:
            return ref
        raise ImageNotFoundError(f"Error: No such image: {ref}")

    stub_docker_image_inspect(monkeypatch, inspect)
    stub_docker_build(monkeypatch,
                      lambda build_dir, tag: builds.append((Path(build_dir), tag)))
    stub_docker_pull(monkeypatch, lambda ref: pulls.append(ref))
    return held


def _pipeline_scenario(pid: str) -> Scenario:
    """A -> b -> c; a and b run in ENV (lost), c in PRESENT_ENV (held)."""
    return Scenario(
        nodes=[
            selector(),
            node("a", inputs=[wire("sel")]),
            node("b", inputs=[wire("a")]),
            node("c", inputs=[wire("b")], document_env=PRESENT_ENV),
        ],
        samples=["S1"],
        pipeline_id=pid,
        env_name=ENV,
        env_backend="pixi",
    )


@workflow(purpose="run_pipeline over two nodes sharing a pixi env the Docker "
                  "daemon lost and one node on a present env rebuilds the lost "
                  "env once, before the launch: when the engine starts, HEAD "
                  "is the rebuild's manifest commit on a clean tree (what every "
                  "node's claim records), and the image each node's dispatch "
                  "will hand Docker is one the daemon holds")
def test_run_pipeline_rebuilds_a_lost_env_once_before_the_launch(
    git_project, monkeypatch,
):
    from wfc import layout
    from wfc.environments import daemon_ref, get
    from wfc.execution import run_pipeline
    from wfc.execution.node_env import document_node_env, resolve_node_env

    pid = "pipe-env-preflight"
    口 = Step(step_num=1, name="Build the project and register its envs",
             purpose="Nodes a and b run in a registered pixi env, c in a "
                     "registered byo env; every commit is production's")
    project = build_project(_pipeline_scenario(pid), root=git_project,
                            monkeypatch=monkeypatch)
    root = project.root
    _register_envs(root, monkeypatch, present_env=True)
    head_before = _git(root, "rev-parse", "HEAD").strip()

    口 = Step(step_num=2, name="The daemon loses the pixi env's image",
             purpose="Probes of the old image miss; the byo image is held")
    builds: list = []
    pulls: list = []
    probes: list = []
    held = _daemon(monkeypatch, builds=builds, pulls=pulls, probes=probes)

    口 = Step(step_num=3, name="Run the pipeline with the engine spawn stubbed",
             purpose="At the spawn, record the commit, the tree and the image "
                     "each node's dispatch resolves")
    at_spawn: dict = {}

    def launch(argv, **kwargs):
        frozen = json.loads(layout.pipeline_doc_path(root, pid)
                            .read_text(encoding="utf-8"))
        at_spawn["builds"] = len(builds)
        at_spawn["head_parent"] = _git(root, "rev-parse", "HEAD~1").strip()
        at_spawn["committed"] = json.loads(
            _git(root, "show", "HEAD:.wfc/envs.json"))["envs"][ENV]["container"]
        at_spawn["status"] = _git(root, "status", "--porcelain").strip()
        at_spawn["refs"] = {
            nid: daemon_ref(resolve_node_env(
                document_node_env(frozen, nid), root).record.container)
            for nid in ("a", "b", "c")
        }

    with fake_engine_process(launch=launch, only_snakemake=True):
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(root), pipeline_id=pid)

    口 = Step(step_num=4, name="One rebuild, committed before the launch",
             purpose="The shared env was built once; the launch saw its "
                     "commit on a clean tree; every node's image is held; "
                     "nothing was pulled")
    assert builds == [(root / ".wfc" / "build" / ENV, BUILD_TAG)]
    assert pulls == []
    rebuilt = f"docker://local/{ENV}@sha256:{NEW_DIGEST}"
    assert get(ENV, root).container == rebuilt
    assert at_spawn["builds"] == 1
    assert at_spawn["head_parent"] == head_before
    assert at_spawn["committed"] == rebuilt
    assert at_spawn["status"] == ""
    assert at_spawn["refs"] == {"a": f"sha256:{NEW_DIGEST}",
                                "b": f"sha256:{NEW_DIGEST}",
                                "c": f"sha256:{PRESENT_DIGEST}"}
    assert set(at_spawn["refs"].values()) <= held
    assert f"sha256:{PRESENT_DIGEST}" in probes


@workflow(purpose="run_pipeline refuses a pipeline whose lost pixi env cannot "
                  "be rebuilt (its build context is gone) before the launch: "
                  "the error names the env and `wfc register-env <name> "
                  "--force`, no run row or pipeline directory exists, and the "
                  "engine never starts")
def test_run_pipeline_refuses_an_env_it_cannot_rebuild_before_any_run_row(
    git_project, monkeypatch,
):
    from wfc import layout
    from wfc.environments import EnvNotRunnableError
    from wfc.execution import run_pipeline
    from wfc.persistence import Run, get_session

    pid = "pipe-env-refused"
    project = build_project(_pipeline_scenario(pid), root=git_project,
                            monkeypatch=monkeypatch)
    root = project.root
    _register_envs(root, monkeypatch, present_env=True)
    build_dir = root / ".wfc" / "build" / ENV
    for f in build_dir.iterdir():
        f.unlink()
    build_dir.rmdir()

    builds: list = []
    pulls: list = []
    _daemon(monkeypatch, builds=builds, pulls=pulls, probes=[])
    spawned: list = []
    with fake_engine_process(launch=lambda argv, **kw: spawned.append(argv),
                             only_snakemake=True):
        with pytest.raises(EnvNotRunnableError) as exc:
            run_pipeline(pipeline_path=str(project.pipeline_json),
                         project_root=str(root), pipeline_id=pid)

    assert ENV in str(exc.value)
    assert f"wfc register-env {ENV} --force" in str(exc.value)
    assert spawned == [] and builds == [] and pulls == []
    assert not layout.pipeline_run_dir(root, pid).exists()
    with get_session() as session:
        assert session.exec(select(Run)).all() == []


def _step_scenario(pid: str | None) -> Scenario:
    return Scenario(
        nodes=[selector(), node("clean", inputs=[wire("sel")])],
        samples=["S1"],
        pipeline_id=pid,
        env_name=ENV,
        env_backend="pixi",
    )


@workflow(purpose="A standalone run-step (no pipeline id) whose pixi env the "
                  "daemon lost asks ensure_runnable before Claim: the env is "
                  "rebuilt once and the claimed run records the rebuilt env's "
                  "fingerprint")
def test_standalone_run_step_rebuilds_its_env_before_the_claim(
    git_project, monkeypatch,
):
    import importlib

    from wfc.environments import get
    from wfc.persistence import Run, get_session

    # The module, not the package's re-exported function of the same name.
    run_step_mod = importlib.import_module("wfc.execution.run_step")

    from tests.fixtures.fakes import interpose_phases
    from tests.harness.drivers import _StopPhase
    from tests.harness.observe import TargetRun

    project = build_project(_step_scenario("unused"), root=git_project,
                            monkeypatch=monkeypatch)
    _register_envs(project.root, monkeypatch, present_env=False)
    old_fingerprint = get(ENV, project.root).env_fingerprint
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
    monkeypatch.delenv("WFC_PIPELINE_JSON", raising=False)

    builds: list = []
    pulls: list = []
    _daemon(monkeypatch, builds=builds, pulls=pulls, probes=[])
    # The CLI's standalone shape: no --pipeline-id and none in the
    # environment. The harness drivers always pass the scenario's id, so the
    # step is driven directly and stopped after Claim by the harness's spy.
    record = TargetRun(target=("clean", "S1", "default"))
    with interpose_phases(run_step_mod, record, Phase.CLAIM, monkeypatch):
        with pytest.raises(_StopPhase):
            run_step_mod.run_step(node_id="clean", sample="S1",
                                  pipeline_json=str(project.pipeline_json))

    rebuilt = get(ENV, project.root)
    assert builds == [(project.root / ".wfc" / "build" / ENV, BUILD_TAG)]
    assert pulls == []
    assert rebuilt.env_fingerprint != old_fingerprint
    assert record.phases_ran == ["claim"]
    with get_session() as session:
        row = session.get(Run, record.run_id)
        assert row.env_fingerprint == rebuilt.env_fingerprint


@workflow(purpose="A run-step the engine invokes (pipeline id set) never "
                  "probes or rebuilds its env: run_pipeline's pre-flight owns "
                  "that, before the run's commit")
def test_engine_invoked_run_step_does_not_rebuild(git_project, monkeypatch):
    from wfc.environments import get

    project = build_project(_step_scenario("pipe-engine-step"),
                            root=git_project, monkeypatch=monkeypatch)
    _register_envs(project.root, monkeypatch, present_env=False)
    before = get(ENV, project.root)

    builds: list = []
    pulls: list = []
    probes: list = []
    _daemon(monkeypatch, builds=builds, pulls=pulls, probes=probes)
    obs = drive_target(project, "clean", monkeypatch=monkeypatch,
                       through=Phase.CLAIM)

    assert builds == [] and pulls == [] and probes == []
    assert get(ENV, project.root) == before
    row = obs.run_row(("clean", "S1", "default"))
    assert row["env_fingerprint"] == before.env_fingerprint
