"""`wfc try`, the wired `wfc shell|jupyter --method`, `wfc runs` and `wfc export` on draft steps.

Cases 7, 8, 10 (the `--method` half) and 25 of the trying-methods request.
A project is built through the scenario harness (a registered method, a
draft of another and one recorded run), then an unregistered method folder
is tried on the CLI. The container launch is the harness's stub rung: the
draft folder's own script runs as a local process under the variables
dispatch built.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from axiom_annotations import Step, workflow

from tests.fixtures.draft_project import built_draft_project
from wfc.cli import cli_main
from wfc.execution.draft import DRAFT_REF_FORM, spell_draft_ref

_FRESH_SCRIPT = '''\
import json, os
from pathlib import Path

paths = json.loads(os.environ["WFC_INPUT_PATHS"])
params = json.loads(os.environ["WFC_PARAMS"])
parts = [params.get("label", "")]
for slot in ("data", "up_table", "draft_table"):
    parts.append(slot + "=" + Path(paths[slot][0]).read_text(encoding="utf-8").strip())
Path(os.environ["WFC_RUN_DIR"], "result.csv").write_text("\\n".join(parts), encoding="utf-8")
'''


def _method_yaml(env: str) -> str:
    slot = "    type: .csv\n    required: true\n"
    return (f"inputs:\n  data:\n{slot}  up_table:\n{slot}  draft_table:\n{slot}"
            f"outputs:\n  result:\n{slot}"
            f"params:\n  label:\n    type: any\n"
            f"executor: local\nenv: {env}\ngpus: false\n")


def _write_folder(parent: Path, name: str, env: str) -> Path:
    folder = parent / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "method.yaml").write_text(_method_yaml(env), encoding="utf-8")
    (folder / f"{name}.py").write_text(_FRESH_SCRIPT, encoding="utf-8")
    return folder


@contextmanager
def _launch(tmp_project, scenario, monkeypatch):
    """The stub rung's container launch, for a step outside a scenario's own."""
    from tests.fixtures.fakes.engine import stub_method_process
    from tests.harness.drivers import STUB
    from tests.harness.observe import TargetRun

    project = SimpleNamespace(root=tmp_project, method_scripts={}, scenario=scenario)
    with stub_method_process(project, SimpleNamespace(method_name="fresh"),
                             TargetRun(target=("up", "s1", "default")),
                             monkeypatch, STUB):
        yield


def _db_rows() -> dict:
    from tests.harness.observe import _read_rows

    return {name: sorted(str(r) for r in _read_rows(name))
            for name in ("Run", "RunOutput", "Blob")}


def _try_argv(folder: Path, module: str, up_run: int, mid_ref: str) -> list[str]:
    return ["try", str(folder), "--module", module, "--sample", "s1",
            "--params", json.dumps({"label": "hello"}),
            "--parent-run-id", f"up_table:table:{up_run}",
            "--parent-run-id", f"draft_table:table:{mid_ref}"]


@workflow(purpose="Case 7: `wfc try` runs an unregistered folder as its module's "
                  "draft on a sample, params and parent entries (a recorded run "
                  "and a draft string), keeps its outputs in the draft's folder, "
                  "records nothing and exits 0; an unregistered env or a draft "
                  "from another folder is refused with nothing added or run")
def test_wfc_try_runs_an_unregistered_folder_and_records_nothing(
        tmp_project, monkeypatch, capsys):
    from tests.harness.observe import _read_rows
    from wfc import layout
    from wfc.execution.draft import read_draft_run
    from wfc.registration import read_draft

    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    sources = tmp_project.parent / f"{tmp_project.name}-tried"
    folder = _write_folder(sources, "fresh", scenario.env_name)
    before = _db_rows()
    capsys.readouterr()

    口 = Step(step_num=1, name="Try the folder",
             purpose="The outcome is stated first, the step runs, and the "
                     "command ends with an export command per output")
    with _launch(tmp_project, scenario, monkeypatch):
        rc = cli_main(_try_argv(folder, module, up_run, mid_ref))
    out = capsys.readouterr()
    assert rc == 0, out.err
    ref = spell_draft_ref(module, "fresh", "fresh", "s1")
    assert out.out.index("Adds ") < out.out.index("Tried ")
    assert f"wfc export --run {ref} --slot result --dest <dir>" in out.out

    口 = Step(step_num=2, name="The outputs are in the draft's folder",
             purpose="The script read the sample, the recorded run's output, the "
                     "draft step's output and the params")
    record = read_draft_run(module, "fresh", tmp_project)
    assert record["pipeline_name"] == "try-fresh"
    step = record["steps"]["fresh"]["s1"]
    assert step["status"] == "completed" and step["version"] == "draft"
    result = layout.draft_dir(tmp_project, module, "fresh") / step["outputs"]["result"]
    text = result.read_text(encoding="utf-8")
    assert text.startswith("hello")
    assert "up_table=" in text and "draft_table=" in text and "data=" in text

    口 = Step(step_num=3, name="Nothing was recorded",
             purpose="No run, output or blob row; the try's pipeline row was "
                     "written at launch and its outcome stored at the end")
    assert _db_rows() == before
    rows = [r for r in _read_rows("PipelineRun") if r["pipeline_id"] == record["pipeline_id"]]
    assert len(rows) == 1 and rows[0]["name"] == "try-fresh"
    assert rows[0]["status"] != "running"

    口 = Step(step_num=4, name="Refusals",
             purpose="An env that is not registered, then a draft from another folder")
    bad = _write_folder(sources, "noenv", "no_such_env")
    assert cli_main(_try_argv(bad, module, up_run, mid_ref)) == 1
    assert "not registered" in capsys.readouterr().err
    assert read_draft(module, "noenv", tmp_project) is None
    other = _write_folder(sources / "elsewhere", "fresh", scenario.env_name)
    assert cli_main(_try_argv(other, module, up_run, mid_ref)) == 1
    assert str(folder.resolve()) in capsys.readouterr().err


def _pipeline_rows() -> dict[str, dict]:
    from tests.harness.observe import _read_rows

    return {row["pipeline_id"]: row for row in _read_rows("PipelineRun")}


@workflow(purpose="An interrupted `wfc try` (Ctrl-C while its method runs) "
                  "ends its draft step and stores its pipeline's outcome, so "
                  "nothing is left in progress and the next try of the draft runs")
def test_an_interrupted_try_leaves_nothing_in_progress(tmp_project, monkeypatch, capsys):
    import pytest

    from tests.fixtures import fakes
    from wfc.execution.draft import read_draft_run

    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)
    capsys.readouterr()

    口 = Step(step_num=1, name="Interrupt the try while its method runs",
             purpose="The container launch raises KeyboardInterrupt; the try "
                     "lets it through")
    with monkeypatch.context() as interrupted:
        fakes.interrupt_method_process(interrupted)
        with pytest.raises(KeyboardInterrupt):
            cli_main(_try_argv(folder, module, up_run, mid_ref))

    口 = Step(step_num=2, name="Nothing is left running",
             purpose="The draft step is cancelled and the pipeline's stored "
                     "status is not running")
    record = read_draft_run(module, "fresh", tmp_project)
    assert record["steps"]["fresh"]["s1"]["status"] == "cancelled"
    assert _pipeline_rows()[record["pipeline_id"]]["status"] != "running"

    口 = Step(step_num=3, name="The next try runs",
             purpose="It is not refused as still in progress, and replaces the "
                     "interrupted draft run")
    with _launch(tmp_project, scenario, monkeypatch):
        rc = cli_main(_try_argv(folder, module, up_run, mid_ref))
    assert rc == 0, capsys.readouterr().err
    later = read_draft_run(module, "fresh", tmp_project)
    assert later["pipeline_id"] != record["pipeline_id"]
    assert later["steps"]["fresh"]["s1"]["status"] == "completed"


def test_a_try_refused_before_its_step_leaves_no_pipeline_row(
        tmp_project, monkeypatch, capsys):
    """A try whose sample cannot be read runs no step and keeps no ``pipeline_runs`` row."""
    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)
    before = set(_pipeline_rows())
    capsys.readouterr()

    argv = _try_argv(folder, module, up_run, mid_ref)
    argv[argv.index("--sample") + 1] = "no_such_sample"
    with _launch(tmp_project, scenario, monkeypatch):
        assert cli_main(argv) == 1
    assert "sample 'no_such_sample' cannot be read" in capsys.readouterr().err
    assert set(_pipeline_rows()) == before


@workflow(purpose="Case 2 through `wfc try`: a try the draft preflight refuses "
                  "(a retired columns: form, the Registration check's ✗) exits 1 "
                  "naming the check, prints no Replaces line, writes no pipeline "
                  "row and keeps the previous draft run and its outputs")
def test_a_refused_try_writes_nothing_and_replaces_nothing(
        tmp_project, monkeypatch, capsys):
    from wfc import layout
    from wfc.execution.draft import read_draft_run

    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)

    口 = Step(step_num=1, name="A good try", purpose="fresh has a draft run")
    with _launch(tmp_project, scenario, monkeypatch):
        assert cli_main(_try_argv(folder, module, up_run, mid_ref)) == 0
    before = read_draft_run(module, "fresh", tmp_project)
    output = (layout.draft_dir(tmp_project, module, "fresh")
              / before["steps"]["fresh"]["s1"]["outputs"]["result"])
    assert output.is_file()
    rows = _pipeline_rows()
    capsys.readouterr()

    口 = Step(step_num=2, name="Break the contract and try again",
             purpose="method.yaml declares the retired columns: on its output")
    yaml_path = folder / "method.yaml"
    yaml_path.write_text(yaml_path.read_text(encoding="utf-8").replace(
        "outputs:\n  result:\n", "outputs:\n  result:\n    columns: [gene]\n"),
        encoding="utf-8")
    with _launch(tmp_project, scenario, monkeypatch):
        rc = cli_main(_try_argv(folder, module, up_run, mid_ref))
    out = capsys.readouterr()

    口 = Step(step_num=3, name="Refused before its step; nothing written",
             purpose="ERROR naming the check; no Replaces line; the pipeline rows "
                     "and the previous draft run and its output as they were")
    assert rc == 1
    assert "ERROR:" in out.err and "Registration check" in out.err, out.err
    assert "`columns`" in out.err, out.err
    assert "Replaces" not in out.out
    assert _pipeline_rows() == rows
    assert read_draft_run(module, "fresh", tmp_project) == before
    assert output.is_file()


@workflow(purpose="Cases 25 and 8: `wfc runs` lists each draft step with its full "
                  "string in RUN, filtered by pipeline and method; `wfc export "
                  "--run draft:...` copies the latest draft run's output saying "
                  "which run, with --path --all names each line by its slot, "
                  "and refuses a short string (showing the full form), "
                  "an unused sample, deleted outputs and a removed draft")
def test_runs_lists_draft_strings_and_export_takes_them(
        tmp_project, monkeypatch, capsys):
    from wfc.execution.draft_clearing import (
        delete_draft_outputs_and_clear,
        plan_draft_delete,
        remove_draft_and_clear,
    )

    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)
    with _launch(tmp_project, scenario, monkeypatch):
        assert cli_main(_try_argv(folder, module, up_run, mid_ref)) == 0
    ref = spell_draft_ref(module, "fresh", "fresh", "s1")
    capsys.readouterr()

    口 = Step(step_num=1, name="wfc runs lists the draft steps",
             purpose="Both draft steps' strings in RUN; --method and --pipeline filter")
    assert cli_main(["runs"]) == 0
    listed = capsys.readouterr().out
    assert ref in listed and mid_ref in listed and "try-fresh" in listed
    assert cli_main(["runs", "--method", f"{module}.fresh"]) == 0
    only = capsys.readouterr().out
    assert ref in only and mid_ref not in only
    assert cli_main(["runs", "--pipeline", "try-fresh"]) == 0
    assert mid_ref not in capsys.readouterr().out

    口 = Step(step_num=2, name="Export the draft step's output",
             purpose="The bytes are copied and the run used is named")
    dest = tmp_project.parent / f"{tmp_project.name}-out"
    dest.mkdir()
    assert cli_main(["export", "--run", ref, "--slot", "result",
                     "--dest", str(dest)]) == 0
    out = capsys.readouterr()
    assert (dest / "result.csv").read_text(encoding="utf-8").startswith("hello")
    assert "from the draft run of" in out.err and "try-fresh" in out.err
    # Case 17, draft half: --path --all names each line by its slot.
    assert cli_main(["export", "--run", ref, "--all", "--path"]) == 0
    slot, path = capsys.readouterr().out.strip().split("\t")
    assert slot == "result" and Path(path).name == "result.csv"

    口 = Step(step_num=3, name="Refusals name why",
             purpose="A short string, an unused sample, deleted outputs, a removed draft")
    assert cli_main(["export", "--run", f"draft:{module}.fresh", "--slot",
                     "result", "--dest", str(dest)]) == 1
    assert DRAFT_REF_FORM in capsys.readouterr().err
    assert cli_main(["export", "--run", spell_draft_ref(module, "fresh", "fresh", "s9"),
                     "--slot", "result", "--dest", str(dest)]) == 1
    assert "used samples: s1" in capsys.readouterr().err
    delete_draft_outputs_and_clear(
        module, "fresh", tmp_project,
        planned=plan_draft_delete(module, "fresh", outputs_only=True,
                                  root=tmp_project).token)
    assert cli_main(["export", "--run", ref, "--slot", "result", "--path"]) == 1
    assert "has no draft run" in capsys.readouterr().err
    remove_draft_and_clear(module, "fresh", tmp_project,
                           planned=plan_draft_delete(module, "fresh",
                                                     root=tmp_project).token)
    assert cli_main(["export", "--run", ref]) == 1
    assert "registered or removed" in capsys.readouterr().err


@workflow(purpose="wfc try (and a wired shell) on a draft with two required "
                  "inputs no parent entry feeds is refused naming both and "
                  "saying to feed the others with --parent-run-id; nothing runs "
                  "and no pipeline row is written")
def test_a_try_with_two_unfed_required_inputs_is_refused_naming_both(
        tmp_project, monkeypatch, capsys):
    scenario, module, up_run, _mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)
    before = set(_pipeline_rows())
    capsys.readouterr()

    口 = Step(step_num=1, name="Try with one parent entry",
             purpose="data and draft_table are both left to feed: refused, "
                     "naming both and the flag that feeds the others")
    argv = ["try", str(folder), "--module", module, "--sample", "s1",
            "--parent-run-id", f"up_table:table:{up_run}"]
    with _launch(tmp_project, scenario, monkeypatch):
        assert cli_main(argv) == 1
    err = capsys.readouterr().err
    assert "'data', 'draft_table'" in err and "--parent-run-id" in err, err
    assert set(_pipeline_rows()) == before

    口 = Step(step_num=2, name="The wired shell asks the same rule",
             purpose="wfc shell --method with the same inputs is refused the same way")
    from tests.fixtures.fakes.docker import stub_dev_loop_launch

    launched: list[list[str]] = []
    stub_dev_loop_launch(monkeypatch, lambda argv: launched.append(argv) or 0)
    argv = ["shell", "--method", str(folder), "--module", module, "--sample", "s1",
            "--parent-run-id", f"up_table:table:{up_run}"]
    assert cli_main(argv) == 1
    err = capsys.readouterr().err
    assert "'data', 'draft_table'" in err and "--parent-run-id" in err, err
    assert launched == []


def test_try_help_marks_module_and_sample_required_and_json_prints_the_outcome(
        tmp_project, monkeypatch, capsys):
    """The usage line shows both flags as required; ``--json`` is one object on stdout."""
    import pytest

    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)
    capsys.readouterr()

    with pytest.raises(SystemExit):
        cli_main(["try", "--help"])
    shown = capsys.readouterr().out
    usage = shown.split("\n\n")[0]
    assert "--module MODULE" in usage and "[--module" not in usage, usage
    assert "--sample SAMPLE" in usage and "[--sample" not in usage, usage
    with pytest.raises(SystemExit) as refused:
        cli_main(["try", str(folder), "--module", module])
    assert refused.value.code == 2
    assert "--sample" in capsys.readouterr().err

    with _launch(tmp_project, scenario, monkeypatch):
        rc = cli_main([*_try_argv(folder, module, up_run, mid_ref), "--json"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert out.out.startswith("{"), out.out
    outcome = json.loads(out.out)
    ref = spell_draft_ref(module, "fresh", "fresh", "s1")
    assert outcome["ok"] is True and outcome["run"] == ref
    assert outcome["module"] == module and outcome["method"] == "fresh"
    assert outcome["sample"] == "s1"
    assert outcome["outputs"] == [
        {"slot": "result",
         "export": f"wfc export --run {ref} --slot result --dest <dir>"}]
    assert "Adds " in out.err


def test_runs_names_a_recorded_rows_pipeline_by_its_name(tmp_project, monkeypatch, capsys):
    """A recorded run's PIPELINE cell is its pipeline's name; ``--pipeline`` takes the id."""
    from wfc.execution.pipeline_outcome import record_pipeline_launch

    _scenario, _module, up_run, _mid_ref = built_draft_project(tmp_project, monkeypatch)
    # The harness document carries no name; a named launch records one, as
    # the canvas's launch does.
    record_pipeline_launch("try-base", {"name": "try_base", "nodes": []})
    capsys.readouterr()

    assert cli_main(["runs"]) == 0
    rows = [line.split() for line in capsys.readouterr().out.splitlines()]
    (up_row,) = [row for row in rows if row and row[0] == str(up_run)]
    assert up_row[1] == "try_base"
    assert cli_main(["runs", "--pipeline", "try-base"]) == 0
    assert any(line.split()[:1] == [str(up_run)]
               for line in capsys.readouterr().out.splitlines())


def test_export_run_refuses_a_draft_string_naming_the_draft_step(
        tmp_project, monkeypatch, capsys):
    """``wfc export-run --run draft:...`` lists Run bundle's named refusal; no zip."""
    _scenario, _module, _up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    out = tmp_project.parent / f"{tmp_project.name}-bundle.zip"
    capsys.readouterr()

    assert cli_main(["export-run", "--run", mid_ref, "--out", str(out), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "draft step 'mid'" in err and "register the draft" in err
    assert "Nothing was written." in err
    assert not out.exists()


@workflow(purpose="Case 10, the --method half:`wfc shell --method` gives the "
                  "container a step's variables from the one builder (the "
                  "session scratch as WFC_RUN_DIR, the inputs, params, declared "
                  "names, PYTHONUNBUFFERED and the env's bin on PATH), so "
                  "RunContext() constructs from them")
def test_a_wired_shell_session_gets_a_steps_variables(tmp_project, monkeypatch, capsys):
    from tests.fixtures.fakes.docker import stub_dev_loop_launch
    from wfc import layout
    from wfc_client.context import RunContext

    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)
    session = layout.draft_session_dir(tmp_project, module, "fresh")
    session.mkdir(parents=True)
    (session / "stale.txt").write_text("old", encoding="utf-8")
    launched: list[list[str]] = []
    stub_dev_loop_launch(monkeypatch, lambda argv: launched.append(argv) or 0)

    口 = Step(step_num=1, name="Start a wired shell",
             purpose="The env is method.yaml's; the scratch is emptied")
    argv = ["shell", "--method", str(folder), "--module", module, "--sample", "s1",
            "--params", json.dumps({"label": "hi"}),
            "--parent-run-id", f"up_table:table:{up_run}",
            "--parent-run-id", f"draft_table:table:{mid_ref}"]
    assert cli_main(argv) == 0, capsys.readouterr().err
    assert not (session / "stale.txt").exists()
    flags = launched[0]
    env = dict(flags[i + 1].split("=", 1) for i, flag in enumerate(flags) if flag == "-e")

    口 = Step(step_num=2, name="The variables are a step's",
             purpose="Container paths under /work, every input, the declared "
                     "names; RunContext() constructs from them on the host side")
    assert env["WFC_RUN_DIR"].startswith("/work/")
    assert env["PYTHONUNBUFFERED"] == "1" and "WFC_DECLARED_NAMES" in env
    paths = json.loads(env["WFC_INPUT_PATHS"])
    assert set(paths) == {"data", "up_table", "draft_table"}
    host_dir = str(tmp_project) + env["WFC_RUN_DIR"][len("/work"):]
    for name in ("WFC_PARAMS", "WFC_INPUT_PATHS", "WFC_DECLARED_NAMES"):
        monkeypatch.setenv(name, env[name])
    monkeypatch.setenv("WFC_RUN_DIR", host_dir)
    ctx = RunContext()
    assert ctx.params == {"label": "hi"}
    assert Path(ctx.run_dir) == session.resolve()
