"""The claim's door: every claim checks, normalizes and fills its params.

``wfc.execution.claim.resolve_claim_params`` runs Contracts' parameter check,
the ``int``/``float`` normalization and the declared-default fill against the
version a claim runs. ``run_claim`` passes each claim through it once and
hands dispatch its output (its ``pre_run`` call does not resolve again), a
direct ``pre_run`` passes its claim through it, and the ``check_cache`` and
``register_run`` verbs go through it against the newest version. So a run row records every value
the method received, ``WFC_PARAMS`` is that same dict, a hand-run verb gets
the pipeline's verdict and the pipeline's key, and the cache-status preview's
key for a sparse document is the key the claim builds.

Every method is registered through production registration: the harness
registers ``fit`` (v1), then its ``method.yaml`` params are edited and it is
registered again, minting v2, the version that declares the defaults. Runs
are produced by production's ``run_step``; the method process is faked at the
dispatch boundary only.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import spy_claim_resolutions
from tests.fixtures.fakes.engine import stub_method_process
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.drivers import STUB, drive_target
from tests.harness.observe import TargetRun
from tests.fixtures.routes import register_again_with_params
from tests.harness.scenario import DEFAULT_MODULE, SELECTOR_ID

M = DEFAULT_MODULE
#: The params fit's v2 declares: ``count`` is required with a default;
#: ``ratio``'s default is written as an int for a float parameter; ``seed``
#: declares a null default, which is none.
_DEFAULTED = {
    "count": {"type": "int", "required": True, "default": 3,
              "constraints": {"min": 1, "max": 10}},
    "ratio": {"type": "float", "required": False, "default": 1},
    "label": {"type": "str", "required": False, "default": "base"},
    "seed": {"type": "int", "required": False, "default": None},
}
TARGET = ("fit", "S1", "default")
#: fit's params as the harness document writes them, filled by v2.
FILLED = {"count": 2, "ratio": 1.0, "label": "base"}
_ENRICHED_ONLY = ("script", "slot_outputs", "slot_types", "env")


def _project(tmp_project, monkeypatch, pipeline_id: str):
    """Build selector -> fit (params ``{"count": 2}``), then register fit's v2.

    fit's one output is ``output: output.parquet``: a hand-run step carries no
    document, so collect takes its single-output fallback, ``output.parquet``.
    """
    scenario = Scenario(
        nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)],
                                params={"count": 2},
                                outputs={"output": ".parquet"},
                                output_files={"output": "output.parquet"})],
        samples=["S1"], pipeline_id=pipeline_id, name=pipeline_id.replace("-", "_"),
    )
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    register_again_with_params(project.root, scenario.node_by_id("fit"), _DEFAULTED)
    return project


def _run_row(run_id):
    from wfc.persistence import Run, get_session

    with get_session() as session:
        return session.get(Run, int(run_id))


def _canvas_form(project, params: dict | None = None) -> dict:
    """The sparse document the canvas would post for the built project."""
    enriched = json.loads(Path(project.pipeline_json).read_text(encoding="utf-8"))
    nodes = [{k: v for k, v in n.items() if k not in _ENRICHED_ONLY}
             for n in enriched["nodes"]]
    if params is not None:
        for n in nodes:
            if n["id"] == "fit":
                n["params"] = params
    links = [{"source": link["source"], "target": link["target"],
              "targetHandle": link.get("target_slot") or "data"}
             for link in enriched["links"]]
    rest = {k: v for k, v in enriched.items() if k not in ("nodes", "links")}
    return {**rest, "nodes": nodes, "links": links}


def _preview_key(document: dict) -> str:
    from wfc.execution.cache_status import cache_status

    report = cache_status(document)
    assert report.blocked_reason is None, report.blocked_reason
    (row,) = [r for r in report.rows if r.node_id == "fit"]
    return row.cache_key


@workflow(purpose="A pipeline whose node leaves declared defaults out, run on "
                  "a document never prepared, records the filled params on its "
                  "run row and hands the method the same dict as WFC_PARAMS; "
                  "the cache-status preview of the sparse document, and of the "
                  "document written at its defaults, predicts the key the "
                  "claim built, resolving its params once; the door changes "
                  "nothing on prepared params")
def test_a_sparse_pipeline_records_runs_and_keys_its_filled_params(
        tmp_project, monkeypatch):
    from wfc.execution.claim import resolve_claim_params
    from wfc.execution.prepare import prepare_document

    口 = Step(step_num=1, name="Register fit; v2 declares defaults",
             purpose="The harness document writes only count, unprepared")
    project = _project(tmp_project, monkeypatch, "claim-sparse")
    sparse = _canvas_form(project)

    口 = Step(step_num=2, name="Preview, then run the node in the pipeline",
             purpose="The claim reads the unprepared document; the method "
                     "process is the stub rung's local subprocess")
    predicted = _preview_key(sparse)
    resolutions = spy_claim_resolutions(monkeypatch)
    obs = drive_target(project, TARGET, monkeypatch=monkeypatch)
    assert obs.exit_code(TARGET) == 0, obs.runs[TARGET].ending
    run = _run_row(obs.run_id_sidecars[TARGET])

    口 = Step(step_num=3, name="One params dict, one key, one resolution",
             purpose="Run row == WFC_PARAMS == the filled dict; the preview's "
                     "key for the sparse and the written-at-default document "
                     "is the claimed key; the claim read fit's version list "
                     "once (run_claim's pre_run did not resolve again)")
    assert [(m, meth, node_id) for m, meth, _, node_id in resolutions] == [
        (M, "fit", run.node_id)]
    assert run.params == FILLED
    assert json.loads(obs.dispatch_env(TARGET)["WFC_PARAMS"]) == FILLED
    assert predicted == run.cache_key
    assert _preview_key(_canvas_form(project, dict(FILLED))) == run.cache_key

    口 = Step(step_num=4, name="The door on prepared params",
             purpose="A prepared node's params come back unchanged")
    (prepared,) = [n for n in prepare_document(sparse)["nodes"] if n["id"] == "fit"]
    assert prepared["params"] == FILLED
    assert resolve_claim_params(M, "fit", prepared["params"]) == prepared["params"]


@workflow(purpose="The hand-run verbs check-cache, pre-run and register, given "
                  "params that leave a default out and write an int as 2.0, "
                  "normalize and fill them as the pipeline does: check-cache "
                  "finds the pipeline's run under the pipeline's key, pre-run "
                  "is a cache hit, and register records the filled params")
def test_hand_run_verbs_key_and_record_what_the_pipeline_does(
        tmp_project, monkeypatch, cli):
    口 = Step(step_num=1, name="Run the node in the pipeline",
             purpose="Its run row carries the pipeline path's key")
    project = _project(tmp_project, monkeypatch, "claim-verbs")
    obs = drive_target(project, TARGET, monkeypatch=monkeypatch)
    run_id = int(obs.run_id_sidecars[TARGET])
    sparse = json.dumps({"count": 2.0})
    common = ["--method", "fit", "--module", M, "--sample", "S1",
              "--params", sparse]

    口 = Step(step_num=2, name="check-cache looks up the pipeline's key",
             purpose="count 2.0 keyed as 2, ratio and label filled: the "
                     "pipeline's run is the hit")
    result = cli("check_cache", *common)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(run_id)

    口 = Step(step_num=3, name="pre-run is a cache hit recording the filled params",
             purpose="The audit row's params are the filled dict")
    result = cli("pre_run", *common, "--git-commit", "abc123")
    assert result.returncode == 0, result.stderr
    flag, audit_id = result.stdout.strip().split(":")
    assert flag == "CACHED"
    assert _run_row(audit_id).params == FILLED

    口 = Step(step_num=4, name="register records the filled params",
             purpose="The legacy verb writes the same dict")
    result = cli("register_run", *common)
    assert result.returncode == 0, result.stderr
    assert _run_row(result.stdout.strip()).params == FILLED


@workflow(purpose="run-step --params hands the method, as WFC_PARAMS, the "
                  "normalized and filled dict its run row records; an "
                  "undeclared parameter is refused at run-step, check-cache, "
                  "pre-run and register with the pipeline validator's message "
                  "and no run row is written")
@pytest.mark.usefixtures("ready_preflight")
def test_run_step_params_are_filled_and_an_undeclared_one_is_refused_everywhere(
        tmp_project, monkeypatch, cli):
    from wfc.execution.validation import validation_refusals
    from wfc.persistence import Run, get_session
    from sqlmodel import select

    口 = Step(step_num=1, name="Hand-run the step with --params",
             purpose="count 4.0 and a written label; no document")
    project = _project(tmp_project, monkeypatch, "claim-run-step")
    record = TargetRun(target=TARGET)
    run_step = ["run-step", "--node-id", "fit", "--sample", "S1",
                "--method", "fit", "--module", M, "--pipeline-id", "hand-run",
                "--git-commit", "abc123"]
    with stub_method_process(project, SimpleNamespace(method_name="fit"),
                             record, monkeypatch, STUB):
        result = cli(*run_step, "--params", json.dumps({"count": 4.0, "label": "x"}))
    assert result.returncode == 0, result.stderr

    口 = Step(step_num=2, name="WFC_PARAMS is the run row's params",
             purpose="count is the int 4, ratio filled at 1.0, label kept")
    with get_session() as session:
        (run,) = session.exec(select(Run).where(Run.pipeline_id == "hand-run")).all()
    assert run.params == {"count": 4, "label": "x", "ratio": 1.0}
    assert type(run.params["count"]) is int
    assert json.loads(record.dispatch_env["WFC_PARAMS"]) == run.params

    口 = Step(step_num=3, name="An undeclared parameter at every verb",
             purpose="The pipeline validator's line; a verb with no node names "
                     "the method; nothing is written")
    bad = json.dumps({"count": 2, "bogus": 1})
    (pipeline_line,) = validation_refusals(
        _canvas_form(project, {"count": 2, "bogus": 1}))
    assert pipeline_line == (f"Node 'fit' ({M}.fit v2): parameter 'bogus' is not "
                             f"declared by v2; remove it")
    method_line = pipeline_line.replace("Node 'fit'", "Method 'fit'", 1)
    with get_session() as session:
        before = len(session.exec(select(Run)).all())

    result = cli(*run_step, "--params", bad)
    assert result.returncode == 1
    assert f"ERROR: pre_run failed: {pipeline_line}" in result.stderr
    common = ["--method", "fit", "--module", M, "--sample", "S1", "--params", bad]
    for verb, extra in (("check_cache", []), ("pre_run", ["--git-commit", "abc123"]),
                        ("register_run", [])):
        result = cli(verb, *common, *extra)
        assert result.returncode == 1, verb
        assert f"ERROR: {method_line}" in result.stderr, (verb, result.stderr)
    with get_session() as session:
        assert len(session.exec(select(Run)).all()) == before
