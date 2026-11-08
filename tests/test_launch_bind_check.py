"""The launch's container bind check: its argv, its verdict, and the launches it stops.

Environments' :func:`wfc.environments.check_binds` starts one short container
from the launch's image with the binds a step gets and compares what it sees
with the host. ``run_pipeline`` and ``wfc try`` ask it before anything is
recorded: an empty bind refuses the launch naming the bind and ``wfc
doctor``; a check that cannot run prints one note and the launch goes on.
The container run is faked at its docker edge (``stub_bind_probe``); the
Docker-marked witnesses are in ``tests/integration/test_launch_bind_check_docker.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlalchemy import text

from tests.fixtures.fakes import fake_engine_process, stub_bind_probe
from wfc import layout
from wfc.environments import (
    BIND_EMPTY,
    BIND_NOT_RUN,
    BIND_VISIBLE,
    build_docker_command,
    check_binds,
    host_bind_expectations,
)
from wfc.environments.bind_check import bind_check_command
from wfc.environments.docker import DockerTimeoutError

IMAGE = "sha256:" + "e" * 64


def _project(tmp_path: Path, *, cache_entries: int = 2) -> tuple[Path, Path]:
    """A project folder with its config file and a cache holding *cache_entries*."""
    root = tmp_path / "proj"
    layout.marker_path(root).parent.mkdir(parents=True)
    layout.marker_path(root).write_text('[project]\nname = "p"\n', encoding="utf-8")
    cache = layout.dvc_cache_dir(root)
    cache.mkdir(parents=True)
    for i in range(cache_entries):
        (cache / f"entry{i}").mkdir()
    return root, cache


def _rows(argv: list[str], flag: str) -> list[str]:
    return [value for f, value in zip(argv, argv[1:]) if f == flag]


def test_the_check_binds_exactly_the_rows_a_step_gets(tmp_path):
    """Case 5: the check's -v and -w rows are build_docker_command's, no code copy."""
    root, cache = _project(tmp_path)
    expected = host_bind_expectations(root, cache)
    check = bind_check_command(IMAGE, root, cache, expected, uid=1, gid=2, name="n")
    step = build_docker_command(IMAGE, root, cache, ["python", "x.py"], uid=1, gid=2)
    assert _rows(check, "-v") == _rows(step, "-v")
    assert len(_rows(check, "-v")) == 2
    assert _rows(check, "-w") == _rows(step, "-w") == ["/work"]
    assert check[:5] == ["docker", "run", "--rm", "--pull", "never"]
    assert check[-2:] == ["/work/.wfc/wf-canvas.toml", "/dvc-cache"]


def test_a_missing_cache_is_never_bound_and_not_probed(tmp_path):
    """A host path that does not exist is left out, so Docker Desktop never creates it."""
    root, cache = _project(tmp_path)
    for child in cache.iterdir():
        child.rmdir()
    cache.rmdir()
    expected = host_bind_expectations(root, cache)
    assert expected.cache_entries is None
    argv = bind_check_command(IMAGE, root, cache, expected, uid=0, gid=0, name="n")
    assert [v for v in _rows(argv, "-v") if v.endswith(":/dvc-cache")] == []
    assert argv[-1] == ""
    assert not cache.exists()


@pytest.mark.parametrize("seen, outcome, container", [
    ("visible", BIND_VISIBLE, None),
    ("empty", BIND_EMPTY, "/work"),
])
def test_the_verdict_names_the_bind_that_came_up_empty(tmp_path, monkeypatch,
                                                       seen, outcome, container):
    """A visible probe passes; an empty one names the project bind and its host path."""
    root, cache = _project(tmp_path)
    stub_bind_probe(monkeypatch, seen)
    verdict = check_binds(IMAGE, root, cache, host_bind_expectations(root, cache))
    assert verdict.outcome == outcome
    assert verdict.container == container
    if outcome == BIND_EMPTY:
        assert verdict.host == root.resolve().as_posix()


def test_an_empty_cache_bind_is_named_and_both_empty_passes(tmp_path, monkeypatch):
    """A cache the container sees empty is named; an empty cache on both sides passes."""
    root, cache = _project(tmp_path)
    config = layout.marker_path(root).stat().st_size
    stub_bind_probe(monkeypatch, subprocess.CompletedProcess(
        [], 0, stdout=f"config {config}\ncache 0\n", stderr=""))
    verdict = check_binds(IMAGE, root, cache, host_bind_expectations(root, cache))
    assert (verdict.outcome, verdict.container) == (BIND_EMPTY, "/dvc-cache")
    empty_root, empty_cache = _project(tmp_path / "b", cache_entries=0)
    assert check_binds(IMAGE, empty_root, empty_cache,
                       host_bind_expectations(empty_root, empty_cache)
                       ).outcome == BIND_VISIBLE


@pytest.mark.parametrize("answer", [
    subprocess.CompletedProcess([], 127, stdout="",
                                stderr="exec: \"/bin/sh\": no such file\nSee 'docker run --help'.\n"),
    DockerTimeoutError("the bind check did not finish within 60 s"),
    OSError("docker not found"),
])
def test_a_check_that_cannot_run_says_so_without_a_docker_command(tmp_path, monkeypatch,
                                                                  answer):
    """A failed, timed-out or unstartable probe is not-run, its detail free of docker's hint."""
    root, cache = _project(tmp_path)
    stub_bind_probe(monkeypatch, answer)
    verdict = check_binds(IMAGE, root, cache, host_bind_expectations(root, cache))
    assert verdict.outcome == BIND_NOT_RUN
    assert verdict.detail and "docker run" not in verdict.detail


def _launch_project(git_project, monkeypatch, pid):
    from tests.test_env_preflight import _pipeline_project

    return _pipeline_project(pid, git_project, monkeypatch)


def _cache_listing(root: Path) -> list[str]:
    cache = layout.dvc_cache_dir(root)
    return sorted(p.relative_to(cache).as_posix() for p in cache.rglob("*")) if cache.exists() else []


@workflow(purpose="At the pipeline: run_pipeline whose bind check sees the "
                  "project bind empty stops before the launch is prepared: the "
                  "message names the bind and wfc doctor, no pipeline_runs or "
                  "run row, no frozen document, no step")
def test_run_pipeline_refuses_an_empty_project_bind_before_anything_is_recorded(
    git_project, monkeypatch,
):
    """Case 3 at the pipeline: refused before prepare_launch, nothing recorded."""
    from wfc.execution import run_pipeline
    from wfc.execution.pipeline import BindsNotVisibleError
    from wfc.persistence import get_session

    pid = "pipe-bind-empty"
    口 = Step(step_num=1, name="A project whose images are present",
             purpose="Registered envs and methods through production paths")
    project = _launch_project(git_project, monkeypatch, pid)
    before = _cache_listing(project.root)

    口 = Step(step_num=2, name="The bind check's container sees the project empty",
             purpose="The docker edge of the check answers no config file")
    seen: list = []

    def empty(argv, *, timeout):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="config none\ncache 0\n",
                                           stderr="")
    stub_bind_probe(monkeypatch, empty)

    口 = Step(step_num=3, name="Launch, and nothing is recorded",
             purpose="Refused before prepare_launch; the engine never starts")
    spawned: list = []
    with fake_engine_process(launch=lambda argv, **kw: spawned.append(argv),
                             only_snakemake=True):
        with pytest.raises(BindsNotVisibleError) as info:
            run_pipeline(pipeline_path=str(project.pipeline_json),
                         project_root=str(project.root), pipeline_id=pid)
    message = str(info.value)
    assert f"{project.root.resolve().as_posix()} at /work" in message
    assert "`wfc doctor`" in message
    assert len(seen) == 1 and seen[0][seen[0].index("--entrypoint") + 2].startswith("sha256:")
    assert spawned == []
    assert not layout.pipeline_run_dir(project.root, pid).exists()
    assert _cache_listing(project.root) == before
    with get_session() as session:
        assert session.exec(text("select count(*) from pipeline_runs")).one()[0] == 0
        assert session.exec(text("select count(*) from runs")).one()[0] == 0


@workflow(purpose="run_pipeline whose bind check cannot run (the image has no "
                  "shell) prints one note and launches; a visible check prints "
                  "nothing new")
@pytest.mark.parametrize("answer", ["not-run", "visible"])
def test_a_check_that_cannot_run_lets_the_launch_go_on(git_project, monkeypatch, capsys,
                                                       answer):
    """Not-run prints one note and launches; visible prints nothing."""
    from wfc.execution import run_pipeline

    pid = f"pipe-bind-{answer}"
    project = _launch_project(git_project, monkeypatch, pid)
    stub_bind_probe(monkeypatch, "visible" if answer == "visible" else
                    subprocess.CompletedProcess([], 127, stdout="", stderr="no shell\n"))
    spawned: list = []
    with fake_engine_process(launch=lambda argv, **kw: spawned.append(argv),
                             only_snakemake=True):
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(project.root), pipeline_id=pid)
    assert len(spawned) == 1
    notes = [line for line in capsys.readouterr().err.splitlines()
             if "could not check that a step's container" in line]
    assert notes == ([] if answer == "visible" else
                     ["Note: could not check that a step's container sees the "
                      "project (no shell); the launch goes on."])


def _pipeline_dirs(root: Path) -> set[str]:
    pipelines = layout.pipelines_dir(root)
    return {p.name for p in pipelines.iterdir()} if pipelines.is_dir() else set()


@workflow(purpose="`wfc try` whose bind check sees the project bind empty exits 1 "
                  "naming the bind and wfc doctor, before the freeze: no frozen "
                  "document, no pipeline row, and the previous draft run and its "
                  "output as they were")
def test_wfc_try_with_an_empty_bind_is_refused_before_the_freeze(
        tmp_project, monkeypatch, capsys):
    """`wfc try` with an empty bind exits 1 before the freeze and writes nothing."""
    from tests.fixtures.draft_project import built_draft_project
    from tests.test_cli_try import _launch, _pipeline_rows, _try_argv, _write_folder
    from wfc.cli import cli_main
    from wfc.execution.draft import read_draft_run

    口 = Step(step_num=1, name="A good try",
             purpose="The draft folder has a draft run and its output")
    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)
    argv = _try_argv(folder, module, up_run, mid_ref)
    with _launch(tmp_project, scenario, monkeypatch):
        assert cli_main(argv) == 0, capsys.readouterr().err
    before = read_draft_run(module, "fresh", tmp_project)
    output = (layout.draft_dir(tmp_project, module, "fresh")
              / before["steps"]["fresh"]["s1"]["outputs"]["result"])
    output_bytes = output.read_bytes()
    rows, dirs = _pipeline_rows(), _pipeline_dirs(tmp_project)
    capsys.readouterr()

    口 = Step(step_num=2, name="The bind check's container sees the project empty",
             purpose="The docker edge of the check answers no config file")
    probes: list = []

    def empty(probe_argv, *, timeout):
        probes.append(probe_argv)
        return subprocess.CompletedProcess(probe_argv, 0, stdout="config none\ncache 0\n",
                                           stderr="")
    stub_bind_probe(monkeypatch, empty)

    口 = Step(step_num=3, name="Try again: refused, nothing written",
             purpose="ERROR naming /work and wfc doctor; no frozen document, no "
                     "pipeline row; the previous draft run and output kept")
    with _launch(tmp_project, scenario, monkeypatch):
        rc = cli_main(argv)
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR:" in err and "at /work" in err and "`wfc doctor`" in err, err
    assert len(probes) == 1
    assert _pipeline_dirs(tmp_project) == dirs
    assert _pipeline_rows() == rows
    assert read_draft_run(module, "fresh", tmp_project) == before
    assert output.read_bytes() == output_bytes


def test_a_try_image_that_cannot_be_read_is_a_refusal(tmp_project):
    """Reading the try's image back for the bind check fails: a TryRefusedError.

    The draft preflight has just read the draft, so this is another writer
    leaving its pointer unreadable in between. ``run_try`` reads the image
    through ``try_image`` before the freeze, so the try ends as a refusal
    (one ERROR line at the terminal), not a raw traceback.
    """
    from wfc.contracts import DRAFT_PIN
    from wfc.execution.try_run import TryRefusedError, try_image

    # The fault itself, written by hand: a draft pointer that is not one wfc
    # wrote (no production path leaves one).
    pointer = layout.draft_pointer_path(tmp_project, "mod", "fresh")
    pointer.parent.mkdir(parents=True)
    pointer.write_text("not a pointer", encoding="utf-8")
    document = {"nodes": [{"id": "fresh", "module": "mod", "method": "fresh",
                           "version": DRAFT_PIN}]}

    with pytest.raises(TryRefusedError, match="image cannot be read") as caught:
        try_image(document, tmp_project)
    assert "is not a draft pointer" in str(caught.value)


def test_the_canvas_names_the_refusal(tmp_path):
    """The canvas classifies the refusal as binds_not_visible, hinting at wfc doctor."""
    from wfc.canvas.submission import _classify_pipeline_error
    from wfc.execution.pipeline import BindsNotVisibleError

    payload = _classify_pipeline_error(BindsNotVisibleError("came up empty"))
    assert payload["kind"] == "binds_not_visible"
    assert "wfc doctor" in payload["hint"]
