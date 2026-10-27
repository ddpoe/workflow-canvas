"""Tier 2/3 tests: wfc run-step container dispatch.

Covers container dispatch, the SLURM carve-out, and the
Snakefile-generator invariant (generator emits ``python -m wfc
run-step`` only — container wrapping happens inside run-step, not in the
generated shell line).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    stub_dev_loop_launch,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_push,
    stub_docker_tag,
)
from tests.harness import (
    STUB_DIGEST,
    Phase,
    Scenario,
    build_project,
    drive_target,
    node,
    run_target,
    selector,
    wire,
)

ENV_NAME = "image-io"
# The env is recorded as docker://local/<name>@sha256:<image ID>, the shape
# production's pixi local-build path records. Docker is handed its daemon
# ref: the bare image ID, which both image stores run and neither pulls.
DAEMON_REF = f"sha256:{STUB_DIGEST}"


def _dispatch_scenario(method_name: str, **node_fields) -> Scenario:
    """One selector-rooted method node bound to a pixi container env.

    The env record is a pixi record with no per-env ``python`` field, so
    dispatch must resolve the container-side interpreter via the per-backend
    default path rather than reading it off the record.

    Args:
        method_name: Method the node runs. Also its node id.
        **node_fields: Any :class:`~tests.harness.NodeSpec` field.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[selector(),
               node(method_name, module="test", inputs=[wire("sel")],
                    **node_fields)],
        samples=["s1"],
        env_name=ENV_NAME,
        env_backend="pixi",
        env_legacy_no_python=True,
    )


@workflow(purpose="run_step with container env dispatches via docker run --rm "
                  "--user with WFC_* env vars forwarded as -e flags")
def test_run_step_container_dispatch_docker_argv(git_project, monkeypatch):
    scn = _dispatch_scenario("ml_train")
    obs = run_target(scn, "ml_train", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.DISPATCH)

    target = ("ml_train", "s1", "default")
    assert obs.runs[target].dispatch_cmd is not None, (
        f"_run_method_subprocess was not called (rc={obs.exit_code(target)})"
    )
    cmd = obs.dispatch_cmd(target)
    # Docker run shape
    assert cmd[0] == "docker"
    assert cmd[1] == "run"
    assert "--rm" in cmd
    assert "--user" in cmd
    # The image is named by the daemon ref: the local env's bare image ID,
    # never local/<name>@sha256:, which the classic store pulls.
    assert DAEMON_REF in cmd
    assert not any(a.startswith(("local/", "docker://")) for a in cmd)
    # Inner argv runs the method script DIRECTLY under the env's resolved
    # interpreter — no `-m wfc`, no in-container wfc entrypoint at all. The
    # outer host run-step owns run-state; the image needs nothing
    # wfc-related. The env record
    # declares backend=pixi with no recorded `python`, so the resolver
    # falls back to the pixi per-backend default for env name "image-io".
    # The script is the version's extracted copy's, under /work at its own
    # project-relative path.
    from wfc.registration import resolve_method_code

    copy = resolve_method_code("test", "ml_train", git_project).code_dir
    copy_in_container = "/work/" + copy.relative_to(git_project).as_posix()
    image_idx = cmd.index(DAEMON_REF)
    inner = cmd[image_idx + 1:]
    assert inner == [
        "/opt/.pixi/envs/image-io/bin/python",
        f"{copy_in_container}/ml_train.py",
    ], f"inner argv must be [<env-python>, <script-in-container>]; got {inner!r}"
    assert "-m" not in cmd and "wfc" not in cmd, (
        "Inner argv must NOT dispatch through `python -m wfc` — registered "
        "env images contain nothing wfc-related."
    )
    assert "run-step" not in inner, (
        "Inner argv must NOT recursively call wfc run-step — that regenerates "
        "run_id and breaks host-side output collection."
    )
    # WFC_* env vars are forwarded via -e flags.
    flag_pairs = [(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1) if cmd[i] == "-e"]
    forwarded_keys = {pair[1].split("=", 1)[0] for pair in flag_pairs}
    assert "WFC_RUN_ID" in forwarded_keys
    assert "WFC_NODE_ID" in forwarded_keys
    assert "WFC_VARIANT" in forwarded_keys
    # PYTHONPATH is NOT forwarded into the container.
    assert "PYTHONPATH" not in forwarded_keys
    # No --gpus flag (method.yaml gpus=false default).
    assert "--gpus" not in cmd
    # Docker never fetches an image the pre-flight did not check.
    assert cmd[cmd.index("--pull") + 1] == "never"


@workflow(purpose="Through dispatch: the version's image was pushed and "
                  "pulled (the daemon lacks its local image ID and answers "
                  "another ID for the pulled registry ref); dispatch hands "
                  "Docker the registry ref, never a recomputed image ID, with "
                  "--pull never")
def test_dispatch_runs_a_pulled_images_registry_ref_with_pull_never(
        git_project, monkeypatch):
    import subprocess

    from wfc import environments as envs_mod
    from wfc.environments.docker import ImageNotFoundError
    from wfc.persistence import set_registry_url

    project = build_project(_registered_env_scenario(), root=git_project,
                            monkeypatch=monkeypatch)
    root = project.root
    target = ("a", "s1", "default")
    base = "ghcr.io/owner/repo"
    registry_ref = f"{base}/{ENV_NAME}@{FAKE_PUSHED_DIGEST}"
    pulled_id = "sha256:" + "f" * 64

    口 = Step(step_num=1, name="Push the env's image",
             purpose="push_image writes pushed.json; it and the base are "
                     "committed so the claim sees a clean tree")
    record = envs_mod.get(ENV_NAME, root)
    set_registry_url(root, base)
    stub_docker_tag(monkeypatch, None)
    stub_docker_push(monkeypatch, None)
    envs_mod.push_image(root, ENV_NAME, record.env_fingerprint,
                        record.container, base)
    subprocess.run(["git", "add", ".wfc"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "push env images"], cwd=root,
                   check=True, capture_output=True)

    口 = Step(step_num=2, name="The daemon holds only the pulled image",
             purpose="Its local image ID misses; the registry ref answers "
                     "another image ID, as the other image store does")

    def inspect(ref):
        if ref == registry_ref:
            return pulled_id
        raise ImageNotFoundError(f"Error: No such image: {ref}")

    stub_docker_image_inspect(monkeypatch, inspect)

    口 = Step(step_num=3, name="Dispatch the step",
             purpose="The argv names the registry ref and --pull never")
    obs = drive_target(project, target, monkeypatch=monkeypatch,
                       through=Phase.DISPATCH)
    cmd = obs.dispatch_cmd(target)
    assert cmd is not None, obs.runs[target].ending
    assert cmd[cmd.index("--pull") + 1] == "never"
    assert registry_ref in cmd
    assert DAEMON_REF not in cmd
    assert not any(pulled_id in a for a in cmd)




@workflow(purpose="For one recorded pixi env in one project, the argv dispatch "
                  "builds and the argv `wfc exec` builds carry the same --user, "
                  "the same binds and the same workdir, and differ only in the "
                  "flags each caller splices after --rm, in the inner command, "
                  "and in dispatch's read-only bind of the version's extracted "
                  "copy at its own /work path")
def test_dispatch_and_dev_loop_argv_agree_on_user_binds_and_workdir(
    git_project, monkeypatch,
):
    口 = Step(step_num=1, name="Dispatch a step in the recorded env",
             purpose="Run one target through the dispatch phase and capture "
                     "the docker argv dispatch hands the method process")
    obs = run_target(_dispatch_scenario("ml_train"), "ml_train",
                     root=git_project, monkeypatch=monkeypatch,
                     through=Phase.DISPATCH)
    dispatch_argv = obs.dispatch_cmd(("ml_train", "s1", "default"))

    口 = Step(step_num=2, name="Run wfc exec in the same env",
             purpose="Launch the dev loop's exec verb in the same project, "
                     "stubbed at its subprocess call, and capture its argv")
    from wfc.environments import dev_loop

    launched: list[list[str]] = []

    def fake_run_subprocess(argv):
        launched.append(list(argv))
        return 0

    stub_dev_loop_launch(monkeypatch, fake_run_subprocess)
    # The daemon holds the env's image, so the dev loop pulls nothing.
    stub_docker_image_inspect(monkeypatch, DAEMON_REF)
    assert dev_loop.exec_(ENV_NAME, ["python", "-c", "print(1)"]) == 0
    (exec_argv,) = launched

    口 = Step(step_num=3, name="Compare the two argvs",
             purpose="Split each argv at --rm, --user and the image ref: the "
                     "builder's --user, binds and workdir must match once "
                     "dispatch's read-only bind of the code copy is set aside, "
                     "so only that bind, the caller's spliced flags and the "
                     "inner command differ")
    from wfc import layout
    from wfc.registration import resolve_method_code

    def _parts(argv):
        """The ``docker run --rm`` prefix, and the builder's run options
        from ``--user`` up to the image ref.
        """
        image_idx = argv.index(DAEMON_REF)
        user_idx = argv.index("--user")
        return argv[:3], argv[user_idx:image_idx]

    dispatch_prefix, dispatch_body = _parts(dispatch_argv)
    exec_prefix, exec_body = _parts(exec_argv)
    assert dispatch_prefix == exec_prefix == ["docker", "run", "--rm"]
    assert {"-v", "-w"} <= set(dispatch_body)
    code = resolve_method_code("test", "ml_train", git_project)
    copy = layout.code_copy_dir(git_project, code.entry.tree_hash)
    assert copy == code.code_dir
    copy_bind = (f"{copy.as_posix()}:/work/"
                 f"{copy.relative_to(git_project).as_posix()}:ro")
    bind_idx = dispatch_body.index(copy_bind)
    assert dispatch_body[bind_idx - 1] == "-v"
    dispatch_body = dispatch_body[:bind_idx - 1] + dispatch_body[bind_idx + 1:]
    assert dispatch_body == exec_body, (
        f"dispatch and wfc exec disagree on --user / binds / workdir:\n"
        f"  dispatch: {dispatch_body}\n  exec:     {exec_body}"
    )


@workflow(purpose="method.yaml gpus: true plumbs through to docker --gpus all; "
                  "absent/false → no --gpus flag")
def test_run_step_gpu_flag_routing(git_project, monkeypatch):
    scn = _dispatch_scenario("ml_gpu", gpus=True)
    obs = run_target(scn, "ml_gpu", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.DISPATCH)

    target = ("ml_gpu", "s1", "default")
    assert obs.runs[target].dispatch_cmd is not None, (
        f"_run_method_subprocess was not called (rc={obs.exit_code(target)})"
    )
    cmd = obs.dispatch_cmd(target)
    assert "--gpus" in cmd
    assert cmd[cmd.index("--gpus") + 1] == "all"


@workflow(purpose="executor=slurm + container env → run_step exits non-zero "
                  "with 'cluster Apptainer dispatch ... is not supported' on "
                  "stderr")
def test_run_step_slurm_executor_carve_out_error(git_project, monkeypatch, capsys):
    scn = _dispatch_scenario("ml_cluster", executor="slurm")
    obs = run_target(scn, "ml_cluster", root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(("ml_cluster", "s1", "default")) == 1
    err = capsys.readouterr().err
    assert "cluster Apptainer dispatch (executor=slurm) is not supported" in err


def test_run_step_translates_wfc_paths_for_container(git_project, monkeypatch):
    """WFC_RUN_DIR and WFC_INPUT_PATHS are translated from host paths to
    container paths before being forwarded via ``-e`` flags. Host paths
    inside ``project_root`` rewrite to ``/work/...`` (POSIX); host paths
    inside the DVC cache rewrite to ``/dvc-cache/...``. Out-of-bounds
    paths are passed through unchanged.

    PYTHONPATH is never among the forwarded env vars.
    """
    scn = _dispatch_scenario("ml_paths")
    obs = run_target(scn, "ml_paths", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.DISPATCH)

    target = ("ml_paths", "s1", "default")
    assert obs.runs[target].dispatch_cmd is not None, (
        f"_run_method_subprocess was not called (rc={obs.exit_code(target)})"
    )
    cmd = obs.dispatch_cmd(target)
    flag_pairs = [(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1) if cmd[i] == "-e"]
    forwarded = {pair[1].split("=", 1)[0]: pair[1].split("=", 1)[1] for pair in flag_pairs}

    # WFC_RUN_DIR is rewritten to /work/... (the run_dir lives under
    # project_root at .wfc/runs/<run_id>/...).
    assert "WFC_RUN_DIR" in forwarded
    assert forwarded["WFC_RUN_DIR"].startswith("/work"), (
        f"WFC_RUN_DIR should be translated to /work/...; got {forwarded['WFC_RUN_DIR']!r}"
    )
    # No backslashes (POSIX form).
    assert "\\" not in forwarded["WFC_RUN_DIR"]

    # WFC_INPUT_PATHS is JSON; each path under project_root should be
    # rewritten to /work/... (the sample's data file lives under the project).
    assert "WFC_INPUT_PATHS" in forwarded
    decoded = json.loads(forwarded["WFC_INPUT_PATHS"])
    # Collect path-like leaf values regardless of dict vs list shape.
    leaves: list[str] = []
    if isinstance(decoded, dict):
        for v in decoded.values():
            if isinstance(v, list):
                leaves.extend(v)
            elif isinstance(v, str):
                leaves.append(v)
    elif isinstance(decoded, list):
        leaves = [p for p in decoded if isinstance(p, str)]
    assert leaves, f"expected at least one path leaf in WFC_INPUT_PATHS, got {decoded!r}"
    work_leaves = [p for p in leaves if p.startswith("/work")]
    assert work_leaves, (
        f"expected at least one WFC_INPUT_PATHS entry rewritten to /work/...; "
        f"got {leaves!r}"
    )
    # No backslashes anywhere in the translated payload.
    for p in leaves:
        assert "\\" not in p, f"path leaf still contains backslashes: {p!r}"

    # PYTHONPATH is absent from the forwarded -e flags.
    assert "PYTHONPATH" not in forwarded


def test_snakefile_generator_emits_python_dash_m_wfc_for_container_env(tmp_path):
    """Container-env methods get a plain ``python -m wfc run-step`` shell
    line. Container wrapping happens inside run-step, not in the generated
    Snakefile.
    """
    from wfc.graph import StepDef
    from wfc.orchestration.snakemake import _generate_rule

    step = StepDef(
        method_name="ml_train",
        module_name="test",
        params={},
        node_id="n1",
        env="image-io",   # container env name registered in .wfc/envs.json
    )
    lines = _generate_rule(step, {"n1": step}, pipeline_id="p1")
    shell_block = "\n".join(lines)
    assert "python -m wfc run-step" in shell_block or "{sys.executable} -m wfc run-step" in shell_block
    assert "docker run" not in shell_block
    assert "apptainer exec" not in shell_block


@pytest.mark.parametrize("given, expected", [
    pytest.param(lambda root, cache: str(root), "/work", id="equal-to-root"),
    pytest.param(lambda root, cache: str(cache), "/dvc-cache", id="equal-to-cache"),
    pytest.param(lambda root, cache: str(cache / "ab" / "cdef"), "/dvc-cache/ab/cdef",
                 id="under-cache"),
    pytest.param(lambda root, cache: str(root.parent / "elsewhere" / "x.csv"), None,
                 id="outside-both"),
    pytest.param(lambda root, cache: str(root) + "-sibling", None,
                 id="sibling-sharing-the-root-prefix"),
    pytest.param(lambda root, cache: "bad\x00path", None, id="unresolvable"),
    pytest.param(lambda root, cache: str(root / "data" / "x.csv"), "/work/data/x.csv",
                 id="native-separators-give-forward-slashes"),
])
def test_host_paths_translate_over_the_mount_table(tmp_path, given, expected):
    """Each branch of the host-to-container rewrite, asserted literally.

    Equal to a mounted root gives the root's container path, under it gives
    ``<container>/<rel>``, and anything else (including an input that cannot
    be resolved) comes back unchanged. The native-separator case is a drive
    letter with backslashes on a Windows host; the output is POSIX either way.
    """
    from wfc.layout.mounts import mount_table, translate_host_path

    root = tmp_path / "project"
    cache = tmp_path / "cache"
    root.mkdir()
    cache.mkdir()
    host_path = given(root, cache)

    translated = translate_host_path(host_path, mount_table(root, cache))

    assert translated == (host_path if expected is None else expected)
    assert "\\" not in translated or expected is None


# =============================================================================
# The env a step runs in is its method's registered env
# =============================================================================

OTHER_ENV = "other-env"
OTHER_DIGEST = "e" * 64


def _registered_env_scenario() -> Scenario:
    """One selector-rooted method node in the harness's built env."""
    return Scenario(nodes=[selector(),
                           node("a", module="test", inputs=[wire("sel")])],
                    samples=["s1"], env_name=ENV_NAME)


def _register_other_env(root: Path, monkeypatch) -> None:
    """Register a second, byo env through production (its commit included).

    Its image content names its own layer, so its env fingerprint differs
    from the harness env's, whose content names the ``STUB_DIGEST`` layer.
    """
    from wfc import environments as envs_mod

    stub_docker_image_inspect(monkeypatch, f"sha256:{OTHER_DIGEST}")
    stub_docker_image_content(monkeypatch, {
        "layers": [f"sha256:{OTHER_DIGEST}"], "env": None,
        "user": None, "workdir": None})
    envs_mod.register(name=OTHER_ENV, backend="byo",
                      source={"image": "docker://local/other-env:latest"},
                      project_dir=root)


def _register_in_env(spec, env: str) -> None:
    """Register the node's method again through production, declaring *env*.

    Only ``env:`` changes in the source ``method.yaml``: ``env`` is outside
    the contract projection, so the code fingerprint and the version stay.
    """
    from tests.harness.project import method_source_dir
    from wfc.persistence import project_root
    from wfc.registration import register_method

    source_dir = method_source_dir(project_root(), spec)
    method_yaml = source_dir / "method.yaml"
    method_yaml.write_text(
        method_yaml.read_text(encoding="utf-8")
        .replace(f"env: {ENV_NAME}\n", f"env: {env}\n"),
        encoding="utf-8")
    register_method(method_dir=source_dir, module_name=spec.module, assume_yes=True)


def _image(root: Path, env: str) -> str:
    from wfc.environments import daemon_ref, get

    return daemon_ref(get(env, root).container)


def _env_fingerprint(root: Path, env: str) -> str:
    from wfc.environments import get

    return get(env, root).env_fingerprint


@workflow(purpose="A frozen document enriched while the method was registered "
                  "in one env, run after the method is registered again in "
                  "another (same code, same version): the step launches the "
                  "registered env's image and its run row carries that env's "
                  "fingerprint, so what runs is what the key is over")
def test_a_document_naming_an_earlier_env_runs_in_the_registered_env(
        git_project, monkeypatch):
    project = build_project(_registered_env_scenario(), root=git_project,
                            monkeypatch=monkeypatch)
    spec = project.scenario.node_by_id("a")
    target = ("a", "s1", "default")

    口 = Step(step_num=1, name="Register the method again in another env",
             purpose="The document still names the env of the earlier "
                     "registration")
    _register_other_env(project.root, monkeypatch)
    _register_in_env(spec, OTHER_ENV)
    document = json.loads(project.pipeline_json.read_text())
    assert next(n for n in document["nodes"] if n["id"] == "a")["env"] == ENV_NAME

    口 = Step(step_num=2, name="Run the step",
             purpose="It launches in, and is keyed on, the registered env")
    obs = drive_target(project, target, monkeypatch=monkeypatch,
                       through=Phase.DISPATCH)
    cmd = obs.dispatch_cmd(target)
    assert cmd is not None, obs.runs[target].ending
    assert _image(project.root, OTHER_ENV) in cmd
    assert _image(project.root, ENV_NAME) not in cmd
    assert (obs.run_row(target)["env_fingerprint"]
            == _env_fingerprint(project.root, OTHER_ENV))


@workflow(purpose="A committed hand edit of the registered copy's env: changes "
                  "nothing that runs: a standalone run-step with no document "
                  "launches the method's registered env's image and its run "
                  "row carries that env's fingerprint")
def test_a_hand_edited_registered_env_runs_and_keys_in_the_registered_env(
        git_project, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from sqlmodel import select

    from tests.fixtures.fakes import stub_method_process
    from tests.harness.drivers import STUB
    from tests.harness.observe import TargetRun
    from wfc import layout
    from wfc.execution.run_step import run_step
    from wfc.persistence import Run, get_session

    project = build_project(_registered_env_scenario(), root=git_project,
                            monkeypatch=monkeypatch)
    spec = project.scenario.node_by_id("a")
    root = project.root

    口 = Step(step_num=1, name="Hand-edit and commit the registered copy's env",
             purpose="The copy's method.yaml names another registered env; "
                     "Method.env still names the env of the registration")
    _register_other_env(root, monkeypatch)
    copy_yaml = layout.method_dir(root, spec.module, spec.method_name) / "method.yaml"
    copy_yaml.write_text(copy_yaml.read_text(encoding="utf-8")
                         .replace(f"env: {ENV_NAME}\n", f"env: {OTHER_ENV}\n"),
                         encoding="utf-8")
    subprocess.run(["git", "commit", "-q", "-am", "user edits registered env"],
                   cwd=root, check=True, capture_output=True)

    口 = Step(step_num=2, name="Run the step standalone, with no document",
             purpose="The pre-flight finds the registered env's image held; "
                     "the step launches in, and is keyed on, that env")
    for var in ("WFC_PIPELINE_ID", "WFC_PIPELINE_JSON"):
        monkeypatch.delenv(var, raising=False)
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    record = TargetRun(target=("a", "s1", "default"))
    with stub_method_process(project, SimpleNamespace(method_name="a"), record,
                             monkeypatch, STUB):
        run_step(node_id="a", sample="s1", method_name=spec.method_name,
                 module_name=spec.module, pipeline_json=None)
    assert record.dispatch_cmd is not None
    assert _image(root, ENV_NAME) in record.dispatch_cmd
    assert _image(root, OTHER_ENV) not in record.dispatch_cmd
    with get_session() as session:
        run = session.exec(select(Run).order_by(Run.id.desc())).first()
    assert run.env_fingerprint == _env_fingerprint(root, ENV_NAME)


@workflow(purpose="A method registered again in another env between the claim "
                  "and dispatch (the same code in another env, so the next "
                  "version) leaves the claimed version intact: the step "
                  "launches the claimed version's recorded image with its "
                  "copy bound read-only, and its completed run row carries the "
                  "claimed version and env fingerprint")
def test_env_registered_again_after_the_claim_launches_the_claimed_env(
        git_project, monkeypatch):
    from sqlmodel import select

    from tests.fixtures.fakes import register_after_claim
    from wfc import layout
    from wfc.persistence import MethodVersion, get_session
    from wfc.registration.method_version import read_version_list

    project = build_project(_registered_env_scenario(), root=git_project,
                            monkeypatch=monkeypatch)
    spec = project.scenario.node_by_id("a")
    target = ("a", "s1", "default")
    root = project.root
    _register_other_env(root, monkeypatch)
    (v1,) = read_version_list(root, spec.module, spec.method_name)
    claimed_image = _image(root, ENV_NAME)

    口 = Step(step_num=1, name="Register the method in another env mid-step",
             purpose="The claim keyed the step on v1 in the first env; the "
                     "registration (v2, the other env) lands before dispatch "
                     "launches")
    register_after_claim(monkeypatch, lambda: _register_in_env(spec, OTHER_ENV))
    obs = drive_target(project, target, monkeypatch=monkeypatch)
    _, v2 = read_version_list(root, spec.module, spec.method_name)
    assert (v2.env_name, v2.code_fingerprint) == (OTHER_ENV, v1.code_fingerprint)

    口 = Step(step_num=2, name="The claimed version launches",
             purpose="Its recorded image and its read-only copy; the run row "
                     "is v1's with v1's env fingerprint")
    assert obs.runs[target].ending == "completed", obs.runs[target].ending
    cmd = obs.dispatch_cmd(target)
    assert claimed_image in cmd
    assert _image(root, OTHER_ENV) not in cmd
    copy = layout.code_copy_dir(root, v1.tree_hash)
    assert (f"{copy.as_posix()}:/work/{copy.relative_to(root).as_posix()}:ro"
            in cmd)
    row = obs.run_row(target)
    assert row["status"] == "completed"
    assert row["env_fingerprint"] == v1.env_fingerprint
    with get_session() as session:
        v1_row = session.exec(select(MethodVersion.id).where(
            MethodVersion.label == 1,
            MethodVersion.env_fingerprint == v1.env_fingerprint)).one()
    assert row["version_id"] == v1_row


# =============================================================================
# A run executes its version's extracted copy, which it cannot write to
# =============================================================================

def _copy_bind_value(root: Path, tree_hash: str, *, read_only: bool) -> str:
    """The ``-v`` value binding a version's copy at its own /work path."""
    from wfc import layout

    copy = layout.code_copy_dir(root, tree_hash)
    value = f"{copy.as_posix()}:/work/{copy.relative_to(root).as_posix()}"
    return value + ":ro" if read_only else value


def _commit(root: Path, message: str) -> None:
    """Commit the edited pipeline document, so the claim sees a clean tree."""
    import subprocess

    subprocess.run(["git", "commit", "-q", "-am", message], cwd=root,
                   check=True, capture_output=True)


def _version_row(entry) -> int:
    """The ``MethodVersion`` row id mirroring a version-list entry."""
    from sqlmodel import select

    from wfc.persistence import MethodVersion, get_session

    with get_session() as session:
        return session.exec(select(MethodVersion.id).where(
            MethodVersion.label == entry.label,
            MethodVersion.tree_hash == entry.tree_hash)).one()


@workflow(purpose="A node pinned to an older version runs that "
                  "version's tree under that version's key: with v1 and v2 "
                  "registered, the step pinned to v1 launches v1's extracted "
                  "copy (bound read-only, its script inside) and records v1's "
                  "row; the same step unpinned runs v2's copy under another key")
def test_a_node_pinned_to_an_older_version_runs_that_versions_tree(
        git_project, monkeypatch):
    from tests.harness.project import method_source_dir
    from wfc.registration import register_method
    from wfc.registration.method_version import read_version_list

    project = build_project(_registered_env_scenario(), root=git_project,
                            monkeypatch=monkeypatch)
    root = project.root
    spec = project.scenario.node_by_id("a")
    target = ("a", "s1", "default")

    口 = Step(step_num=1, name="Register changed code: v2",
             purpose="v1 stays runnable beside it")
    source = method_source_dir(root, spec)
    script = source / "a.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# v2\n",
                      encoding="utf-8")
    register_method(method_dir=source, module_name=spec.module, assume_yes=True)
    v1, v2 = read_version_list(root, spec.module, spec.method_name)
    assert v1.tree_hash != v2.tree_hash

    口 = Step(step_num=2, name="Pin the node to v1 and run it",
             purpose="The frozen document names v1's identity")
    document = json.loads(project.pipeline_json.read_text())
    next(n for n in document["nodes"] if n["id"] == "a")["version"] = v1.identity
    project.pipeline_json.write_text(json.dumps(document, indent=2))
    _commit(root, "pin a to v1")
    pinned = drive_target(project, target, monkeypatch=monkeypatch)
    cmd = pinned.dispatch_cmd(target)
    assert pinned.runs[target].ending == "completed", pinned.runs[target].ending
    assert _copy_bind_value(root, v1.tree_hash, read_only=True) in cmd
    v1_rel = _copy_bind_value(root, v1.tree_hash, read_only=False).split(":/work/")[1]
    assert f"/work/{v1_rel}/a.py" in cmd
    assert not any(v2.tree_hash in a for a in cmd)
    pinned_row = pinned.run_row(target)
    assert pinned_row["version_id"] == _version_row(v1)
    assert pinned_row["env_fingerprint"] == v1.env_fingerprint

    口 = Step(step_num=3, name="Unpin the node and run it",
             purpose="The newest, v2, runs under its own key")
    del next(n for n in document["nodes"] if n["id"] == "a")["version"]
    project.pipeline_json.write_text(json.dumps(document, indent=2))
    _commit(root, "unpin a")
    newest = drive_target(project, target, monkeypatch=monkeypatch)
    assert newest.runs[target].ending == "completed"
    assert _copy_bind_value(root, v2.tree_hash, read_only=True) in newest.dispatch_cmd(target)
    assert newest.run_row(target)["version_id"] == _version_row(v2)
    assert newest.run_row(target)["cache_key"] != pinned_row["cache_key"]


@workflow(purpose="Fallback: on a backend that declares no read-only "
                  "binds, the version's copy is bound read-write with "
                  "PYTHONDONTWRITEBYTECODE=1, and a run that writes into its "
                  "code directory fails method-failed naming the file, and the "
                  "copy's stamp is dropped so its next use re-extracts it")
def test_a_run_that_writes_into_a_writable_code_copy_fails_naming_the_file(
        git_project, monkeypatch):
    from tests.harness import Behavior
    from wfc import layout
    from wfc.environments import argv
    from wfc.registration.method_version import read_version_list

    scn = _registered_env_scenario()
    scn.node_by_id("a").behavior = Behavior(write_beside_script=("scratch.txt",))
    project = build_project(scn, root=git_project, monkeypatch=monkeypatch)
    root = project.root
    target = ("a", "s1", "default")
    (v1,) = read_version_list(root, "test", "a")

    口 = Step(step_num=1, name="Run on a backend with no read-only binds",
             purpose="The method writes scratch.txt beside its own script")
    monkeypatch.setitem(argv.READ_ONLY_BINDS, "docker", False)
    obs = drive_target(project, target, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="The run fails naming the file; the stamp is gone",
             purpose="The argv bound the copy read-write and told Python "
                     "not to write bytecode")
    cmd = obs.dispatch_cmd(target)
    assert _copy_bind_value(root, v1.tree_hash, read_only=False) in cmd
    assert _copy_bind_value(root, v1.tree_hash, read_only=True) not in cmd
    assert "PYTHONDONTWRITEBYTECODE=1" in cmd
    assert obs.runs[target].ending == "method-failed"
    row = obs.run_row(target)
    assert row["status"] == "failed"
    assert "scratch.txt" in row["error_message"]
    assert not layout.code_copy_stamp(root, v1.tree_hash).exists()
