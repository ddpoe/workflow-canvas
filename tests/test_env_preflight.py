"""Execution's env pre-flights: a lost image is pulled and checked, or refused, before any claim.

``run_pipeline`` asks Environments' ``ensure_version_runnable`` once per
distinct image its nodes run, before the launch; a standalone ``run-step``
asks for its one image before Claim; a ``run-step`` the engine invokes
(pipeline id set) never asks. Envs are registered (and pushed) through
production ``register`` and ``push_image`` with the Docker boundary stubbed.
The daemon's contents are simulated at ``image_inspect`` and
``image_content``: a probe of an image the daemon holds answers, any other
misses, and a ``pull`` makes the pulled ref held. ``build`` is armed to fail
the test: nothing is ever built at run time.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    fake_engine_process,
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_push,
    stub_docker_tag,
)
from tests.harness import Phase, Scenario, build_project, drive_target, node, selector, wire
from wfc.environments.docker import ImageNotFoundError

OLD_DIGEST = "a" * 64
PULLED_ID = "f" * 64
# Neither the harness's stub image ("c" * 64) nor the fakes' pushed digest.
PRESENT_DIGEST = "e" * 64
ENV = "image-io"
PRESENT_ENV = "present-env"
BASE = "ghcr.io/owner/repo"
REGISTRY_REF = f"{BASE}/{ENV}@{FAKE_PUSHED_DIGEST}"

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
    ``register --force`` builds it again the way ``wfc register-env`` does,
    so the record, the captures and the commit are production's.
    """
    from wfc import environments as envs_mod

    stub_docker_build(monkeypatch, None)
    stub_docker_image_content(monkeypatch, lambda ref: {"layers": [f"layer-of-{ref}"]})
    stub_docker_image_inspect(monkeypatch, f"sha256:{OLD_DIGEST}")
    envs_mod.register(
        name=ENV, backend="pixi",
        source={"pixi_lock_content": PIXI_LOCK,
                "pixi_toml_content": PIXI_TOML,
                "pip_freeze_content": PIP_FREEZE},
        project_dir=project, force=True,
    )
    if present_env:
        stub_docker_image_content(monkeypatch, lambda ref: {"layers": [f"layer-of-{ref}"]})
        stub_docker_image_inspect(monkeypatch, f"sha256:{PRESENT_DIGEST}")
        envs_mod.register(name=PRESENT_ENV, backend="byo",
                          source={"image": "docker://local/present:latest"},
                          project_dir=project)


def _register_methods_again(project, node_ids) -> None:
    """Register the nodes' methods again through production.

    After ``register-env --force`` a method's newest version still records
    the env's earlier image; registering it again makes a version that runs
    the env's current one, as the user is told to.
    """
    from tests.harness.project import method_source_dir
    from wfc.registration import register_method

    for nid in node_ids:
        spec = project.scenario.node_by_id(nid)
        register_method(method_dir=method_source_dir(project.root, spec),
                        module_name=spec.module, assume_yes=True)


def _push_env(root: Path, monkeypatch) -> None:
    """Set the project's base and push ENV's current image through production.

    ``push_image`` writes ``pushed.json`` into the image's captures folder;
    it and the config edit are committed, as ``wfc push-env`` commits them.
    """
    from wfc.environments import get, push_image
    from wfc.persistence import set_registry_url

    set_registry_url(root, BASE)
    stub_docker_tag(monkeypatch, None)
    stub_docker_push(monkeypatch, None)
    record = get(ENV, root)
    push_image(root, ENV, record.env_fingerprint, record.container, BASE)
    _git(root, "add", ".wfc")
    _git(root, "commit", "-q", "-m", "push env images")


#: What the daemon reports for ENV's image as registered (its content is
#: keyed on the image ID inspected at register).
REGISTERED_CONTENT = {"layers": [f"layer-of-sha256:{OLD_DIGEST}"]}


def _daemon(monkeypatch, *, pulls: list, probes: list, pulled_content=None,
            pull_error: Exception | None = None) -> set:
    """Simulate a daemon that lost ENV's image but holds PRESENT_ENV's.

    A ``pull`` records its ref and makes it held (or raises *pull_error*);
    a pulled image answers inspect with PULLED_ID, another ID than the
    record's, and *pulled_content* as its content (default: the registered
    image's content).
    """
    held = {f"sha256:{PRESENT_DIGEST}"}

    def inspect(ref):
        probes.append(ref)
        if ref not in held:
            raise ImageNotFoundError(f"Error: No such image: {ref}")
        return ref if ref.startswith("sha256:") else f"sha256:{PULLED_ID}"

    def pull(ref):
        pulls.append(ref)
        if pull_error is not None:
            raise pull_error
        held.add(ref)

    def content(ref):
        if ref not in held:
            raise ImageNotFoundError(f"Error: No such image: {ref}")
        return pulled_content or REGISTERED_CONTENT

    stub_docker_image_content(monkeypatch, content)
    stub_docker_image_inspect(monkeypatch, inspect)
    stub_docker_build(monkeypatch, AssertionError("built at run time"))
    stub_docker_pull(monkeypatch, pull)
    return held


def _pipeline_scenario(pid: str) -> Scenario:
    """A -> b -> c; a and b run in ENV (lost), c in PRESENT_ENV (held)."""
    return Scenario(
        nodes=[
            selector(),
            node("a", inputs=[wire("sel")]),
            node("b", inputs=[wire("a")]),
            node("c", inputs=[wire("b")]),
        ],
        samples=["S1"],
        pipeline_id=pid,
        env_name=ENV,
        env_backend="pixi",
    )


def _pipeline_project(pid: str, git_project, monkeypatch):
    """Build the a -> b -> c project with every method's newest version current.

    Nodes a and b run in a registered pixi env and are registered again
    after it, so their newest versions record its image; c's method is
    registered again in a registered byo env. Every commit is production's.
    """
    from tests.harness.project import method_source_dir
    from wfc.registration import register_method

    project = build_project(_pipeline_scenario(pid), root=git_project,
                            monkeypatch=monkeypatch)
    root = project.root
    _register_envs(root, monkeypatch, present_env=True)
    _register_methods_again(project, ("a", "b"))
    c = project.scenario.node_by_id("c")
    c_source = method_source_dir(root, c)
    c_yaml = c_source / "method.yaml"
    c_yaml.write_text(c_yaml.read_text(encoding="utf-8")
                      .replace(f"env: {ENV}\n", f"env: {PRESENT_ENV}\n"),
                      encoding="utf-8")
    register_method(method_dir=c_source, module_name=c.module, assume_yes=True)
    return project


@workflow(purpose="At the pipeline: run_pipeline over two nodes sharing "
                  "a pushed pixi env the Docker daemon lost and one node on a "
                  "present env pulls the lost image once, before the launch, by "
                  "its digest-pinned registry ref, and checks its content: "
                  "nothing is built or committed, HEAD is unchanged on a clean "
                  "tree, and each node's dispatch hands Docker the registry ref "
                  "(the lost env) or the held image (the present env)")
def test_run_pipeline_pulls_a_lost_pushed_image_once_before_the_launch(
    git_project, monkeypatch,
):
    from wfc.environments import get, version_run_ref
    from wfc.execution import run_pipeline
    from wfc.registration import version_entry

    pid = "pipe-env-preflight"
    口 = Step(step_num=1, name="Build the project, register and push its envs",
             purpose="Nodes a and b run in a registered pixi env whose image "
                     "is pushed; c's method is registered again in a "
                     "registered byo env; every commit is production's")
    project = _pipeline_project(pid, git_project, monkeypatch)
    root = project.root
    _push_env(root, monkeypatch)
    record_before = get(ENV, root)
    head_before = _git(root, "rev-parse", "HEAD").strip()

    口 = Step(step_num=2, name="The daemon loses the pixi env's image",
             purpose="Probes of the registered image miss; the byo image is "
                     "held; a pull makes the registry ref held")
    pulls: list = []
    probes: list = []
    held = _daemon(monkeypatch, pulls=pulls, probes=probes)

    口 = Step(step_num=3, name="Run the pipeline with the engine spawn stubbed",
             purpose="At the spawn, record the pulls, the commit, the tree and "
                     "the ref each node's dispatch hands Docker")
    at_spawn: dict = {}

    def launch(argv, **kwargs):
        at_spawn["pulls"] = list(pulls)
        at_spawn["head"] = _git(root, "rev-parse", "HEAD").strip()
        at_spawn["status"] = _git(root, "status", "--porcelain").strip()
        refs = {}
        for nid in ("a", "b", "c"):
            spec = project.scenario.node_by_id(nid)
            entry = version_entry(spec.module, spec.method_name)
            refs[nid] = version_run_ref(entry.env_name, entry.env_fingerprint,
                                        root, image=entry.image_ref)
        at_spawn["refs"] = refs

    with fake_engine_process(launch=launch, only_snakemake=True):
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(root), pipeline_id=pid)

    口 = Step(step_num=4, name="One pull before the launch, nothing recorded",
             purpose="The shared image was pulled once by its registry ref; "
                     "the launch saw the same HEAD on a clean tree; a and b "
                     "run the registry ref, c its held image")
    assert pulls == [REGISTRY_REF]
    assert at_spawn["pulls"] == [REGISTRY_REF]
    assert at_spawn["head"] == head_before
    assert at_spawn["status"] == ""
    assert get(ENV, root) == record_before
    assert at_spawn["refs"] == {"a": REGISTRY_REF, "b": REGISTRY_REF,
                                "c": f"sha256:{PRESENT_DIGEST}"}
    assert set(at_spawn["refs"].values()) <= held
    assert f"sha256:{PRESENT_DIGEST}" in probes


def _refused_before_any_run(project, pid: str, *, pulls: list,
                            pulled: list | None = None) -> str:
    """Run the pipeline expecting a pre-flight refusal; return the error text.

    Asserts the engine never started, *pulls* (the list the simulated
    daemon records pulls in) equals *pulled* (default: nothing pulled), and
    no run row or pipeline directory exists.
    """
    from wfc import layout
    from wfc.environments import EnvNotRunnableError
    from wfc.execution import run_pipeline
    from wfc.persistence import Run, get_session

    spawned: list = []
    with fake_engine_process(launch=lambda argv, **kw: spawned.append(argv),
                             only_snakemake=True):
        with pytest.raises(EnvNotRunnableError) as exc:
            run_pipeline(pipeline_path=str(project.pipeline_json),
                         project_root=str(project.root), pipeline_id=pid)
    assert spawned == []
    assert pulls == (pulled or [])
    assert not layout.pipeline_run_dir(project.root, pid).exists()
    with get_session() as session:
        assert session.exec(select(Run)).all() == []
    return str(exc.value)


@pytest.mark.parametrize("case", ["never-pushed", "pull-fails", "content-differs"])
@workflow(purpose="At the pipeline: run_pipeline refuses a node "
                  "whose version's image the daemon lost when it was never "
                  "pushed, when its pull fails (Docker's cause named), or when "
                  "the pulled content renders another env fingerprint; the "
                  "refusal names the version, `wfc register-env <env> --force` "
                  "then `wfc register-method`, before any run row, nothing is "
                  "built, and the engine never starts")
def test_run_pipeline_refuses_a_lost_image_it_cannot_pull_and_check(
    git_project, monkeypatch, case,
):
    from wfc.environments import get

    pid = f"pipe-env-{case}"
    project = _pipeline_project(pid, git_project, monkeypatch)
    if case != "never-pushed":
        _push_env(project.root, monkeypatch)
    fingerprint = get(ENV, project.root).env_fingerprint
    a = project.scenario.node_by_id("a")
    pulls: list = []
    cause = "unauthorized: authentication required"
    _daemon(monkeypatch, pulls=pulls, probes=[],
            pull_error=RuntimeError(cause) if case == "pull-fails" else None,
            pulled_content=({"layers": ["something-else"]}
                            if case == "content-differs" else None))

    message = _refused_before_any_run(
        project, pid, pulls=pulls,
        pulled=[] if case == "never-pushed" else [REGISTRY_REF])

    assert f"of method '{a.module}/{a.method_name}'" in message
    assert f"wfc register-env {ENV} --force" in message
    assert message.index("--force") < message.index("wfc register-method")
    if case == "never-pushed":
        assert "never pushed" in message
    if case == "pull-fails":
        assert cause in message
    if case == "content-differs":
        assert fingerprint in message


@workflow(purpose="run_pipeline refuses a node pinned to an older version "
                  "whose image the daemon no longer holds and that was never "
                  "pushed, even though the env's current image is held: the "
                  "rule answers for the version's own image, so nothing is "
                  "built or pulled; the refusal names the version and `wfc "
                  "register-method`, before any run row")
def test_run_pipeline_refuses_an_older_versions_lost_image(
    git_project, monkeypatch,
):
    from wfc.registration.method_version import read_version_list

    pid = "pipe-env-older-lost"
    project = _pipeline_project(pid, git_project, monkeypatch)
    a = project.scenario.node_by_id("a")
    v1 = read_version_list(project.root, a.module, a.method_name)[0]
    document = json.loads(project.pipeline_json.read_text())
    next(n for n in document["nodes"] if n["id"] == "a")["version"] = v1.identity
    project.pipeline_json.write_text(json.dumps(document, indent=2))
    pulls: list = []
    held = _daemon(monkeypatch, pulls=pulls, probes=[])
    # The env's current image (the newest versions') is held; v1's is not.
    held.add(f"sha256:{OLD_DIGEST}")
    assert v1.image_ref != f"docker://local/{ENV}@sha256:{OLD_DIGEST}"

    message = _refused_before_any_run(project, pid, pulls=pulls)

    assert f"Version v{v1.label} of method '{a.module}/{a.method_name}'" in message
    assert "wfc register-method" in message


def _remove_captures(root: Path, env: str) -> None:
    """Delete *env*'s captures folders and commit it, as a project whose
    versions were registered before wfc recorded captures holds none."""
    _git(root, "rm", "-r", "-q", f".wfc/captures/{env}")
    _git(root, "commit", "-q", "-m", "no captures for the env")


@workflow(purpose="run_pipeline refuses a node whose version's (env, "
                  "fingerprint) has no captures folder, before any pull, "
                  "run row or engine start: the message names the version, "
                  "the env and `wfc register-method` with the module and "
                  "method")
def test_run_pipeline_refuses_a_version_with_no_captures(
    git_project, monkeypatch,
):
    pid = "pipe-env-no-captures"
    project = _pipeline_project(pid, git_project, monkeypatch)
    a = project.scenario.node_by_id("a")
    from wfc.registration import version_entry
    newest = version_entry(a.module, a.method_name)
    _remove_captures(project.root, ENV)
    pulls: list = []
    _daemon(monkeypatch, pulls=pulls, probes=[])

    message = _refused_before_any_run(project, pid, pulls=pulls)

    assert (f"Version v{newest.label} of method "
            f"'{a.module}/{a.method_name}'") in message
    assert f"env '{ENV}'" in message
    assert "wfc register-method" in message
    assert f"module '{a.module}', method '{a.method_name}'" in message


@workflow(purpose="A standalone run-step whose version's (env, fingerprint) "
                  "has no captures folder is refused by its pre-flight before "
                  "Claim: exit 1, a message naming `wfc register-method`, no "
                  "run row, nothing pulled or built")
def test_standalone_run_step_refuses_a_version_with_no_captures(
    git_project, monkeypatch, capsys,
):
    import importlib

    from tests.fixtures.fakes import interpose_phases
    from tests.harness.observe import TargetRun
    from wfc.persistence import Run, get_session

    run_step_mod = importlib.import_module("wfc.execution.run_step")
    project = build_project(_step_scenario("unused"), root=git_project,
                            monkeypatch=monkeypatch)
    _register_envs(project.root, monkeypatch, present_env=False)
    _register_methods_again(project, ("clean",))
    _remove_captures(project.root, ENV)
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
    monkeypatch.delenv("WFC_PIPELINE_JSON", raising=False)
    pulls: list = []
    _daemon(monkeypatch, pulls=pulls, probes=[])

    record = TargetRun(target=("clean", "S1", "default"))
    capsys.readouterr()
    with interpose_phases(run_step_mod, record, Phase.CLAIM, monkeypatch):
        assert run_step_mod.run_step(
            node_id="clean", sample="S1",
            pipeline_json=str(project.pipeline_json)) == 1

    err = capsys.readouterr().err
    assert "wfc register-method" in err and f"env '{ENV}'" in err
    assert record.phases_ran == []
    assert pulls == []
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


@pytest.mark.parametrize("pulled", ["same-content", "other-content"])
@workflow(purpose="A standalone run-step (no pipeline id) whose version's "
                  "pushed image the daemon lost asks Environments before "
                  "Claim: the image is pulled once by its registry ref; "
                  "pulled content that renders the recorded env fingerprint "
                  "is claimed with that fingerprint, other content is refused "
                  "naming `wfc register-method` before any run row; nothing "
                  "is built")
def test_standalone_run_step_pulls_its_image_before_the_claim(
    git_project, monkeypatch, capsys, pulled,
):
    import importlib

    from wfc.persistence import Run, get_session
    from wfc.registration import version_entry

    # The module, not the package's re-exported function of the same name.
    run_step_mod = importlib.import_module("wfc.execution.run_step")

    from tests.fixtures.fakes import interpose_phases
    from tests.harness.drivers import _StopPhase
    from tests.harness.observe import TargetRun

    project = build_project(_step_scenario("unused"), root=git_project,
                            monkeypatch=monkeypatch)
    _register_envs(project.root, monkeypatch, present_env=False)
    _register_methods_again(project, ("clean",))
    _push_env(project.root, monkeypatch)
    spec = project.scenario.node_by_id("clean")
    newest = version_entry(spec.module, spec.method_name)
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
    monkeypatch.delenv("WFC_PIPELINE_JSON", raising=False)

    pulls: list = []
    _daemon(monkeypatch, pulls=pulls, probes=[],
            pulled_content=({"layers": ["something-else"]}
                            if pulled == "other-content" else None))
    # The CLI's standalone shape: no --pipeline-id and none in the
    # environment. The harness drivers always pass the scenario's id, so the
    # step is driven directly and stopped after Claim by the harness's spy.
    record = TargetRun(target=("clean", "S1", "default"))
    capsys.readouterr()
    with interpose_phases(run_step_mod, record, Phase.CLAIM, monkeypatch):
        if pulled == "same-content":
            with pytest.raises(_StopPhase):
                run_step_mod.run_step(node_id="clean", sample="S1",
                                      pipeline_json=str(project.pipeline_json))
        else:
            assert run_step_mod.run_step(
                node_id="clean", sample="S1",
                pipeline_json=str(project.pipeline_json)) == 1

    assert pulls == [REGISTRY_REF]
    if pulled == "other-content":
        assert "wfc register-method" in capsys.readouterr().err
        assert record.phases_ran == []
        with get_session() as session:
            assert session.exec(select(Run)).all() == []
        return
    assert record.phases_ran == ["claim"]
    with get_session() as session:
        row = session.get(Run, record.run_id)
        assert row.env_fingerprint == newest.env_fingerprint


@workflow(purpose="A run-step the engine invokes (pipeline id set) never "
                  "probes or pulls its image: run_pipeline's pre-flight owns "
                  "that, before the run's commit")
def test_engine_invoked_run_step_does_not_pull(git_project, monkeypatch):
    from wfc.environments import get

    from wfc.registration import version_entry

    project = build_project(_step_scenario("pipe-engine-step"),
                            root=git_project, monkeypatch=monkeypatch)
    _register_envs(project.root, monkeypatch, present_env=False)
    _register_methods_again(project, ("clean",))
    before = get(ENV, project.root)
    spec = project.scenario.node_by_id("clean")
    newest = version_entry(spec.module, spec.method_name)

    pulls: list = []
    probes: list = []
    _daemon(monkeypatch, pulls=pulls, probes=probes)
    obs = drive_target(project, "clean", monkeypatch=monkeypatch,
                       through=Phase.CLAIM)

    assert pulls == [] and probes == []
    assert get(ENV, project.root) == before
    row = obs.run_row(("clean", "S1", "default"))
    # The key is over the version's recorded env fingerprint.
    assert row["env_fingerprint"] == newest.env_fingerprint
