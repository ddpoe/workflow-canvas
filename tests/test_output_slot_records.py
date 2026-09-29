"""Output slot records: outputs are found by slot, inputs record the slot that fed them.

A run's output records carry the contract slot each output fills, and its
input records carry the upstream slot each input consumed. Every reader
selects by slot, so a method may save an output under any file name. A run
whose output records do not all carry a slot has a malformed record: it is
never reused from the cache, and reading its outputs fails naming the run to
re-run. A run-reference link whose handle is not a slot of the referenced
run is an invalid reference and fails the load at Run.

The harness-driven scenarios run on the stub rung: the method process is
faked at the dispatch boundary, while every phase of run_step runs for real.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import Session, select

from tests.fixtures.fakes import bind_provider
from tests.harness import Scenario, node, run_scenario, selector, wire
from tests.harness.scenario import DEFAULT_MODULE, DEFAULT_SAMPLE, Behavior

VARIANT = "default"


def _target(node_id: str) -> tuple[str, str, str]:
    """The ``(node, sample, variant)`` target of a default-sample node."""
    return (node_id, DEFAULT_SAMPLE, VARIANT)


def _drop_slots(run_id: int) -> None:
    """Clear the slot on every output record of a run, making the record malformed."""
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        for ro in session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).all():
            ro.slot = None
            session.add(ro)
        session.commit()


def _two_output_classifier(**inputs_kwargs) -> list:
    """A selector feeding a classifier that publishes ``predictions`` and ``model``.

    The two slots are published under file names that share nothing with the
    slot names, so a listing that pairs the two cannot be mistaken for one
    that prints either of them twice.
    """
    return [
        selector(),
        node("classifier", inputs=[wire("sel")],
             outputs={"predictions": ".csv", "model": ".pkl"},
             output_files={"predictions": "labels.csv",
                           "model": "weights.pkl"}),
    ]


# =============================================================================
# Output file names are the method's own
# =============================================================================

@workflow(purpose="A method saves an output under its own file name and the "
                  "next step wired to that output's slot receives that file")
def test_downstream_receives_output_saved_under_its_own_file_name(
    git_project, monkeypatch
):
    口 = Step(step_num=1, name="Run an upstream that saves slot 'merged' as "
                                "merged_table.csv, and a consumer wired to 'merged'",
             purpose="The document declares the slot's file as merged.csv; the "
                     "method saves merged_table.csv and records it for the slot, "
                     "so only a lookup by slot finds it")
    scn = Scenario(
        nodes=[
            selector(),
            node("merger", inputs=[wire("sel")], outputs={"merged": ".csv"},
                 behavior=Behavior(saved_files={"merged": "merged_table.csv"})),
            node("consumer",
                 inputs=[wire("merger", source_slot="merged", target_slot="data")],
                 behavior=Behavior(echo_input="data")),
        ],
    )
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="The consumer receives merged_table.csv",
             purpose="The consumer runs, and its data slot holds the file the "
                     "upstream method saved for 'merged'")
    assert obs.exit_code(_target("consumer")) == 0
    slot_paths = obs.phase_args("dispatch", _target("consumer"))["slot_paths"]
    assert [Path(p).name for p in slot_paths["data"]] == ["merged_table.csv"]


@workflow(purpose="Two slots saved as files that share a name keep one record "
                  "each, and each slot resolves to its own file")
def test_same_named_files_keep_one_record_per_slot(git_project, monkeypatch):
    from wfc.storage import resolve_input

    scn = Scenario(nodes=[
        selector(),
        node("reporter", inputs=[wire("sel")],
             outputs={"qc": ".csv", "final": ".csv"},
             behavior=Behavior(saved_files={"qc": "qc/report.csv",
                                            "final": "final/report.csv"})),
    ])
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)
    run_id = obs.runs[_target("reporter")].run_id

    rows = obs.output_rows_for_run(run_id)
    assert sorted((r["slot"], r["output_name"]) for r in rows) == [
        ("final", "report.csv"), ("qc", "report.csv"),
    ]
    assert Path(resolve_input(run_id, slot="qc")).parent.name == "qc"
    assert Path(resolve_input(run_id, slot="final")).parent.name == "final"


@workflow(purpose="A run that saves two slots to the same file fails when its "
                  "outputs are collected, with an error naming both slots and "
                  "the path")
def test_two_slots_saved_to_one_file_fail_the_run(git_project, monkeypatch):
    scn = Scenario(nodes=[
        selector(),
        node("splitter", inputs=[wire("sel")],
             outputs={"left": ".csv", "right": ".csv"},
             behavior=Behavior(saved_files={"left": "shared.csv",
                                            "right": "shared.csv"})),
    ])
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(_target("splitter")) == 1
    row = obs.run_row(_target("splitter"))
    assert row["status"] == "failed"
    message = row["error_message"] or ""
    assert "left" in message
    assert "right" in message
    assert "shared.csv" in message
    assert obs.output_rows_for_run(obs.runs[_target("splitter")].run_id) == []


@workflow(purpose="wfc export --all places outputs that share a file name under "
                  "their slot folders and keeps every other output's bare file "
                  "name")
def test_export_all_puts_clashing_file_names_under_slot_folders(
    tmp_project, tmp_path
):
    from wfc.cli import cli_main
    from wfc.persistence import Method, Module, Run, RunOutput, get_session
    from wfc.storage import archive_outputs

    staging = tmp_project / "staging"
    contents = {
        "qc": ("qc/report.csv", b"qc\n1\n"),
        "final": ("final/report.csv", b"final\n2\n"),
        "summary": ("summary.csv", b"summary\n3\n"),
    }
    with get_session() as session:
        mod = Module(name="reports")
        session.add(mod)
        session.flush()
        meth = Method(name="report", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.flush()
        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.flush()
        run_id = run.id
        for slot, (rel, content) in contents.items():
            path = staging / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            session.add(RunOutput(run_id=run_id, slot=slot, output_name=path.name,
                                  artifact_path=str(path),
                                  artifact_type="method_file"))
        session.commit()
    archive_outputs(tmp_project, run_id=run_id)

    dest = tmp_path / "out"
    assert cli_main(["export", str(run_id), "--all", str(dest)]) == 0
    assert (dest / "qc" / "report.csv").read_bytes() == contents["qc"][1]
    assert (dest / "final" / "report.csv").read_bytes() == contents["final"][1]
    assert (dest / "summary.csv").read_bytes() == contents["summary"][1]


def test_a_slot_folder_named_like_another_output_cannot_be_named_apart():
    """Splitting a clash into slot folders must not reuse another output's name.

    ``qc`` and ``final`` both save ``report.csv``, so each goes under its slot
    folder; a third output whose plain name is ``qc`` would then stand beside
    the ``qc/`` folder under the same name.
    """
    from wfc.persistence import RunOutput
    from wfc.storage import ResolveOutputError, output_export_names

    rows = [
        RunOutput(run_id=7, slot="qc", output_name="report.csv",
                  artifact_path="/cache/qc/report.csv", artifact_type="method_file"),
        RunOutput(run_id=7, slot="final", output_name="report.csv",
                  artifact_path="/cache/final/report.csv",
                  artifact_type="method_file"),
        RunOutput(run_id=7, slot="notes", output_name="qc",
                  artifact_path="/cache/notes/qc", artifact_type="method_file"),
    ]

    with pytest.raises(ResolveOutputError, match="'qc'"):
        output_export_names(rows)
    assert output_export_names(rows[:2]) == ["qc/report.csv", "final/report.csv"]


@workflow(purpose="A run whose two outputs share a file name is listed in the "
                  "canvas Files list as qc/report.csv and final/report.csv, "
                  "each listed name serves that output's own bytes, and a "
                  "single-output export of one of them keeps the bare name")
def test_clashing_outputs_list_under_slot_folders_and_export_singly_by_bare_name(
    tmp_project, monkeypatch, tmp_path
):
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.cli import cli_main
    from wfc.storage import archive_outputs, resolve_input

    from tests.fixtures.routes import canvas_client, completed_run

    口 = Step(step_num=1, name="Complete and archive a run that saves qc and "
                                "final as report.csv",
             purpose="The run's two outputs share one file name in two "
                     "folders; `wfc cache archive` puts them in the cache "
                     "the Files list reads")
    run = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="reporter",
        outputs={"qc": ".csv", "final": ".csv"},
        behavior=Behavior(saved_files={"qc": "qc/report.csv",
                                       "final": "final/report.csv"}),
    )
    run_id = run.run_id
    archive_outputs(tmp_project, run_id=run_id)
    by_slot = {slot: Path(resolve_input(run_id, slot=slot)).read_bytes()
               for slot in ("qc", "final")}
    assert by_slot["qc"] != by_slot["final"], by_slot

    口 = Step(step_num=2, name="The Files list names each output under its slot",
             purpose="Each listed name serves the bytes of the output it names")
    client = canvas_client(tmp_project, monkeypatch)
    bind_provider(monkeypatch, WfcProvider(str(tmp_project)))
    listing = client.get(f"/api/wfc/run/{run_id}/artifacts")
    assert listing.status_code == 200, listing.text
    assert sorted(a["name"] for a in listing.json()) == [
        "final/report.csv", "qc/report.csv"]
    for slot in ("qc", "final"):
        served = client.get(f"/api/wfc/run/{run_id}/artifact/{slot}/report.csv")
        assert served.status_code == 200, served.text
        assert served.content == by_slot[slot]

    口 = Step(step_num=3, name="Export qc alone",
             purpose="The single-output export keeps the bare file name")
    dest = tmp_path / "one"
    dest.mkdir()
    assert cli_main(["export", str(run_id), "qc", str(dest)]) == 0
    assert (dest / "report.csv").read_bytes() == by_slot["qc"]
    assert not (dest / "qc").exists()


# =============================================================================
# Inputs record the slot that fed them; lineage wires it
# =============================================================================

@workflow(purpose="A consumer of a two-output upstream records which upstream "
                  "slot fed its input, the canvas run record carries it, and "
                  "lineage synthesis wires that edge from the slot the consumer "
                  "used")
def test_input_record_and_lineage_carry_the_source_slot(git_project, monkeypatch):
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.lineage.synthesis import synthesize_lineage_pipeline
    from wfc.persistence import RunInput, get_session

    口 = Step(step_num=1, name="Run a consumer wired to the upstream's second output",
             purpose="The plotter's data slot is fed by the classifier's 'model' "
                     "output, not its first-declared 'predictions' output")
    scn = Scenario(nodes=_two_output_classifier() + [
        node("plotter",
             inputs=[wire("classifier", source_slot="model", target_slot="data")]),
    ])
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)
    assert obs.exit_code(_target("plotter")) == 0
    classifier_id = obs.runs[_target("classifier")].run_id
    plotter_id = obs.runs[_target("plotter")].run_id

    口 = Step(step_num=2, name="The input record carries the source slot",
             purpose="The plotter's run_inputs row names the input slot, the "
                     "upstream run and the upstream slot 'model'")
    with get_session() as session:
        inputs = session.exec(
            select(RunInput).where(RunInput.run_id == plotter_id)
        ).all()
        recorded = [(r.input_name, r.source_run_id, r.source_slot) for r in inputs]
    assert recorded == [("data", classifier_id, "model")]

    口 = Step(step_num=3, name="The canvas run record surfaces it",
             purpose="The provider's parents entry for the plotter carries the "
                     "recorded source slot")
    records = WfcProvider(str(git_project)).run_records()
    assert records[str(plotter_id)].parents == [
        {"slot": "data", "sourceRunId": str(classifier_id), "sourceSlot": "model"},
    ]

    口 = Step(step_num=4, name="Lineage wires the edge from 'model'",
             purpose="The synthesized classifier-to-plotter edge's sourceHandle is "
                     "the slot the plotter consumed")
    document = synthesize_lineage_pipeline(records, str(plotter_id))
    node_for = {n["method"]: n["id"] for n in document["nodes"]
                if n["type"] == "method"}
    edge = next(link for link in document["links"]
                if link["source"] == node_for["classifier"]
                and link["target"] == node_for["plotter"])
    assert edge["sourceHandle"] == "model"


# =============================================================================
# Malformed records and invalid references
# =============================================================================

@workflow(purpose="A completed run with an output record that lacks a slot is "
                  "never returned as a cache hit, so the step recomputes")
def test_run_with_malformed_record_is_not_a_cache_hit(git_project, monkeypatch):
    from wfc.execution.claim import lookup_cache_hit

    obs = run_scenario(Scenario(), root=git_project, monkeypatch=monkeypatch)
    target = _target("n1")
    run_id = obs.runs[target].run_id
    cache_key = obs.run_row(target)["cache_key"]
    assert lookup_cache_hit("n1", DEFAULT_MODULE, DEFAULT_SAMPLE, cache_key) == run_id

    _drop_slots(run_id)

    assert lookup_cache_hit("n1", DEFAULT_MODULE, DEFAULT_SAMPLE, cache_key) is None


@workflow(purpose="Reading the outputs of a run with a malformed record fails "
                  "with a message naming the run and saying to re-run it, for a "
                  "downstream step's input and for the History Files list")
def test_reading_a_malformed_run_names_the_run_to_re_run(
    canvas_db, tmp_project, canvas_client, monkeypatch, capsys
):
    from wfc.canvas import state as canvas_state
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.persistence import Method, Module, Run, RunOutput
    from wfc.storage import archive_outputs, resolve_input

    staging = tmp_project / "staging"
    staging.mkdir()
    (staging / "norm.csv").write_bytes(b"a\n1\n")
    with Session(canvas_db) as session:
        mod = Module(name="prep")
        session.add(mod)
        session.flush()
        meth = Method(name="normalize", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.flush()
        # Several runs, so the id the message carries is one a substring
        # of any other number in the text could not stand in for.
        for _ in range(4):
            session.add(Run(method_id=meth.id, sample="s0",
                            status="completed"))
        session.flush()
        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.flush()
        run_id = run.id
        session.add(RunOutput(run_id=run_id, slot=None, output_name="norm.csv",
                              artifact_path=str(staging / "norm.csv"),
                              artifact_type="method_file"))
        session.commit()
    archive_outputs(tmp_project, run_id=run_id)
    bind_provider(monkeypatch, WfcProvider(str(tmp_project)))

    capsys.readouterr()
    assert resolve_input(run_id) is None
    err = capsys.readouterr().err
    assert str(run_id) in err
    assert "normalize" in err
    assert "re-run" in err

    listing = canvas_client.get(f"/api/wfc/run/{run_id}/artifacts")
    assert listing.status_code >= 400, listing.text
    assert str(run_id) in listing.text
    assert "normalize" in listing.text
    assert "re-run" in listing.text


@workflow(purpose="A pipeline whose run-reference link names an output the "
                  "referenced run does not have fails when it loads for Run, "
                  "naming the reference node and the run and saying to re-run")
def test_invalid_run_reference_fails_the_load_naming_node_and_run(
    git_project, monkeypatch
):
    from wfc.execution.composer import load_pipeline_from_document

    obs = run_scenario(Scenario(nodes=_two_output_classifier()),
                       root=git_project, monkeypatch=monkeypatch)
    run_id = obs.runs[_target("classifier")].run_id
    document = {
        "nodes": [
            {"id": "ref", "type": "run_reference", "run_id": str(run_id),
             "label": "baseline"},
            {"id": "plot", "method": "plotter", "module": DEFAULT_MODULE,
             "env": "container:demo", "params": {}},
        ],
        "links": [
            {"source": "ref", "target": "plot", "source_slot": "bogus",
             "target_slot": "data"},
        ],
        "samples": [],
    }

    with pytest.raises(ValueError) as excinfo:
        load_pipeline_from_document(document)
    message = str(excinfo.value)
    assert "baseline" in message
    assert str(run_id) in message
    assert "re-run" in message


@workflow(purpose="A run-reference link that names no output on a run with "
                  "several fails when it loads for Run, naming the reference "
                  "and the run and listing the outputs to choose from")
def test_unnamed_reference_link_on_a_multi_output_run_lists_the_outputs(
    git_project, monkeypatch
):
    from wfc.execution.composer import load_pipeline_from_document

    obs = run_scenario(Scenario(nodes=_two_output_classifier()),
                       root=git_project, monkeypatch=monkeypatch)
    run_id = obs.runs[_target("classifier")].run_id
    document = {
        "nodes": [
            {"id": "ref", "type": "run_reference", "run_id": str(run_id),
             "label": "baseline"},
            {"id": "plot", "method": "plotter", "module": DEFAULT_MODULE,
             "env": "container:demo", "params": {}},
        ],
        "links": [
            {"source": "ref", "target": "plot", "target_slot": "data"},
        ],
        "samples": [],
    }

    with pytest.raises(ValueError) as excinfo:
        load_pipeline_from_document(document)
    message = str(excinfo.value)
    assert "baseline" in message
    assert str(run_id) in message
    assert "predictions (labels.csv)" in message
    assert "model (weights.pkl)" in message
