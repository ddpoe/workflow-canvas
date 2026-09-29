"""Tier 2 tests: run_step's materialize and record seams.

Covers:
  - Selector-root fallback: with no parent runs and an input_selector
    upstream, the registered sample file fills the selector's target slot.
  - Collapsed fan-in: sample="__all__" materializes one file per bundled
    sample in the order the samples were supplied; a missing or empty
    sample directory fails the run before any dispatch, naming the
    offending sample(s).
  - Collect/record agreement: collect is the sole RunOutput writer; the
    rows it wrote are byte-identical before and after the record phase's
    completion write — record changes no output rows.

Scenarios are declared against the harness and run on its stub rung — the
method process is faked at the dispatch boundary, so no Docker and no real
execution, while every phase of run_step runs for real.
"""
from __future__ import annotations

from pathlib import Path

from axiom_annotations import workflow

from wfc import layout

from tests.harness import (
    Behavior,
    Scenario,
    completed,
    node,
    reference,
    run_target,
    selector,
    wire,
)

T1 = ("n1", "s1", "default")
BUNDLE = ("n1", "__all__", "default")


def _selector_scenario(**kwargs) -> Scenario:
    """A selector root feeding one method node on the ``raw`` input slot."""
    return Scenario(
        nodes=[
            selector(fan_mode=kwargs.pop("fan_mode", None)),
            node("n1", inputs=[wire("sel", target_slot="raw")]),
        ],
        **kwargs,
    )


# =============================================================================
# Materialize: selector-root fallback
# =============================================================================

@workflow(purpose="run_step with no parent runs and an input_selector upstream "
                  "fills the selector's target slot with the registered sample "
                  "file via WFC_INPUT_PATHS")
def test_selector_root_fallback_fills_target_slot(git_project, monkeypatch):
    obs = run_target(_selector_scenario(), "n1",
                     root=git_project, monkeypatch=monkeypatch)

    assert obs.runs[T1].dispatch_cmd is not None, (
        f"method dispatch did not happen (rc={obs.exit_code(T1)})"
    )
    input_paths = obs.input_paths(T1)
    # The slot key is the selector link's target_slot, not a default name.
    assert list(input_paths.keys()) == ["raw"]
    # The slot holds exactly the registered sample file.
    sample_file = obs.project.sample_file("s1")
    assert [Path(p).name for p in input_paths["raw"]] == ["s1.csv"]
    assert Path(input_paths["raw"][0]).resolve() == sample_file.resolve()


# =============================================================================
# Materialize: collapsed fan-in
# =============================================================================

@workflow(purpose="Collapsed fan-in materializes one file per bundled sample "
                  "preserving the supplied sample order; a missing or empty "
                  "sample directory fails before dispatch naming the offenders")
def test_collapsed_fan_in_order_and_missing_sample_error(
    git_project, tmp_path, monkeypatch, capsys
):
    ordered = _selector_scenario(fan_mode="in",
                                 samples=["beta", "alpha", "gamma"])
    obs = run_target(ordered, "n1", root=git_project, monkeypatch=monkeypatch)

    assert obs.runs[BUNDLE].dispatch_cmd is not None, (
        f"method dispatch did not happen (rc={obs.exit_code(BUNDLE)})"
    )
    input_paths = obs.input_paths(BUNDLE)
    # One path per bundled sample, in collapsed_samples order — NOT sorted
    # by name. The method reads the slot positionally, so re-sorting would
    # hand it its inputs in an order the document never asked for.
    # This is NOT a cache-key constraint: the bundle reaches the key through
    # build_input_fingerprint, which sorts, so membership moves the key and
    # order deliberately does not.
    assert [Path(p).name for p in input_paths["raw"]] == [
        "beta.csv", "alpha.csv", "gamma.csv",
    ]

    # A nonexistent sample dir and an existing-but-empty one both fail the
    # run before dispatch, and the error names each offender.
    offenders = _selector_scenario(
        fan_mode="in",
        samples=["beta", "ghost", "hollow"],
        missing_samples=("ghost",),
        empty_samples=("hollow",),
    )
    capsys.readouterr()
    failed = run_target(offenders, "n1", root=tmp_path / "offender_project",
                        monkeypatch=monkeypatch)

    assert failed.exit_code(BUNDLE) == 1
    assert "dispatch" not in failed.phases_ran(BUNDLE), (
        "must fail before any dispatch"
    )
    err = capsys.readouterr().err
    assert "ghost" in err
    assert "hollow" in err


# =============================================================================
# Record: collect/record writer agreement
# =============================================================================

@workflow(purpose="The RunOutput rows written during output collection are "
                  "byte-identical before and after the record phase's "
                  "completion write — collect is the sole output-row writer — "
                  "and the row left behind carries the slot the scenario "
                  "declared: its filename, its module-file type, its staged "
                  "path and its size")
def test_collect_and_record_output_writers_agree(git_project, monkeypatch):
    """Snapshot all RunOutput columns around the record-phase write.

    Output collection upserts one full row per declared slot; the record
    phase then flips the run row without touching output rows (it passes
    ``complete_run`` no output_files). Agreement here means the completion
    write is a no-op on every RunOutput column.

    The value-level check at the end is the assertion the standing pack
    cannot make: the pack compares the two snapshots with *each other*,
    never with what the scenario declared, so a collect phase that wrote
    the wrong artifact type or the wrong filename into BOTH snapshots would
    agree with itself and pass the pack.
    """
    content = "k,v\n1,2\n3,4\n"
    scn = Scenario(
        nodes=[
            selector(),
            node("src", inputs=[wire("sel")]),
            reference("ref", run_of="src"),
            node("n1", inputs=[wire("ref", source_slot=None)],
                 outputs={"out": ".csv"},
                 behavior=Behavior(outputs={"out": content})),
        ],
        prior_runs=[completed("src")],
    )

    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch)
    run = obs.runs[T1]

    assert obs.exit_code(T1) == 0, "run_step must succeed end-to-end"
    assert run.completion_writes == 1, (
        f"expected exactly one completion write, got {run.completion_writes}"
    )
    assert run.output_rows_before, (
        "collection wrote no RunOutput rows before the completion write"
    )
    assert run.output_rows_before == run.output_rows_after, (
        "the completion re-upsert changed RunOutput rows — the two writers "
        f"disagree.\nbefore: {run.output_rows_before}\n"
        f"after: {run.output_rows_after}"
    )

    # Value-level: the row the record phase left behind is the one declared
    # slot's file — named by its filename (not the slot), typed as a
    # slot-declared module output, staged under this run's archive dir,
    # sized from the bytes the method wrote.
    rows = run.output_rows_after
    assert len(rows) == 1, (
        f"expected exactly one RunOutput row for the one declared slot, "
        f"got {len(rows)}: {rows}"
    )
    row = rows[0]
    expected = {
        "run_id": run.run_id,
        "output_name": "out.csv",
        "artifact_type": "module_file",
        "file_size": len(content.encode("utf-8")),
    }
    got = {column: row[column] for column in expected}
    assert got == expected, (
        "the RunOutput row does not carry the declared slot content.\n"
        f"expected: {expected}\ngot: {got}"
    )
    staged = layout.run_archive_dir(obs.project.root, run.run_id) / "out.csv"
    assert Path(row["artifact_path"]).resolve() == staged.resolve(), (
        f"artifact_path {row['artifact_path']!r} is not the slot file staged "
        f"under the run archive ({staged})"
    )
