"""Slot-correct input resolution for multi-output parent nodes.

When a node records more than one output, each downstream consumer must
receive exactly the output its link's ``source_slot`` names — never the
parent's first-recorded output. These tests cover the live ``run_step``
sidecar path (fresh runs and cache-hit audit rows) and the resolver's
loud-failure guards when slot wiring is absent or contradictory.

The single-output control path is covered by ``tests/test_run_step.py``.

Scenarios are declared against the harness and run on its stub rung — the
method process is faked at the dispatch boundary, so no Docker and no real
execution, while every phase of run_step runs for real.
"""

from __future__ import annotations

from axiom_annotations import workflow

from tests.harness import (
    Scenario,
    completed,
    node,
    run_scenario,
    selector,
    wire,
)

SAMPLE = "sample_A"
CLASSIFIER = ("classifier", SAMPLE, "default")
PLOTTER = ("plotter", SAMPLE, "default")

# The parent's published outputs. Each consumer slot is wired to exactly one
# of them, and the assertions compare the WHOLE slot_paths map, so a
# first-row-wins resolver is caught whichever of the two collect happens to
# record first.
PARENT_OUTPUTS = ("predictions.csv", "model.pkl")

#: The parent's declared output slots. ``metrics`` is declared and wired to
#: nothing — the unknown-source-slot error lists the declared slots, and a
#: third one that no link names is what makes that listing meaningful.
PARENT_SLOTS = {"predictions": ".csv", "model": ".pkl", "metrics": ".json"}
PARENT_FILES = {"predictions": "predictions.csv", "model": "model.pkl"}


# =============================================================================
# Scenario declarations
# =============================================================================

def _multi_output_scenario(consumer_inputs=None, **kwargs) -> Scenario:
    """A multi-output parent feeding one two-slot consumer.

    Args:
        consumer_inputs: The consumer's wiring. Defaults to the correct
            wiring — ``data`` from ``predictions``, ``model`` from
            ``model``.
        **kwargs: Any :class:`~tests.harness.Scenario` field to override.

    Returns:
        The scenario.
    """
    if consumer_inputs is None:
        consumer_inputs = [
            wire("classifier", source_slot="predictions", target_slot="data"),
            wire("classifier", source_slot="model", target_slot="model"),
        ]
    return Scenario(
        nodes=[
            selector(),
            node("classifier", inputs=[wire("sel")],
                 outputs=dict(PARENT_SLOTS), output_files=dict(PARENT_FILES)),
            node("plotter", inputs=consumer_inputs),
        ],
        samples=[SAMPLE],
        **kwargs,
    )


def _parent_only_scenario(outputs, output_files) -> Scenario:
    """One method node publishing the given output slots and nothing else.

    Args:
        outputs: Declared output slot -> type string.
        output_files: Declared output slot -> published filename.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[
            selector(),
            node("classifier", inputs=[wire("sel")],
                 outputs=dict(outputs), output_files=dict(output_files)),
        ],
        samples=[SAMPLE],
    )


def _published(obs, run_id) -> dict[str, str]:
    """Map published filename -> artifact path for one run's outputs."""
    return {r["output_name"]: r["artifact_path"]
            for r in obs.output_rows_for_run(run_id)}


# =============================================================================
# Live sidecar path: each consumer slot receives the wired output
# =============================================================================

@workflow(purpose="A consumer wired to a specific output of a multi-output "
                  "parent receives exactly that output through run_step's "
                  "live sidecar resolution path")
def test_each_slot_receives_its_wired_output(git_project, monkeypatch):
    """The plotter's 'data' slot is wired to the classifier's 'predictions'
    output and its 'model' slot to 'model'. Each slot must resolve its own
    output — a resolver that hands every consumer the first-recorded output
    would put the same file in both."""
    obs = run_scenario(_multi_output_scenario(), root=git_project,
                       monkeypatch=monkeypatch)

    assert obs.exit_code(PLOTTER) == 0
    paths = _published(obs, obs.runs[CLASSIFIER].run_id)
    slot_paths = obs.phase_args("dispatch", PLOTTER)["slot_paths"]
    assert slot_paths == {
        "data": [paths["predictions.csv"]],
        "model": [paths["model.pkl"]],
    }


@workflow(purpose="A consumer whose parent is a cache-hit audit row resolves "
                  "the same slot-correct outputs as a fresh run, through the "
                  "one-hop cache_source_run_id pointer")
def test_cache_hit_audit_row_resolves_slot_correct(git_project, monkeypatch,
                                                   capsys):
    """Cache-hit audit rows own no RunOutput rows; their outputs live on the
    source run. The output-name selection must apply on the source run's
    rows after the hop, not fall back to first-recorded."""
    scn = _multi_output_scenario(
        prior_runs=[completed("classifier", sample=SAMPLE)]
    )
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

    audit_row = obs.run_row(CLASSIFIER)
    assert audit_row["cache_source_run_id"] is not None, (
        "the parent must be a cache-hit audit row for this test to mean anything"
    )
    paths = _published(obs, audit_row["cache_source_run_id"])
    assert obs.output_rows_for(CLASSIFIER) == [], (
        "a cache-hit audit row owns no output rows of its own"
    )

    assert obs.exit_code(PLOTTER) == 0
    slot_paths = obs.phase_args("dispatch", PLOTTER)["slot_paths"]
    assert slot_paths == {
        "data": [paths["predictions.csv"]],
        "model": [paths["model.pkl"]],
    }
    assert "resolve_input: AUDIT" in capsys.readouterr().err


# =============================================================================
# No slot info: loud error on multi-output parents, unchanged on single
# =============================================================================

@workflow(purpose="Resolving a multi-output parent without slot information "
                  "fails loudly naming the available outputs, while a "
                  "single-output parent still resolves directly")
def test_no_slot_info_errors_on_multi_output_parent(git_project, tmp_path,
                                                    monkeypatch, capsys):
    """A caller with no slot wiring (manual --parent-run-id, legacy links
    without source_slot) must never silently receive an arbitrary output."""
    from wfc.storage import resolve_input

    multi = run_scenario(_parent_only_scenario(PARENT_SLOTS, PARENT_FILES),
                         root=git_project, monkeypatch=monkeypatch)
    multi_id = multi.runs[CLASSIFIER].run_id
    capsys.readouterr()
    assert resolve_input(multi_id) is None
    err = capsys.readouterr().err
    assert str(multi_id) in err
    for name in PARENT_OUTPUTS:
        assert name in err

    single = run_scenario(
        _parent_only_scenario({"only": ".csv"}, {"only": "only.csv"}),
        root=tmp_path / "single_output_project", monkeypatch=monkeypatch,
    )
    paths = _published(single, single.runs[CLASSIFIER].run_id)
    assert resolve_input(single.runs[CLASSIFIER].run_id) == paths["only.csv"]


def test_filter_matching_no_output_errors_with_available_names(git_project,
                                                               monkeypatch,
                                                               capsys):
    """A requested output absent from the parent's recorded outputs is a loud
    failure listing each output as its slot with the file it saved — never a
    silent None.

    The parent saves its slots under file names that share nothing with the
    slot names, so the listing has to pair the two rather than print either
    one twice.
    """
    from wfc.storage import resolve_input

    obs = run_scenario(
        _parent_only_scenario({"predictions": ".csv", "model": ".pkl"},
                              {"predictions": "labels.csv",
                               "model": "weights.pkl"}),
        root=git_project, monkeypatch=monkeypatch,
    )
    run_id = obs.runs[CLASSIFIER].run_id
    capsys.readouterr()
    assert resolve_input(run_id, slot="missing") is None
    err = capsys.readouterr().err
    assert "'missing'" in err
    assert "predictions (labels.csv)" in err
    assert "model (weights.pkl)" in err


# =============================================================================
# Wiring errors detected in run_step
# =============================================================================

@workflow(purpose="Wiring two different outputs of one parent into the same "
                  "input slot is rejected naming the node, the slot, and "
                  "both competing source slots — before any run is "
                  "registered or dispatched")
def test_two_outputs_into_one_slot_rejected(git_project, monkeypatch, capsys):
    scn = _multi_output_scenario(consumer_inputs=[
        wire("classifier", source_slot="predictions", target_slot="data"),
        wire("classifier", source_slot="model", target_slot="data"),
    ])
    capsys.readouterr()
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(PLOTTER) == 1
    err = capsys.readouterr().err
    assert "plotter" in err
    assert "'data'" in err
    assert "predictions" in err
    assert "model" in err
    [row] = obs.rows_for_node("plotter")
    assert row["status"] == "failed"
    assert "'data'" in row["error_message"]
    assert row["version_id"] is None and row["cache_key"] is None, (
        "no run may be registered for rejected wiring: the only record is "
        "the pipeline-end failed row carrying the refusal"
    )


def test_unknown_source_slot_names_declared_slots(git_project, monkeypatch,
                                                  capsys):
    """A link naming a source_slot the parent's slot_outputs does not declare
    fails the step with an error listing the declared output slots."""
    scn = _multi_output_scenario(consumer_inputs=[
        wire("classifier", source_slot="predictions", target_slot="data"),
        wire("classifier", source_slot="bogus", target_slot="model"),
    ])
    capsys.readouterr()
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(PLOTTER) == 1
    err = capsys.readouterr().err
    assert "bogus" in err
    for declared in ("predictions", "model", "metrics"):
        assert declared in err
