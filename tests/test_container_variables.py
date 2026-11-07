"""Every container wfc starts takes its variables from one builder.

Catalog cases 10 (a step's container and plain ``wfc shell``/``wfc
jupyter``), 11 (the dev loop's ``python`` is the step's interpreter, for a uv
and a pixi env) and 14 (a results file wfc cannot use fails the step naming
the file and the field).
"""
from __future__ import annotations

import json
import posixpath

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import stub_dev_loop_launch, stub_docker_image_inspect
from tests.harness import (
    STUB_DIGEST,
    Behavior,
    Phase,
    Scenario,
    chain,
    node,
    run_target,
    selector,
    wire,
)
from wfc.execution.container_vars import DEFAULT_CONTAINER_PATH

ENV_NAME = "image-io"
DAEMON_REF = f"sha256:{STUB_DIGEST}"
TARGET = ("m", "s1", "default")


def _env_flags(argv: list[str]) -> dict[str, str]:
    """The ``-e NAME=VALUE`` flags of a ``docker run`` argv, by name."""
    pairs = [argv[i + 1] for i in range(len(argv) - 1) if argv[i] == "-e"]
    return dict(pair.split("=", 1) for pair in pairs)


def _scenario(backend: str) -> Scenario:
    """One selector-rooted method node in a container env of *backend*."""
    return Scenario(
        nodes=[selector(), node("m", module="test", inputs=[wire("sel")])],
        samples=["s1"], env_name=ENV_NAME, env_backend=backend,
    )


@workflow(purpose="A step's container is started with every variable the one "
                  "builder gives: the WFC_* values (WFC_INPUT_PATHS keeps its "
                  "lists), the method's declared names, PYTHONUNBUFFERED=1 "
                  "and the env's own bin first on PATH; the host's PATH and "
                  "PYTHONPATH stay out")
def test_step_container_takes_every_variable_from_the_builder(git_project,
                                                              monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/host/leak")
    obs = run_target(_scenario("pixi"), "m", root=git_project,
                     monkeypatch=monkeypatch, through=Phase.DISPATCH)
    flags = _env_flags(obs.dispatch_cmd(TARGET))

    assert {"WFC_RUN_ID", "WFC_RUN_DIR", "WFC_SAMPLE", "WFC_PARAMS",
            "WFC_NODE_ID", "WFC_PIPELINE_ID", "WFC_VARIANT"} <= set(flags)
    assert all(isinstance(paths, list)
               for paths in json.loads(flags["WFC_INPUT_PATHS"]).values())
    assert json.loads(flags["WFC_DECLARED_NAMES"]) == {
        "inputs": {"data": {"multiple": False, "optional": False}},
        "outputs": ["data"],
    }
    assert flags["PYTHONUNBUFFERED"] == "1"
    assert flags["PATH"] == f"/opt/.pixi/envs/{ENV_NAME}/bin:{DEFAULT_CONTAINER_PATH}"
    assert "PYTHONPATH" not in flags
    # The host process that launches the container keeps the host's PATH.
    assert obs.dispatch_env(TARGET)["PATH"] != flags["PATH"]


@pytest.mark.parametrize("backend", ["uv", "pixi"])
@workflow(purpose="For a uv env and a pixi env, plain `wfc shell <env>` and "
                  "`wfc jupyter <env>` start their container with "
                  "PYTHONUNBUFFERED=1 and, first on PATH, the directory of "
                  "the interpreter a step of that env runs under, so `python` "
                  "there is the step's; they carry no step's WFC_* values")
def test_dev_loop_python_is_the_steps_interpreter(git_project, monkeypatch,
                                                  backend):
    口 = Step(step_num=1, name="Dispatch a step in the env",
             purpose="The step's inner argv names the interpreter it runs under")
    obs = run_target(_scenario(backend), "m", root=git_project,
                     monkeypatch=monkeypatch, through=Phase.DISPATCH)
    cmd = obs.dispatch_cmd(TARGET)
    step_python = cmd[cmd.index(DAEMON_REF) + 1]
    assert posixpath.isabs(step_python)

    口 = Step(step_num=2, name="Open a shell and a notebook in the env",
             purpose="Through the CLI, with the launch stubbed at its "
                     "subprocess call")
    from wfc.cli import cli_main

    launched: list[list[str]] = []
    stub_dev_loop_launch(monkeypatch, lambda argv: launched.append(list(argv)) or 0)
    stub_docker_image_inspect(monkeypatch, DAEMON_REF)
    assert cli_main(["shell", ENV_NAME]) == 0
    assert cli_main(["jupyter", ENV_NAME, "--port", "8890"]) == 0

    口 = Step(step_num=3, name="Read each container's variables",
             purpose="PATH starts with the step interpreter's directory")
    assert len(launched) == 2
    for argv in launched:
        flags = _env_flags(argv)
        assert flags["PYTHONUNBUFFERED"] == "1"
        first = flags["PATH"].split(":", 1)[0]
        assert first == posixpath.dirname(step_python)
        assert not any(name.startswith("WFC_") for name in flags)


def test_byo_env_keeps_its_own_path_and_paths_translate():
    """A bring-your-own image gets no PATH; host paths become container paths."""
    from wfc import layout
    from wfc.execution.container_vars import container_variables, step_variables

    root = "/proj"
    mounts = layout.mount_table(root, f"{root}/.dvc/cache")
    step = step_variables(run_id=7, run_dir=f"{root}/.wfc/runs/7", sample="s",
                          params={"k": 1}, node_id="n", pipeline_id="p",
                          variant="default",
                          slot_paths={"data": [f"{root}/data/a.csv"]})
    values = container_variables(step=step, env_bin=None, mounts=mounts)
    assert "PATH" not in values
    assert values["PYTHONUNBUFFERED"] == "1"
    assert not values["WFC_RUN_DIR"].startswith(root)
    assert json.loads(values["WFC_INPUT_PATHS"])["data"][0].endswith("data/a.csv")
    assert not json.loads(values["WFC_INPUT_PATHS"])["data"][0].startswith(root)
    # Host-side, nothing is translated.
    assert container_variables(step=step)["WFC_RUN_DIR"] == f"{root}/.wfc/runs/7"


@pytest.mark.parametrize("behavior, field", [
    (Behavior(listed_outputs=("data",)), "data"),
    (Behavior(metrics={"kept_rows": [3]}), "kept_rows"),
], ids=["list-path", "list-metric"])
@workflow(purpose="A results file recording an output as a list, or a metric "
                  "that is not a scalar, fails the step (not orphaned) with "
                  "an error naming _wfc_results.json and the field")
def test_results_file_wfc_cannot_use_fails_the_step_naming_the_field(
        git_project, monkeypatch, behavior, field):
    scn = Scenario(nodes=chain("n1"))
    scn.node_by_id("n1").behavior = behavior
    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch)

    target = ("n1", "s1", "default")
    assert obs.exit_code(target) == 1
    assert obs.runs[target].ending == "refused-output"
    row = obs.run_row(target)
    assert row["status"] == "failed"
    assert "_wfc_results.json" in row["error_message"]
    assert f"'{field}'" in row["error_message"]
    assert obs.outcomes[target]["status"] == "failed"
