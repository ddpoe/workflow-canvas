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

from tests.fixtures.fakes import stub_dev_loop_launch, stub_docker_image_inspect
from tests.harness import (
    Phase,
    STUB_DIGEST,
    Scenario,
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
    image_idx = cmd.index(DAEMON_REF)
    inner = cmd[image_idx + 1:]
    assert inner == [
        "/opt/.pixi/envs/image-io/bin/python",
        "/work/methods/ml_train/ml_train.py",
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


@workflow(purpose="A method script outside the project root reaches the "
                  "docker argv as the host path the document named, not "
                  "rewritten under /work")
def test_run_step_passes_a_script_outside_the_root_through_unchanged(
        git_project, monkeypatch):
    outside = git_project.parent / "outside"
    outside.mkdir()
    outside_script = outside / "outside.py"
    outside_script.write_text("print('outside')\n", encoding="utf-8")
    # The document's script entry is methods/<m>/<script_name>; three
    # parent hops leave the project root for its sibling directory.
    scn = _dispatch_scenario("ml_train",
                             script_name="../../../outside/outside.py")
    obs = run_target(scn, "ml_train", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.DISPATCH)

    target = ("ml_train", "s1", "default")
    cmd = obs.dispatch_cmd(target)
    inner = cmd[cmd.index(DAEMON_REF) + 1:]
    assert len(inner) == 2
    script_arg = inner[1]
    assert script_arg == str(
        git_project / "methods" / "ml_train" / "../../../outside/outside.py")
    assert not script_arg.startswith("/work")
    assert Path(script_arg).resolve() == outside_script.resolve()
@workflow(purpose="For one recorded pixi env in one project, the argv dispatch "
                  "builds and the argv `wfc exec` builds carry the same --user, "
                  "the same binds and the same workdir, and differ only in the "
                  "flags each caller splices after --rm and in the inner command")
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
    # The daemon holds the env's image, so the dev loop rebuilds nothing.
    stub_docker_image_inspect(monkeypatch, DAEMON_REF)
    assert dev_loop.exec_(ENV_NAME, ["python", "-c", "print(1)"]) == 0
    (exec_argv,) = launched

    口 = Step(step_num=3, name="Compare the two argvs",
             purpose="Split each argv at --rm, --user and the image ref: the "
                     "builder's --user, binds and workdir must match, so only "
                     "the caller's spliced flags and the inner command differ")

    def _parts(argv):
        """The ``docker run --rm`` prefix, and the builder's run options
        from ``--user`` up to the image ref."""
        image_idx = argv.index(DAEMON_REF)
        user_idx = argv.index("--user")
        return argv[:3], argv[user_idx:image_idx]

    dispatch_prefix, dispatch_body = _parts(dispatch_argv)
    exec_prefix, exec_body = _parts(exec_argv)
    assert dispatch_prefix == exec_prefix == ["docker", "run", "--rm"]
    assert {"-v", "-w"} <= set(dispatch_body)
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
    # project_root at .runs/<run_id>/...).
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


@workflow(purpose="run_step with a nonexistent method script fails host-side "
                  "with a clear error BEFORE any docker invocation")
def test_run_step_missing_script_fails_before_docker(git_project, monkeypatch, capsys):
    # The node's script points at a file that does NOT exist.
    scn = _dispatch_scenario("ml_ghost", script_name="nope.py")
    obs = run_target(scn, "ml_ghost", root=git_project, monkeypatch=monkeypatch)

    target = ("ml_ghost", "s1", "default")
    assert obs.exit_code(target) == 1
    # No docker command was ever assembled/run.
    assert obs.runs[target].dispatch_cmd is None, (
        f"docker must not be invoked for a missing script; "
        f"got {obs.runs[target].dispatch_cmd!r}"
    )
    err = capsys.readouterr().err
    assert "method script not found" in err
    assert "nope.py" in err


def test_snakefile_generator_emits_python_dash_m_wfc_for_container_env(tmp_path):
    """Container-env methods get a plain ``python -m wfc run-step`` shell
    line. Container wrapping happens inside run-step, not in the generated
    Snakefile."""
    from wfc.graph import StepDef
    from wfc.orchestration.snakemake import _generate_rule

    step = StepDef(
        method_name="ml_train",
        module_name="test",
        script_path="methods/ml_train/ml_train.py",
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
