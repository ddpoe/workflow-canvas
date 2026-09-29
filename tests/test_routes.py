"""The run and client routes in the ``tests/fixtures/routes/`` package.

Tier 2. Each test proves the state a route builds against production's own
composition -- the claim's cache-key recipe, the collect phase's output
rows, the layout catalog's archive location, ``pre_run``'s own answer to a
repeated claim -- never against a literal the route could have written.
"""

from pathlib import Path

import pytest
from axiom_annotations import workflow
from sqlmodel import select

from tests.fixtures.routes import (
    canvas_client,
    claimed_run,
    completed_run,
    register_test_method,
)


def _names_in(payload) -> set[str]:
    """Collect every ``name`` value anywhere in a JSON payload."""
    found: set[str] = set()
    if isinstance(payload, dict):
        if isinstance(payload.get("name"), str):
            found.add(payload["name"])
        for value in payload.values():
            found |= _names_in(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= _names_in(item)
    return found


@workflow(purpose="completed_run produces a run that production's own claim, "
                  "materialize, dispatch, collect and record phases wrote -- "
                  "row, key, output rows and archive -- and is repeatable over "
                  "a root that already ran")
def test_completed_run_is_a_run_production_recorded(tmp_project, monkeypatch):
    from wfc import layout
    from wfc.execution.claim import candidate_cache_key
    from wfc.persistence import Run, RunOutput, get_session

    outputs = {"table": ".csv", "stats": ".csv"}
    first = completed_run(tmp_project, monkeypatch=monkeypatch,
                          method="quantify", outputs=outputs, params={"k": 1})
    module = first.project.scenario.node_by_id("quantify").module

    with get_session() as session:
        row = session.get(Run, first.run_id)
        assert row is not None
        assert row.status == "completed"
        assert row.finished_at is not None
        recorded = session.exec(
            select(RunOutput).where(RunOutput.run_id == first.run_id)
        ).all()
        slots = sorted(r.slot for r in recorded)
        paths = [Path(r.artifact_path) for r in recorded]

    # The collect phase recorded one row per declared slot, each artifact a
    # real file under the archive the layout catalog names for this run.
    assert slots == sorted(outputs)
    archive = layout.run_archive_dir(first.root, first.run_id)
    assert first.archive_dir == archive
    for path in paths:
        assert path.exists(), path
        assert archive in path.parents, (path, archive)
    assert first.output_path("table") in paths

    # The key on the row is the claim's own recipe for the same candidate.
    assert row.cache_key == candidate_cache_key(
        "quantify", module, first.target[1], {"k": 1}
    )

    # A second target (different params) completes beside the first.
    second = completed_run(tmp_project, monkeypatch=monkeypatch,
                           method="quantify", outputs=outputs, params={"k": 2})
    assert second.run_id != first.run_id
    assert second.cache_key != first.cache_key
    with get_session() as session:
        statuses = {
            r.id: r.status for r in session.exec(
                select(Run).where(Run.id.in_([first.run_id, second.run_id]))
            ).all()
        }
    assert statuses == {first.run_id: "completed", second.run_id: "completed"}


@workflow(purpose="claimed_run leaves the row the claim phase registers -- keyed "
                  "by the claim's recipe, no output rows, an empty archive -- and "
                  "a second identical claim relates to it the way pre_run's own "
                  "flag says")
def test_claimed_run_is_the_claim_phase_row(tmp_project, monkeypatch):
    from wfc.execution.claim import candidate_cache_key, pre_run
    from wfc.persistence import Run, RunOutput, get_session

    claimed = claimed_run(tmp_project, monkeypatch=monkeypatch, method="quantify")
    module = claimed.project.scenario.node_by_id("quantify").module
    sample = claimed.target[1]

    with get_session() as session:
        row = session.get(Run, claimed.run_id)
        assert row is not None
        assert row.status == "running"
        assert row.finished_at is None
        recorded = session.exec(
            select(RunOutput).where(RunOutput.run_id == claimed.run_id)
        ).all()
    assert recorded == []
    assert claimed.archive_dir.is_dir()
    assert list(claimed.archive_dir.iterdir()) == []
    assert row.cache_key == candidate_cache_key("quantify", module, sample, {})

    # A second identical claim: whatever pre_run does for the same candidate.
    second = claimed_run(tmp_project, monkeypatch=monkeypatch, method="quantify")
    flag, _ = pre_run(method_name="quantify", module_name=module,
                      sample=sample, params={})
    assert second.cache_key == claimed.cache_key
    with get_session() as session:
        second_row = session.get(Run, second.run_id)
    if flag == "NEW":
        assert second.run_id != claimed.run_id
        assert second_row.cache_source_run_id is None
    else:
        assert second_row.cache_source_run_id == claimed.run_id


@workflow(purpose="canvas_client serves the registry of the project it is "
                  "pinned to, and refuses a directory that is not an "
                  "initialised project rather than laying a marker")
def test_canvas_client_serves_the_pinned_project_and_refuses_a_bare_dir(
    tmp_project, monkeypatch, tmp_path_factory,
):
    register_test_method(
        tmp_project, module_name="test_pipeline",
        method_dir=tmp_project / "methods" / "transform",
        method_name="transform",
    )
    client = canvas_client(tmp_project, monkeypatch)

    response = client.get("/api/registry/methods")
    assert response.status_code == 200, response.text
    names = _names_in(response.json())
    # The one registered method is served; the copied-but-unregistered
    # fixture method directories are not, so this is the pinned database.
    assert "transform" in names
    assert "merge" not in names and "faulty" not in names

    bare = tmp_path_factory.mktemp("bare")
    with pytest.raises(AssertionError, match="not an initialised project"):
        canvas_client(bare, monkeypatch)
    assert not (bare / ".wfc").exists()
