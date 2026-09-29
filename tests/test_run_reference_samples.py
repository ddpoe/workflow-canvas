"""Focused tests for run_reference sample inheritance and multi-output wiring.

Covers three load-bearing behaviours that keep a pipeline rooted solely at a
``run_reference`` from generating a zero-job Snakefile (``SAMPLES = []`` →
silent exit 0 → no Run rows):

1. ``resolve_run_reference_outputs`` populates ``sample`` from ``Run.sample``
   and ``output_paths`` as a {slot: artifact_path} dict for every RunOutput
   of the referenced run.
2. ``load_pipeline`` merges each run_reference's resolved sample into the
   pipeline sample list the same way ``input_selector`` does, and
   raises when a method-node pipeline would end up with zero samples.
3. ``load_pipeline``'s reference binding picks the artifact path for each
   edge by the link's ``source_slot``, so a multi-output run_reference fans
   different outputs into different downstream inputs correctly.

The load takes the resolved reference outputs as a value (the Graph unit's
seam), so only the fetch test (1) opens a database; the load tests hand in
the literal the resolver would return.
"""

import pytest
from axiom_annotations import workflow

from wfc.graph import load_pipeline
from wfc.persistence import get_session
from wfc.storage import resolve_run_reference_outputs

from tests.fixtures.routes import completed_run

ENV = "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _ref_outputs() -> dict:
    """What the resolver returns for run 62: sample ``SJ011``, two outputs.

    The shape is the resolver's whole answer (paired with a real one in the
    fetch test); the paths are placeholders the load tests read verbatim.
    """
    return {
        "run_id": "62",
        "output_paths": {
            "measurements": "/work/run62/measurements.csv",
            "labels": "/work/run62/labels.csv",
        },
        "sample": "SJ011",
        "method": "regionprops_quantification",
        "malformed": False,
    }


@pytest.fixture
def referenced_run(tmp_project, monkeypatch):
    """A completed run owning two outputs on sample ``SJ011``.

    The ``measurements`` slot saves its file as ``regionprops.csv``, so an
    output's saved name differs from its slot and a slot-keyed read is
    distinguishable from a name-keyed one. Used by the fetch test only;
    the load tests take literal values.
    """
    return completed_run(
        tmp_project, monkeypatch=monkeypatch,
        method="regionprops_quantification", module="analysis",
        sample="SJ011", outputs={"measurements": ".csv", "labels": ".csv"},
        output_files={"measurements": "regionprops.csv"},
    )


def test_resolve_populates_sample_and_all_output_paths(referenced_run):
    """DB lookup fills in Run.sample and every RunOutput keyed by slot name.

    The load tests' literal (``_ref_outputs``) is paired with the resolver's
    answer for a run production recorded: the same keys, the same sample,
    the same output slots; the paths are the run's recorded rows.
    """
    with get_session() as session:
        out = resolve_run_reference_outputs({
            "node_ref": {"run_id": str(referenced_run.run_id)},
        }, session)

    info = out["node_ref"]
    assert info["sample"] == "SJ011"
    assert info["output_paths"] == {
        row["slot"]: row["artifact_path"] for row in referenced_run.output_rows
    }
    assert set(info["output_paths"]) == {"measurements", "labels"}
    saved_names = {row["slot"]: row["output_name"]
                   for row in referenced_run.output_rows}
    assert saved_names["measurements"] != "measurements", saved_names
    assert saved_names["measurements"] not in info["output_paths"]

    literal = _ref_outputs()
    assert set(literal) == set(info)
    assert literal["sample"] == info["sample"]
    assert literal["method"] == info["method"]
    assert literal["malformed"] == info["malformed"]
    assert set(literal["output_paths"]) == set(info["output_paths"])


def test_run_reference_root_inherits_sample():
    """A run_reference-rooted pipeline compiles to SAMPLES=[Run.sample].

    Without the inheritance, samples stay [] and the generated Snakefile's
    rule all expand(..., sample=[]) runs zero jobs.
    """
    pipeline_json = {
        "nodes": [
            {
                "id": "ref_1", "type": "run_reference",
                "params": {},
                "run_id": "62",
            },
            {
                "id": "method_1", "type": "method", "env": ENV,
                "method": "binary_feature_labeling", "module": "analysis",
                "script": "methods/binary_feature_labeling/x.py",
                "params": {},
            },
        ],
        "links": [
            {"source": "ref_1", "target": "method_1",
             "source_slot": "measurements", "target_slot": "measurements"},
        ],
        "samples": [],
    }

    pipeline = load_pipeline(pipeline_json, contract_map={},
                             reference_outputs={"ref_1": _ref_outputs()})

    assert pipeline.samples == ["SJ011"]
    assert len(pipeline.steps) == 1
    step = pipeline.steps[0]
    # Label is the link's target_slot so the downstream method finds the
    # artifact under the slot it actually reads (not a synthetic label).
    assert step.run_ref_inputs == {"measurements": ["/work/run62/measurements.csv"]}


def test_load_pipeline_raises_when_pipeline_resolves_to_zero_samples():
    """No input_selector, and a referenced run carrying no sample → clear error.

    This is the defensive gate: a zero-sample method-node pipeline would
    otherwise silently generate an empty Snakefile DAG.
    """
    pipeline_json = {
        "nodes": [
            {
                "id": "ref_1", "type": "run_reference",
                "params": {},
                "run_id": "62",
            },
            {
                "id": "method_1", "type": "method", "env": ENV,
                "method": "foo", "module": "analysis",
                "script": "methods/foo/foo.py",
                "params": {},
            },
        ],
        "links": [
            {"source": "ref_1", "target": "method_1"},
        ],
        "samples": [],
    }

    no_sample = {
        "output_paths": {"measurements": "/work/run62/measurements.csv"},
        "sample": "",
    }
    with pytest.raises(ValueError, match="zero samples") as refusal:
        load_pipeline(pipeline_json, contract_map={},
                      reference_outputs={"ref_1": no_sample})
    # Both remedies are named: add a selector, or reference a sampled run.
    assert "Add an input_selector" in str(refusal.value)
    assert "referenced Run has a sample" in str(refusal.value)


def test_multi_output_per_edge_source_slot():
    """Each outgoing run_reference edge picks its own artifact by source_slot.

    Two method nodes each wire a different output of the same run. The
    engine must inject distinct run_ref paths — not duplicate the same
    one — on each downstream StepDef.
    """
    pipeline_json = {
        "nodes": [
            {
                "id": "ref_1", "type": "run_reference",
                "params": {},
                "run_id": "62",
            },
            {
                "id": "method_m", "type": "method", "env": ENV,
                "method": "consume_measurements", "module": "analysis",
                "script": "methods/consume_measurements/x.py",
                "params": {},
            },
            {
                "id": "method_l", "type": "method", "env": ENV,
                "method": "consume_labels", "module": "analysis",
                "script": "methods/consume_labels/x.py",
                "params": {},
            },
        ],
        "links": [
            {"source": "ref_1", "target": "method_m",
             "source_slot": "measurements", "target_slot": "data"},
            {"source": "ref_1", "target": "method_l",
             "source_slot": "labels", "target_slot": "data"},
        ],
        "samples": [],
    }

    pipeline = load_pipeline(pipeline_json, contract_map={},
                             reference_outputs={"ref_1": _ref_outputs()})

    step_by_id = {s.node_id: s for s in pipeline.steps}
    assert step_by_id["method_m"].run_ref_inputs == {
        "data": ["/work/run62/measurements.csv"],
    }
    assert step_by_id["method_l"].run_ref_inputs == {
        "data": ["/work/run62/labels.csv"],
    }


def test_link_naming_no_output_takes_the_referenced_run_s_only_output():
    """A reference link that names no output binds the run's one output.

    The rule an unnamed link between two methods already follows, applied to
    a reference: with one output there is one answer, so the link needs no
    ``source_slot``.
    """
    pipeline_json = {
        "nodes": [
            {
                "id": "ref_1", "type": "run_reference",
                # Canvas exports write method="" / module="" (empty strings)
                # on system nodes rather than omitting the keys. The loader
                # tolerates that shape as well as the absent-key shape used
                # by every other fixture in the suite.
                "method": "", "module": "",
                "params": {},
                "run_id": "62",
            },
            {
                "id": "method_1", "type": "method", "env": ENV,
                "method": "foo", "module": "analysis",
                "script": "methods/foo/foo.py",
                "params": {},
            },
        ],
        "links": [
            {"source": "ref_1", "target": "method_1"},
        ],
        "samples": [],
    }

    one_output = {
        "output_paths": {"measurements": "/work/run62/measurements.csv"},
        "sample": "SJ011",
    }
    pipeline = load_pipeline(
        pipeline_json, contract_map={},
        reference_outputs={"ref_1": one_output},
    )
    assert pipeline.samples == ["SJ011"]
    # A link with no target_slot takes the synthetic label.
    assert pipeline.steps[0].run_ref_inputs == {
        "run_ref_0": ["/work/run62/measurements.csv"],
    }


@workflow(
    purpose="A reference node that names a path instead of a run is refused "
            "at load, so no step is ever keyed against an artifact with no "
            "run behind it")
def test_reference_naming_no_run_is_refused_at_load():
    """A reference that resolved to no run refuses; it does not serve a path.

    A reference's contribution to a consumer's cache key is the referenced
    run's recorded output, reached through its run id. A document whose
    reference node carries only a path — the pre-run-id export shape — has
    no run behind it, so there is nothing to key against. The load refuses
    rather than serving the path, and the message names the reference.
    """
    pipeline_json = {
        "nodes": [
            {
                "id": "ref_1", "type": "run_reference",
                "method": "", "module": "",
                "params": {},
                # No run_id: the node names an artifact path, not a run.
                "output_path": "/work/run62/measurements.csv",
                "label": "measurements.csv",
            },
            {
                "id": "method_1", "type": "method", "env": ENV,
                "method": "foo", "module": "analysis",
                "script": "methods/foo/foo.py",
                "params": {},
            },
        ],
        "links": [
            {"source": "ref_1", "target": "method_1", "target_slot": "data"},
        ],
        "samples": ["SJ011"],
    }

    # What the resolver returns for a reference node with no run behind it.
    unresolved = {"label": "measurements.csv"}

    with pytest.raises(ValueError, match="names no run"):
        load_pipeline(pipeline_json, contract_map={},
                      reference_outputs={"ref_1": unresolved})


def _second_reference() -> dict:
    """The resolver's answer for run 63: sample ``SJ012``, one output."""
    return {
        "run_id": "63",
        "output_paths": {"labels": "/work/run63/labels.csv"},
        "sample": "SJ012",
        "method": "regionprops_quantification",
        "malformed": False,
    }


def test_two_references_on_differently_sampled_runs_fan_the_consumer_out():
    """Each reference adds its run's sample; the consumer runs once per sample."""
    from wfc.graph import expand_step_combos

    pipeline_json = {
        "nodes": [
            {"id": "ref_1", "type": "run_reference", "params": {}, "run_id": "62"},
            {"id": "ref_2", "type": "run_reference", "params": {}, "run_id": "63"},
            {"id": "method_1", "type": "method", "env": ENV,
             "method": "binary_feature_labeling", "module": "analysis",
             "params": {}},
        ],
        "links": [
            {"source": "ref_1", "target": "method_1",
             "source_slot": "measurements", "target_slot": "measurements"},
            {"source": "ref_2", "target": "method_1",
             "source_slot": "labels", "target_slot": "labels"},
        ],
        "samples": [],
    }

    pipeline = load_pipeline(pipeline_json, contract_map={}, reference_outputs={
        "ref_1": _ref_outputs(), "ref_2": _second_reference()})

    assert pipeline.samples == ["SJ011", "SJ012"]
    rows = expand_step_combos(pipeline.steps, pipeline.samples,
                              {"method_1": {"default": {}}}, None)
    assert sorted(combo["sample"] for _, combo in rows) == ["SJ011", "SJ012"]


def test_stray_output_keys_on_a_reference_node_are_read_by_nothing():
    """``output_slot`` / ``output_path`` left on a reference change nothing."""
    def document(**stray):
        return {
            "nodes": [
                {"id": "ref_1", "type": "run_reference", "params": {},
                 "run_id": "62", **stray},
                {"id": "method_1", "type": "method", "env": ENV,
                 "method": "binary_feature_labeling", "module": "analysis",
                 "params": {}},
            ],
            "links": [{"source": "ref_1", "target": "method_1",
                       "source_slot": "measurements",
                       "target_slot": "measurements"}],
            "samples": [],
        }

    clean = load_pipeline(document(), contract_map={},
                          reference_outputs={"ref_1": _ref_outputs()})
    stray = load_pipeline(
        document(output_slot="labels", output_path="/elsewhere/labels.csv"),
        contract_map={}, reference_outputs={"ref_1": _ref_outputs()})

    assert stray == clean
    assert stray.steps[0].run_ref_inputs == {
        "measurements": ["/work/run62/measurements.csv"]}
