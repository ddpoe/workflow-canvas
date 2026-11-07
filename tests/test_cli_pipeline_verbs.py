"""The CLI's pipeline verbs: ``check-pipeline``, ``run-pipeline`` and ``run-method``.

Catalog case 7 (CLI half): every loader refusal kind -- a missing file, bad
JSON, a non-object, an unregistered method, a Graph refusal, a non-mapping
``param_sets``, an undeclared variable, an unknown key -- prints one
``ERROR:`` line and no traceback from ``wfc run-pipeline`` and from ``wfc
check-pipeline``, and exits 1 with nothing launched. Catalog case 9 (CLI
half): ``wfc run-method <module>.<method> --sample`` prints the saved path
and runs it, carries ``--params`` (the file named by their fingerprint) and
``--name``, reuses the same file the second time, refuses a different file at
the path in one ``ERROR:`` line, never overwriting it, and prints a refused
param before anything is saved.

Declared fixture deviations: none. The project is built by the harness
through production registration; the engine process is the only thing
stubbed (``fake_engine_process``), and the readiness probes say Docker is up
(``stub_readiness_probes``), so no Docker daemon is needed.
"""
from __future__ import annotations

import json

from axiom_annotations import Step, workflow

from tests.conftest import stub_readiness_probes
from tests.fixtures.fakes.docker import stub_docker_image_content, stub_docker_image_inspect
from tests.fixtures.pipeline_verbs import (
    docker_up_project,
    error_lines,
    fit_document,
    launching,
    refusal_kinds,
    write_document,
)
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import SELECTOR_ID
from wfc import layout



@workflow(purpose="Case 7, CLI half: for each loader refusal kind (missing file, "
                  "bad JSON, a non-object, an unregistered method, a Graph "
                  "refusal, a non-mapping param_sets, an undeclared variable, an "
                  "unknown key) run-pipeline and check-pipeline each exit 1 and "
                  "print the refusal as one ERROR: line, never a traceback, and "
                  "nothing is launched")
def test_every_loader_refusal_is_one_error_line_from_both_verbs(cli, tmp_project,
                                                                monkeypatch, tmp_path):
    口 = Step(step_num=1, name="Register fit",
             purpose="Production registration through the harness")
    docker_up_project(tmp_project, monkeypatch, node("fit", inputs=[wire(SELECTOR_ID)]))

    for kind, (content, words) in refusal_kinds().items():
        path = write_document(tmp_path, kind, content)
        口 = Step(step_num=1.1, name="run-pipeline the kind",
                 purpose="Exit 1, one ERROR: line carrying the kind's words, no "
                         "traceback, no engine launch")
        result, launches = launching(cli, "run-pipeline", "--pipeline", path)
        assert result.returncode == 1, (kind, result.stderr)
        errors = error_lines(result.stderr)
        assert len(errors) == 1, (kind, result.stderr)
        # The validator's refusals are indented under their one ERROR: line.
        assert all(w in result.stderr for w in words), (kind, result.stderr)
        assert "Traceback" not in result.stderr + result.stdout, kind
        assert launches == [], kind

        口 = Step(step_num=1.2, name="check-pipeline the kind",
                 purpose="Exit 1; each refusal one ERROR: line, the kind's among "
                         "them; no traceback")
        checked = cli("check-pipeline", "--pipeline", path)
        assert checked.returncode == 1, (kind, checked.stderr)
        errors = error_lines(checked.stderr)
        assert errors and "Traceback" not in checked.stderr + checked.stdout, kind
        assert len(errors) == len(set(errors)), (kind, errors)
        if not isinstance(content, dict):
            assert checked.stderr == result.stderr, kind


@workflow(purpose="check-pipeline gives the verdict without running: a document "
                  "it passes exits 0 saying so; --json prints the verdict as one "
                  "object with ok; a refused document exits 1 with ok false and "
                  "its ERROR: lines on stderr; a file it cannot read is "
                  "{ok: false, error}")
def test_check_pipeline_prints_the_verdict_and_json(cli, tmp_project, monkeypatch,
                                                    tmp_path):
    docker_up_project(tmp_project, monkeypatch, node("fit", inputs=[wire(SELECTOR_ID)]))

    口 = Step(step_num=1, name="A document it passes",
             purpose="Exit 0, no ERROR: line, stdout says it passes")
    good = write_document(tmp_path, "good", fit_document())
    result = cli("check-pipeline", "--pipeline", good)
    assert result.returncode == 0, result.stderr
    assert error_lines(result.stderr) == [] and "passes" in result.stdout

    口 = Step(step_num=2, name="--json on it",
             purpose="One object: ok true and the verdict's keys")
    result = cli("check-pipeline", "--pipeline", good, "--json")
    assert result.returncode == 0, result.stderr
    obj = json.loads(result.stdout)
    assert obj["ok"] is True and obj["valid"] is True and obj["errors"] == []
    assert {"warnings", "draftRefusals"} <= set(obj)

    口 = Step(step_num=3, name="--json on a refused document",
             purpose="Exit 1, ok false, its errors in the object and as ERROR: "
                     "lines on stderr")
    keyed = fit_document()
    keyed["nodes"][1]["colour"] = "red"
    result = cli("check-pipeline", "--pipeline", write_document(tmp_path, "keyed", keyed),
                 "--json")
    assert result.returncode == 1
    obj = json.loads(result.stdout)
    assert obj["ok"] is False and obj["valid"] is False
    assert [f"ERROR: {e}" for e in obj["errors"]] == error_lines(result.stderr)

    口 = Step(step_num=4, name="--json on a missing file",
             purpose="Exit 1, {ok: false, error}")
    result = cli("check-pipeline", "--pipeline", str(tmp_path / "absent.json"), "--json")
    assert result.returncode == 1
    obj = json.loads(result.stdout)
    assert obj["ok"] is False and "absent.json" in obj["error"]


def _param_node():
    """Method node ``a`` on the selector, declaring an int ``n`` (default 5)."""
    return node("a", inputs=[wire(SELECTOR_ID)], param_types={"n": "int"},
                param_defaults={"n": 5})


@workflow(purpose="Case 9, CLI half: run-method prints the saved path, then runs "
                  "it; run again it reuses the same file and runs; --params in "
                  "any spacing reaches the node and the file carries its "
                  "fingerprint; --name saves pipelines/<name>.json; --save "
                  "writes where it is told; a different file at the path is "
                  "refused in one ERROR: line naming it, never overwritten, and "
                  "nothing is launched")
def test_run_method_saves_runs_and_never_overwrites(cli, tmp_project, monkeypatch,
                                                    tmp_path):
    from wfc.execution import params_fingerprint

    project = docker_up_project(tmp_project, monkeypatch, _param_node())
    spec = project.scenario.node_by_id("a")
    qualified = f"{spec.module}.{spec.method_name}"
    path = layout.one_method_pipeline_path(project.root, spec.module,
                                           spec.method_name, "S1")

    口 = Step(step_num=1, name="Run the method on the sample",
             purpose="The saved path is printed and the engine launched once")
    result, launches = launching(cli, "run-method", qualified, "--sample", "S1")
    assert result.returncode == 0, result.stderr
    assert path.is_file() and str(path) in result.stdout
    assert len(launches) == 1

    口 = Step(step_num=2, name="Run it again",
             purpose="The same file, untouched, named and run")
    before = path.stat().st_mtime_ns
    result, launches = launching(cli, "run-method", qualified, "--sample", "S1")
    assert result.returncode == 0, result.stderr
    assert str(path) in result.stdout and path.stat().st_mtime_ns == before
    assert len(launches) == 1

    口 = Step(step_num=3, name="--params, then the same params spelled otherwise",
             purpose="The node carries them; the file carries their fingerprint; "
                     "other spacing reuses that file")
    with_params = layout.one_method_pipeline_path(
        project.root, spec.module, spec.method_name, "S1", params_fingerprint({"n": 3}))
    result, launches = launching(cli, "run-method", qualified, "--sample", "S1",
                                  "--params", '{"n": 3}')
    assert result.returncode == 0, result.stderr
    assert str(with_params) in result.stdout and len(launches) == 1
    saved = json.loads(with_params.read_text(encoding="utf-8"))
    assert [n["params"] for n in saved["nodes"] if n["type"] == "method"] == [{"n": 3}]
    result, _ = launching(cli, "run-method", qualified, "--sample", "S1",
                          "--params", '{ "n" :3 }')
    assert result.returncode == 0 and str(with_params) in result.stdout, result.stderr

    口 = Step(step_num=4, name="--name",
             purpose="pipelines/<name>.json, the document carrying the name")
    result, _ = launching(cli, "run-method", qualified, "--sample", "S1",
                          "--name", "first-try")
    named = layout.named_pipeline_path(project.root, "first-try")
    assert result.returncode == 0 and str(named) in result.stdout, result.stderr
    assert json.loads(named.read_text(encoding="utf-8"))["name"] == "first-try"

    口 = Step(step_num=5, name="--save elsewhere",
             purpose="The document is written at the given path, which is printed")
    elsewhere = tmp_path / "mine" / "one.json"
    result, launches = launching(cli, "run-method", qualified, "--sample", "S1",
                                  "--save", str(elsewhere))
    assert result.returncode == 0, result.stderr
    assert str(elsewhere) in result.stdout and len(launches) == 1
    assert json.loads(elsewhere.read_text(encoding="utf-8")) == json.loads(
        path.read_text(encoding="utf-8"))

    口 = Step(step_num=6, name="A different file at the path",
             purpose="One ERROR: line naming the path; the file kept; nothing "
                     "launched")
    path.write_text('{"name": "mine"}\n', encoding="utf-8")
    result, launches = launching(cli, "run-method", qualified, "--sample", "S1")
    assert result.returncode == 1
    errors = error_lines(result.stderr)
    assert len(errors) == 1 and str(path) in errors[0], result.stderr
    assert "--name" in result.stderr and "Traceback" not in result.stderr
    assert path.read_text(encoding="utf-8") == '{"name": "mine"}\n'
    assert launches == []


@workflow(purpose="Case 3.1 at the run-method door: a param the verdict refuses "
                  "is printed as the refused-document ERROR: line with a line "
                  "naming the node and the parameter, exit 1, before anything is "
                  "saved or launched")
def test_run_method_prints_a_refused_param_before_saving(cli, tmp_project,
                                                         monkeypatch):
    project = docker_up_project(tmp_project, monkeypatch, _param_node())
    spec = project.scenario.node_by_id("a")
    result, launches = launching(cli, "run-method", f"{spec.module}.{spec.method_name}",
                                  "--sample", "S1", "--params", '{"n": "three"}')
    assert result.returncode == 1, result.stderr
    lines = result.stderr.strip().splitlines()
    assert lines[0] == "ERROR: the pipeline document is refused:", result.stderr
    assert error_lines(result.stderr) == [lines[0]], result.stderr
    assert any("'a'" in line and "'n'" in line for line in lines[1:]), result.stderr
    assert not layout.project_pipelines_dir(project.root).exists()
    assert launches == [] and "Traceback" not in result.stderr


@workflow(purpose="Case 3, CLI line: run-pipeline over a document whose draft the "
                  "draft preflight refuses (its method.yaml names an env that is "
                  "not registered: a fail of the Registration check) exits 1 with "
                  "the refused-document ERROR: line and a line naming the node and "
                  "the check, never a traceback, and launches nothing")
def test_run_pipeline_prints_the_draft_preflight_s_refusal(cli, tmp_project,
                                                           monkeypatch, tmp_path):
    import re

    import yaml

    from wfc.registration import read_draft

    口 = Step(step_num=1, name="A project whose mid is a draft",
             purpose="sel -> mid over one sample, mid pinned to its draft")
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    stub_readiness_probes(monkeypatch, docker="ok")
    scenario = Scenario(nodes=[selector(), node("mid", inputs=[wire(SELECTOR_ID)])],
                        samples=["S1"], pipeline_id="drafted", name="drafted",
                        drafts=("mid",))
    mid = scenario.node_by_id("mid")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="The draft's method.yaml names an unregistered env",
             purpose="The Registration check's fail, which only the preflight sees")
    path = read_draft(mid.module, mid.method_name, project.root).folder / "method.yaml"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    contract["env"] = "no-such-env"
    path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")

    口 = Step(step_num=3, name="run-pipeline refuses before the launch",
             purpose="Exit 1; the header and a line naming node mid and the "
                     "Registration check; no traceback; no engine launch")
    result, launches = launching(cli, "run-pipeline", "--pipeline",
                                  str(project.pipeline_json))
    assert result.returncode == 1, result.stderr
    assert "Traceback" not in result.stderr + result.stdout, result.stderr
    lines = result.stderr.strip().splitlines()
    assert lines[0] == "ERROR: the pipeline document is refused:", result.stderr
    assert error_lines(result.stderr) == [lines[0]], result.stderr
    assert any(re.match(r"  node 'mid': .*Registration check.*no-such-env", line)
               for line in lines[1:]), result.stderr
    assert launches == []


def test_run_method_and_run_pipeline_argument_rules(cli, tmp_project, monkeypatch):
    """run-pipeline needs --pipeline; run-method needs the method and --sample;
    a --params that is not a JSON object exits 2 naming the flag."""
    docker_up_project(tmp_project, monkeypatch, node("a", inputs=[wire(SELECTOR_ID)]))
    for argv, named in ((("run-pipeline",), "--pipeline"),
                        (("run-method", "m.a"), "--sample"),
                        (("run-method", "--sample", "S1"), "method"),
                        (("run-method", "m.a", "--sample", "S1", "--params", "n=3"),
                         "--params"),
                        (("run-method", "m.a", "--sample", "S1", "--params", "[3]"),
                         "--params"),
                        (("run-method", "m.a", "--sample", "S1", "--params",
                          '{"n": NaN}'), "--params")):
        result, launches = launching(cli, *argv)
        assert result.returncode == 2, (argv, result.stderr)
        assert "usage:" in result.stderr and launches == [], argv
        assert named in result.stderr, (argv, result.stderr)


def test_an_unknown_verb_s_choices_leave_out_the_hidden_verbs(cli):
    """argparse's invalid-choice message lists the verbs `wfc --help` lists."""
    result = cli("no-such-verb")
    assert result.returncode == 2
    assert "invalid choice: 'no-such-verb'" in result.stderr, result.stderr
    listed = result.stderr.split("(choose from ", 1)[1].split(")", 1)[0].split(", ")
    assert "run-pipeline" in listed and "check-pipeline" in listed, listed
    assert not {"restore-sample", "fail_pipeline", "seed", "run-step",
                "pipeline-summary"} & set(listed), listed


def _outside_any_project(tmp_path_factory, monkeypatch):
    """A folder outside every project, made the current directory, no root pinned."""
    from wfc.persistence import reset_engine

    elsewhere = tmp_path_factory.mktemp("no-project")
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("WFC_PROJECT_ROOT", raising=False)
    reset_engine()
    return elsewhere


def test_check_pipeline_outside_a_project_says_how_to_name_the_project(
        cli, tmp_path_factory, monkeypatch):
    """The no-project error says the file can be anywhere and how to name the project.

    It keeps the prefix the other verbs print and the folder the search
    started from, and names --project-root.
    """
    elsewhere = _outside_any_project(tmp_path_factory, monkeypatch)
    path = write_document(elsewhere, "good", fit_document())
    result = cli("check-pipeline", "--pipeline", path)
    assert result.returncode == 1
    assert error_lines(result.stderr)[0].startswith("ERROR: No wfc project found:"), \
        result.stderr
    assert str(elsewhere.resolve()) in result.stderr, result.stderr
    assert "--project-root" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr


def test_check_pipeline_names_a_missing_file_even_outside_a_project(
        cli, tmp_path_factory, monkeypatch):
    """A missing pipeline file is its own error, before any project is looked for."""
    elsewhere = _outside_any_project(tmp_path_factory, monkeypatch)
    missing = elsewhere / "absent.json"
    result = cli("check-pipeline", "--pipeline", str(missing))
    assert result.returncode == 1
    assert error_lines(result.stderr) == [f"ERROR: Pipeline file not found: {missing}"], \
        result.stderr


def test_check_pipeline_checks_a_file_kept_outside_the_project_with_project_root(
        cli, tmp_project, monkeypatch, tmp_path_factory):
    """A pipeline file kept anywhere passes against the project --project-root names.

    The command runs from a folder outside every project, with no project
    pinned in the environment; the file sits in that folder too.
    """
    docker_up_project(tmp_project, monkeypatch, node("fit", inputs=[wire(SELECTOR_ID)]))
    elsewhere = _outside_any_project(tmp_path_factory, monkeypatch)
    path = write_document(elsewhere, "downloaded", fit_document())
    result = cli("check-pipeline", "--pipeline", path, "--project-root", str(tmp_project))
    assert result.returncode == 0, result.stderr
    assert error_lines(result.stderr) == [] and "passes" in result.stdout


def test_check_pipeline_prints_an_unreachable_database_as_itself(
        cli, tmp_project, monkeypatch, tmp_path):
    """Inside a project, an unreachable database is its own error, not "no project".

    The project is found; the load cannot reach the database. The one
    ERROR: line is the load's own message, with no "No wfc project found:"
    in front of it.
    """
    from wfc.persistence import reset_engine

    docker_up_project(tmp_project, monkeypatch, node("fit", inputs=[wire(SELECTOR_ID)]))
    good = write_document(tmp_path, "good", fit_document())
    monkeypatch.setenv("DATABASE_URL",
                       f"sqlite:///{tmp_project.as_posix()}/no-such-dir/x.db")
    reset_engine()
    try:
        result = cli("check-pipeline", "--pipeline", good)
    finally:
        reset_engine()
    assert result.returncode == 1
    lines = error_lines(result.stderr)
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("ERROR: The project database is unreachable"), lines
