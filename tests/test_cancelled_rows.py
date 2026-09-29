"""
Tests for cancelled-run row generation (pipeline-end walk).

Covers the end-to-end contract:

  1. Schema migration: ``cancelled_due_to_run_id`` column exists and is
     nullable on new DBs; migration is idempotent and works on a legacy DB
     missing the column.
  2. `_write_cancelled_rows` walk:
       - Linear chain with one failed middle step.
       - Branching DAG (one failed root; multiple descendants).
       - Cartesian sample × variant fan-out with one variant's upstream
         failing.
       - Fan-in collapsed chain writes cancelled rows with sample="__all__".
       - Idempotency across re-invocations.
       - Skips triples that already have any Run row.
  3. WfcProvider passthrough: cancelled rows surface via `WfcRun.to_dict`
     with `cancelledDueToRunId` set; legacy DB without the column still
     loads.

All tests use the `tmp_project` fixture from tests/conftest.py. The runs the
walk reconciles are produced by the scenario harness's stub rung -- every
row, and the frozen document the walk reads, is production's own -- with no
Snakemake subprocess.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from tests.fixtures.routes import completed_run
from tests.harness import Scenario, exits, node, run_scenario, selector, wire
from tests.harness.scenario import DEFAULT_VARIANT, SELECTOR_ID
from wfc.persistence import get_engine, get_session, reset_engine, Run


# =============================================================================
# Helpers
# =============================================================================


def _walk_scenario(pid: str, nodes, samples=("S1",), root=None, **kwargs) -> Scenario:
    """Declare a pipeline for the walk: a selector root plus ``nodes``.

    Args:
        pid: Pipeline execution id, also the document name.
        nodes: The method nodes, wired among themselves and to the selector.
        samples: The samples the project registers.
        root: The selector node; the plain one when ``None``.
        **kwargs: Any other :class:`Scenario` field.

    Returns:
        The scenario declaration.
    """
    return Scenario(nodes=[root or selector(), *nodes], samples=list(samples),
                    pipeline_id=pid, name=pid, **kwargs)


def _run_id_of(obs, node_id: str, sample: str = "S1",
               variant: str = DEFAULT_VARIANT) -> int:
    """The run id the claim phase registered for one driven target."""
    return int(obs.runs[(node_id, sample, variant)].run_id)


# =============================================================================
# schema migration
# =============================================================================


def test_run_has_cancelled_due_to_run_id_column(tmp_project):
    """On fresh DB, `runs` table has the nullable self-FK column."""
    engine = get_engine()
    with engine.connect() as conn:
        from sqlalchemy import text
        cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(runs)")).fetchall()
        }
    assert "cancelled_due_to_run_id" in cols


def test_run_started_at_is_nullable(tmp_project, monkeypatch):
    """A cancelled row can be inserted with started_at=None."""
    # The row stays typed: the claim is that the column accepts NULL, so the
    # NULL is the shape under test rather than a state a writer is asked to
    # produce here. The method it hangs off is one production registered.
    method_id = completed_run(tmp_project, monkeypatch=monkeypatch,
                              method="method_a", module="m",
                              sample="S1").run_row["method_id"]
    with get_session() as s:
        run = Run(
            method_id=method_id,
            sample="S1",
            pipeline_id="pid",
            status="cancelled",
            started_at=None,
        )
        s.add(run)
        s.commit()
        s.refresh(run)
        assert run.started_at is None


def test_legacy_db_migration_adds_column(tmp_path, monkeypatch):
    """A pre-migration DB (missing cancelled_due_to_run_id) gains the column
    on first engine init."""
    db_path = tmp_path / "legacy.db"
    # LEGACY-SHAPE FIXTURE (frozen on purpose): hand-rolled DDL recreates an
    # *old* `runs` schema (missing cancelled_due_to_run_id) to exercise the
    # back-compat migration on engine init. This is the one case the
    # test-policy permits raw CREATE TABLE; do not switch it to create_all.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            method_id INTEGER NOT NULL,
            sample TEXT,
            status TEXT,
            pipeline_id TEXT,
            started_at TEXT,
            finished_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    # Also set a project marker so project_root resolution works
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir(exist_ok=True)
    (wfc_dir / "wf-canvas.toml").write_text('[project]\nname="legacy"\n')
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))
    reset_engine()
    try:
        get_engine()  # triggers migration
        conn = sqlite3.connect(str(db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        conn.close()
        assert "cancelled_due_to_run_id" in cols
    finally:
        reset_engine()


# =============================================================================
# _write_cancelled_rows walk
# =============================================================================


@pytest.mark.parametrize(
    ("ids", "c_node_id"),
    [
        pytest.param(("method_a", "method_b", "method_c"), "method_c",
                     id="string-id-document"),
        # A legacy document numbers its nodes; its steps are scheduled under
        # their method names, yet the walk's row carries the raw document id.
        pytest.param(("1", "2", "3"), "3", id="legacy-numeric-id-document"),
    ],
)
@workflow(
    purpose="Linear chain A->B->C where B failed: one cancelled row for C linked "
            "to B, carrying C's own document id",
)
def test_walk_linear_chain_one_failure(tmp_project, monkeypatch, ids, c_node_id):
    from wfc.execution.lifecycle import _write_cancelled_rows

    pid = "pipe-lin-1"
    a_id, b_id, c_id = ids

    # A completes, B fails, C is never scheduled: the harness runs the
    # targets and freezes the document, and leaves the walk to the test.
    obs = run_scenario(
        _walk_scenario(pid, [
            node(a_id, method="method_a", inputs=[wire(SELECTOR_ID)]),
            node(b_id, method="method_b", inputs=[wire(a_id)], behavior=exits(1)),
            node(c_id, method="method_c", inputs=[wire(b_id)]),
        ]),
        root=tmp_project, monkeypatch=monkeypatch, pipeline_end=False)
    b_run_id = _run_id_of(obs, "method_b")

    口 = Step(step_num=1, name="Invoke walk", purpose="Fill in missing cancelled row for C")
    _write_cancelled_rows(pid, str(tmp_project))

    口 = Step(step_num=2, name="Assert one cancelled row", purpose="Row for method_c linked to B")
    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert len(cancelled) == 1
    c_row = cancelled[0]
    assert c_row.sample == "S1"
    assert c_row.cancelled_due_to_run_id == b_run_id
    assert c_row.started_at is None
    assert c_row.node_id == c_node_id


@workflow(
    purpose="Branching A->{B,C,D} where A failed: three cancelled rows pointing to A",
)
def test_walk_branching_root_failure(tmp_project, monkeypatch):
    from wfc.execution.lifecycle import _write_cancelled_rows

    pid = "pipe-branch-1"
    obs = run_scenario(
        _walk_scenario(pid, [
            node("method_a", inputs=[wire(SELECTOR_ID)], behavior=exits(1)),
            node("method_b", inputs=[wire("method_a")]),
            node("method_c", inputs=[wire("method_a")]),
            node("method_d", inputs=[wire("method_a")]),
        ]),
        root=tmp_project, monkeypatch=monkeypatch, pipeline_end=False)
    a_run_id = _run_id_of(obs, "method_a")

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert len(cancelled) == 3
    for c in cancelled:
        assert c.cancelled_due_to_run_id == a_run_id
        assert c.sample == "S1"
    assert sorted(c.node_id for c in cancelled) == ["method_b", "method_c", "method_d"]


@workflow(
    purpose="Cartesian samples × descendants after upstream failure: cancelled per (sample, descendant)",
)
def test_walk_cartesian_two_samples_one_failure(tmp_project, monkeypatch):
    from wfc.execution.lifecycle import _write_cancelled_rows

    pid = "pipe-cart-1"
    # Both samples' method_a fail; method_b never runs for either
    obs = run_scenario(
        _walk_scenario(pid, [
            node("method_a", inputs=[wire(SELECTOR_ID)], behavior=exits(1)),
            node("method_b", inputs=[wire("method_a")]),
        ], samples=("S1", "S2")),
        root=tmp_project, monkeypatch=monkeypatch, pipeline_end=False)
    a_s1 = _run_id_of(obs, "method_a", "S1")
    a_s2 = _run_id_of(obs, "method_a", "S2")

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    samples = sorted(c.sample for c in cancelled)
    assert samples == ["S1", "S2"]
    for c in cancelled:
        if c.sample == "S1":
            assert c.cancelled_due_to_run_id == a_s1
        else:
            assert c.cancelled_due_to_run_id == a_s2


@workflow(
    purpose="Variant sweep: one variant's upstream fails -- only that variant's descendants cancel",
)
def test_walk_variant_sweep_isolated_failure(tmp_project, monkeypatch):
    from wfc.execution.lifecycle import _write_cancelled_rows

    pid = "pipe-var-1"
    variants = {"strict": {"t": 0.1}, "loose": {"t": 0.9}}

    # method_a fails under loose only: strict completes, method_b completes
    # for strict and is never scheduled for loose. The harness freezes the
    # document as launch freezes it; the walk under test is this call's.
    obs = run_scenario(_walk_scenario(pid, [
        node("method_a", inputs=[wire(SELECTOR_ID)],
             behavior_by_variant={"loose": exits(1)}),
        node("method_b", inputs=[wire("method_a")]),
    ], variants=variants), root=tmp_project, monkeypatch=monkeypatch,
        pipeline_end=False)
    a_loose = _run_id_of(obs, "method_a", variant="loose")

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    # Only the loose variant of method_b should be cancelled
    assert len(cancelled) == 1
    assert cancelled[0].cancelled_due_to_run_id == a_loose
    # sanity: params correspond to loose
    assert cancelled[0].params == {"t": 0.9}


@workflow(
    purpose="Fan-in collapsed chain: cancelled row carries sample='__all__'",
)
def test_walk_fan_in_collapsed_failure(tmp_project, monkeypatch):
    from wfc.execution.lifecycle import _write_cancelled_rows

    pid = "pipe-fanin-1"
    # The collapsed step fails; downstream is never scheduled
    obs = run_scenario(
        _walk_scenario(pid, [
            node("csv_merge", inputs=[wire(SELECTOR_ID, bundle=True)],
                 behavior=exits(1)),
            node("downstream", inputs=[wire("csv_merge")]),
        ], samples=("S1", "S2"),
           root=selector(fan_mode="in", samples=["S1", "S2"])),
        root=tmp_project, monkeypatch=monkeypatch, pipeline_end=False)
    merge_run = _run_id_of(obs, "csv_merge", "__all__")

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert len(cancelled) == 1
    assert cancelled[0].sample == "__all__"
    assert cancelled[0].cancelled_due_to_run_id == merge_run


def test_walk_is_idempotent(tmp_project, monkeypatch):
    """Calling the walk twice does not duplicate cancelled rows."""
    from wfc.execution.lifecycle import _write_cancelled_rows

    pid = "pipe-idem-1"
    run_scenario(
        _walk_scenario(pid, [
            node("method_a", inputs=[wire(SELECTOR_ID)], behavior=exits(1)),
            node("method_b", inputs=[wire("method_a")]),
        ]),
        root=tmp_project, monkeypatch=monkeypatch, pipeline_end=False)

    _write_cancelled_rows(pid, str(tmp_project))
    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert len(cancelled) == 1


@workflow(
    purpose="Two unlabelled nodes of one method under identical params, one "
            "failed: the walk matches presence and the failed ancestor by "
            "node id, so only the failed branch's child is cancelled, against "
            "that branch's run",
)
def test_walk_keys_on_node_id_when_two_branches_share_a_method(tmp_project, monkeypatch):
    from wfc.execution.lifecycle import _write_cancelled_rows

    pid = "pipe-nodeid-1"
    # Pipeline layout, no labels anywhere:
    #   al -> dl
    #   ar -> dr
    # al and ar run method_a with identical params; dl and dr run method_b.
    # al fails, ar completes, dr completes on ar, and dl is never
    # scheduled. Method, sample and params are the same across the two
    # branches, so only the node id tells them apart.
    obs = run_scenario(_walk_scenario(pid, [
        node("al", method="method_a", inputs=[wire(SELECTOR_ID)],
             behavior=exits(1)),
        node("ar", method="method_a", inputs=[wire(SELECTOR_ID)]),
        node("dl", method="method_b", inputs=[wire("al")]),
        node("dr", method="method_b", inputs=[wire("ar")]),
    ]), root=tmp_project, monkeypatch=monkeypatch, pipeline_end=False)
    a_left_id = _run_id_of(obs, "al")

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert [(c.node_id, c.cancelled_due_to_run_id) for c in cancelled] == [
        ("dl", a_left_id)
    ]


def test_walk_success_path_writes_nothing(tmp_project, monkeypatch):
    """On a fully-successful pipeline the walk finds zero missing triples."""
    from wfc.execution.lifecycle import _write_cancelled_rows

    pid = "pipe-success-1"
    run_scenario(
        _walk_scenario(pid, [
            node("method_a", inputs=[wire(SELECTOR_ID)]),
            node("method_b", inputs=[wire("method_a")]),
        ]),
        root=tmp_project, monkeypatch=monkeypatch, pipeline_end=False)

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert cancelled == []


# =============================================================================
# WfcProvider passthrough
# =============================================================================


def test_wfc_provider_surfaces_cancelled_due_to_run_id(tmp_project, monkeypatch):
    """Cancelled Run rows appear via WfcProvider with cancelledDueToRunId populated."""
    from wfc.canvas.wfc_provider import WfcProvider

    pid = "pipe-prov-1"
    # method_a fails, so the pipeline-end walk writes method_b's row
    # cancelled under it -- the row the provider surfaces, as the walk wrote it
    obs = run_scenario(
        _walk_scenario(pid, [
            node("method_a", inputs=[wire(SELECTOR_ID)], behavior=exits(1)),
            node("method_b", inputs=[wire("method_a")]),
        ]),
        root=tmp_project, monkeypatch=monkeypatch)
    a_failed = _run_id_of(obs, "method_a")
    (cancelled_row,) = obs.rows_for_node("method_b", status="cancelled")
    cancelled_id = str(cancelled_row["id"])

    prov = WfcProvider(str(tmp_project))
    prov.load()
    run_dict = prov.get_run(cancelled_id)
    assert run_dict is not None
    assert run_dict["status"] == "cancelled"
    # Serialised as string to match parentRunId convention (all canvas IDs
    # are strings on the frontend).
    assert run_dict["cancelledDueToRunId"] == str(a_failed)
    assert isinstance(run_dict["cancelledDueToRunId"], str)


def test_wfc_provider_legacy_db_without_column_still_loads(tmp_path, monkeypatch):
    """A DB with an older schema (runs missing cancelled_due_to_run_id and
    other nullable columns; no run_annotations table) loads via WfcProvider
    without exception.

    The provider reads through the process engine, bound
    to this project's database, and building that engine runs ``ensure_schema``
    + ``create_all``, so the old on-disk schema is
    upgraded (missing nullable columns added, missing tables built) BEFORE any
    ORM ``select`` runs. The load then reads the upgraded schema normally.
    """
    from wfc.canvas.wfc_provider import WfcProvider

    # LEGACY-SHAPE FIXTURE (frozen on purpose): hand-rolled DDL recreates an
    # *old* schema (columns/tables intentionally missing) to exercise the
    # back-compat ``ensure_schema`` backfill. This is the one case the
    # test-policy permits raw CREATE TABLE; do not switch it to create_all.
    # Build a minimal legacy DB by hand
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir()
    (wfc_dir / "wf-canvas.toml").write_text('[project]\nname="legacy"\n')
    db_path = wfc_dir / "wfc.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE modules (id INTEGER PRIMARY KEY, name TEXT, description TEXT);
        CREATE TABLE methods (
            id INTEGER PRIMARY KEY, module_id INTEGER, name TEXT,
            script_path TEXT, env TEXT DEFAULT 'container:demo'
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            method_id INTEGER NOT NULL,
            params TEXT,
            sample TEXT,
            status TEXT,
            pipeline_id TEXT,
            nf_process_name TEXT,
            started_at TEXT,
            finished_at TEXT,
            metrics TEXT,
            nid TEXT
        );
        CREATE TABLE run_inputs (
            id INTEGER PRIMARY KEY, run_id INTEGER, source_run_id INTEGER,
            input_name TEXT, artifact_path TEXT
        );
        CREATE TABLE run_outputs (
            id INTEGER PRIMARY KEY, run_id INTEGER, output_name TEXT,
            artifact_path TEXT, artifact_type TEXT
        );
        INSERT INTO modules (id, name) VALUES (1, 'mod');
        INSERT INTO methods (id, module_id, name, env) VALUES (1, 1, 'method_a', 'container:demo');
        INSERT INTO runs (id, method_id, sample, status) VALUES (1, 1, 'S1', 'completed');
        """
    )
    conn.commit()
    conn.close()

    # The provider reads through the process engine: bind this project's
    # database with the override and a reset, as the harness does.
    from wfc import layout
    monkeypatch.setenv("DATABASE_URL", layout.database_url(tmp_path))
    reset_engine()

    prov = WfcProvider(str(tmp_path))
    prov.load()  # must not raise
    run = prov.get_run("1")
    assert run is not None
    assert run["status"] == "success"  # completed → success remap
    assert run.get("cancelledDueToRunId") is None
