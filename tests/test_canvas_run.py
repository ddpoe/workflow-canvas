"""
Tests for canvas workflow run system.

Covers: pipeline enrichment, run endpoint wiring,
status endpoint, pipeline ID passthrough, and capture_output.
"""

from tests.conftest import pin_project_root
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from wfc.canvas.models import PipelineInput, PipelineLink, PipelineNode
from wfc.canvas.submission import _enrich_pipeline
from wfc.canvas.server import app
from wfc.canvas.state import _active_jobs
from wfc.persistence import project_root as get_project_root
from wfc.layout import run_archive_dir
from wfc.persistence import Module, Method, Run

from tests.fixtures.fakes import (
    fake_engine_process,
    restore_readiness_probe,
    seed_active_job,
    stub_pipeline_submission,
    stub_readiness_probes,
)
from tests.fixtures.routes import (
    build_project_snapshot,
    canvas_client,
    restore_project_snapshot,
)
from tests.harness import Scenario, build_project, node, wire

#: Every test here runs behind a healthy readiness pre-flight: the submission
#: gate in ``run_workflow`` probes docker and git before spawning the run
#: thread, and the happy-path tests are about what happens after the gate.
#: The gate test overrides the probes itself to drive the reject path.
pytestmark = pytest.mark.usefixtures("ready_preflight")

#: Module the canvas payload's two method nodes name.
CANVAS_MODULE = "data_preprocessing"
#: Samples the real-path submission drives the engine over.
SUBMISSION_SAMPLES = ["Pa16c"]


def _submission_scenario() -> Scenario:
    """The project the canvas payload in ``_make_pipeline_input`` names.

    Declares the same two methods the payload references, under the same
    module and with the same output-slot contracts, so the submission path
    enriches against a ``script_path`` and an output-slot contract that
    *registration* derived from the method directory on disk rather than
    ones the test wrote into the rows itself.

    Returns:
        The scenario whose project the real-path submission runs against.
    """
    return Scenario(
        nodes=[
            node("preprocess_1", method="preprocess", module=CANVAS_MODULE,
                 outputs={"data": ".csv"}),
            node("filter_1", method="filter_cells", module=CANVAS_MODULE,
                 inputs=[wire("preprocess_1")], outputs={"filtered": ".h5ad"}),
        ],
        samples=list(SUBMISSION_SAMPLES),
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def canvas_project(tmp_path_factory):
    """The canvas payload's project, built once per module by registration.

    ``build_project`` registers the module, both methods and the sample
    through the same production functions ``wfc register-*`` runs, so the
    rows the enrichment and status tests read carry the ``script_path`` and
    the output-slot contracts *registration* derived from the method
    directories on disk -- never rows a test wrote by hand. The database
    file is snapshotted right after the build; ``db_engine`` restores that
    snapshot before every test, so the ~3 s build is paid once and a test
    that writes rows still starts from the pristine copy.

    Yields:
        The ``ProjectSnapshot``: the built project, its ``DATABASE_URL``,
        the live database file and the pristine copy.
    """
    from wfc.persistence import reset_engine

    root = tmp_path_factory.mktemp("canvas_run_project")
    # The build's environment pin closes before the yield: building the
    # project is what needs it, not every test in the module. ``db_engine``
    # re-pins per test, so a test that never requests it starts from whatever
    # environment it sets itself.
    snapshot = build_project_snapshot(_submission_scenario(), root)
    yield snapshot
    reset_engine()


@pytest.fixture
def db_engine(canvas_project, monkeypatch):
    """A pristine copy of the harness-built database, pinned for one test.

    Per-test isolation is snapshot-restore of the SQLite file: the cached
    production engine is dropped (its pooled connection would otherwise
    hold the file open on Windows), the post-build snapshot is copied back
    over the live file, and cwd, ``WFC_PROJECT_ROOT`` and ``DATABASE_URL``
    are re-pinned to the built project through this test's monkeypatch.
    """
    from wfc.persistence import reset_engine

    restore_project_snapshot(canvas_project, monkeypatch)

    engine = create_engine(canvas_project.database_url)
    yield engine
    engine.dispose()
    reset_engine()




@pytest.fixture
def client(db_engine, canvas_project, monkeypatch):
    """FastAPI test client over the harness-built project ``db_engine`` pinned."""
    return canvas_client(canvas_project.project.root, monkeypatch)


def _make_pipeline_input(nodes=None, links=None, name="test"):
    """Helper to build a PipelineInput dict for POSTing."""
    if nodes is None:
        nodes = [
            {
                "id": "preprocess_1",
                "type": "method",
                "method": "preprocess",
                "module": "data_preprocessing",
                "params": {"normalize": True},
            },
            {
                "id": "filter_1",
                "type": "method",
                "method": "filter_cells",
                "module": "data_preprocessing",
                "params": {"min_quality": 0.5},
            },
        ]
    if links is None:
        links = [
            {
                "source": "preprocess_1",
                "target": "filter_1",
                "sourceHandle": "data",
                "targetHandle": "data",
            }
        ]
    return {"name": name, "nodes": nodes, "links": links, "samples": []}


# =============================================================================
# Pipeline enrichment (_enrich_pipeline)
# =============================================================================


class TestEnrichPipeline:
    """Test the _enrich_pipeline function that adds script paths and slot_outputs."""

    def test_adds_script_path(self, db_engine):
        """Enrichment looks up script_path from the DB for each method node."""
        pipeline = PipelineInput(
            nodes=[
                PipelineNode(
                    id="preprocess_1", type="method",
                    method="preprocess", module="data_preprocessing",
                    params={"normalize": True},
                ),
            ],
            links=[],
        )
        result = _enrich_pipeline(pipeline)

        assert len(result["nodes"]) == 1
        node = result["nodes"][0]
        # Registration derives script_path from the method directory with the
        # host's own separators; enrichment passes it through untouched.
        assert Path(node["script"]).as_posix() == "methods/preprocess/preprocess.py"
        assert node["method"] == "preprocess"
        assert node["module"] == "data_preprocessing"
        assert node["params"] == {"normalize": True}

    def test_adds_slot_outputs_from_contract(self, db_engine):
        """slot_outputs are populated from method contract output_slots."""
        pipeline = PipelineInput(
            nodes=[
                PipelineNode(
                    id="preprocess_1", type="method",
                    method="preprocess", module="data_preprocessing",
                ),
            ],
            links=[],
        )
        result = _enrich_pipeline(pipeline)
        node = result["nodes"][0]
        # preprocess has output_slots: {"data": {"type": ".csv"}}
        assert "data" in node["slot_outputs"]
        assert node["slot_outputs"]["data"] == "data.csv"

    def test_links_preserved(self, db_engine):
        """Links are passed through to the enriched output."""
        pipeline = PipelineInput(**_make_pipeline_input())
        result = _enrich_pipeline(pipeline)

        assert len(result["links"]) == 1
        link = result["links"][0]
        assert link["source"] == "preprocess_1"
        assert link["target"] == "filter_1"

    def test_single_node_no_links(self, db_engine):
        """Single-node pipeline with no links is valid."""
        pipeline = PipelineInput(
            nodes=[
                PipelineNode(
                    id="preprocess_1", type="method",
                    method="preprocess", module="data_preprocessing",
                ),
            ],
            links=[],
        )
        result = _enrich_pipeline(pipeline)
        assert len(result["nodes"]) == 1
        assert result["links"] == []


# =============================================================================
# Run endpoint
# =============================================================================


class TestRunEndpoint:
    """Test the POST /api/workflow/run endpoint."""

    def test_returns_job_id(self, client):
        """Run endpoint returns a job_id and triggers execution."""
        def fake_run_pipeline(**kwargs):
            return 0

        with stub_pipeline_submission(run_pipeline=fake_run_pipeline):
            resp = client.post(
                "/api/workflow/run",
                json=_make_pipeline_input(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "job_id" in data
            assert data["status"] == "submitted"
            assert len(data["job_id"]) > 0
            # Wait briefly for background thread to finish within mock scope
            time.sleep(0.3)

        _active_jobs.clear()

    def test_real_path_submit_passes_real_git_gate(self, git_project, monkeypatch):
        """A real Canvas payload runs the real submit path — real
        _enrich_pipeline + generate_snakefile + run_pipeline — with ONLY the
        Snakemake subprocess spawn stubbed, and the run-readiness gate's real
        git probe passing against a committed repo. Asserts the full
        submission-response contract (200, submitted, job_id, step_map).

        Distinct from ``test_returns_job_id`` (which stubs the whole
        run_pipeline seam and only checks job_id/status): here nothing between
        the HTTP boundary and ``subprocess.Popen`` is mocked, so enrichment,
        Snakefile generation and the readiness gate all execute for real.
        """
        # A real project whose methods are registered and whose tree is
        # committed, so check_git returns ok at the readiness gate (and
        # run_pipeline's git-commit resolution has a HEAD to read).
        project = build_project(_submission_scenario(), root=git_project,
                                monkeypatch=monkeypatch)
        # A canvas server process does not run with its cwd inside the canvas
        # project — that is why the endpoint resolves the project root before
        # probing readiness. build_project chdirs into the project it builds;
        # stepping back out keeps "probed against the resolved root, not the
        # process cwd" a claim this test can actually fail on.
        monkeypatch.chdir(git_project.parent)
        pin_project_root(monkeypatch, project.root)
        _active_jobs.clear()
        client = TestClient(app, raise_server_exceptions=False)
        # Revert only the git half of the autouse readiness stub back to the
        # real probe; check_docker stays stubbed (a live daemon is a true
        # external edge this default-suite test must not depend on).
        restore_readiness_probe(monkeypatch, "git")

        payload = _make_pipeline_input()
        # Method nodes with no input_selector need >=1 sample or load_pipeline
        # rejects the zero-sample DAG; give the real engine something to chew.
        payload["samples"] = list(SUBMISSION_SAMPLES)

        # Stub ONLY the Snakemake spawn (true external edge). ``check_git``
        # runs the real ``git`` via ``subprocess.run`` — which also goes
        # through ``subprocess.Popen`` — so every non-snakemake command goes
        # to the real Popen or the readiness gate can't probe the fixture repo.
        with fake_engine_process(only_snakemake=True):
            resp = client.post("/api/workflow/run", json=payload)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "submitted"
            assert data["job_id"]
            assert data["step_map"] == {
                "preprocess_1": "preprocess",
                "filter_1": "filter_cells",
            }
            # Drain the real run_pipeline under the Popen stub so generation +
            # the archive pass actually execute (and no daemon thread leaks
            # into the next test's DB).
            _active_jobs[data["job_id"]]["thread"].join(timeout=15)

        _active_jobs.clear()

    def test_empty_workflow_rejected(self, client):
        """Pipeline with no nodes is rejected before execution."""
        resp = client.post(
            "/api/workflow/run",
            json=_make_pipeline_input(nodes=[], links=[]),
        )
        assert resp.status_code == 400


class TestRunReadinessGate:
    """Submission is gated on Docker/git readiness."""

    def test_docker_down_rejects_with_kind_tag_no_thread(self, client, monkeypatch):
        """Docker down → 409 {kind,message,hint}; no thread/orphan row spawned."""
        stub_readiness_probes(monkeypatch, docker="fail")
        # If the gate failed to short-circuit, run_pipeline_fn would be called.
        called = {"ran": False}

        def _should_not_run(**kwargs):
            called["ran"] = True
            return 0

        _active_jobs.clear()
        with stub_pipeline_submission(run_pipeline=_should_not_run):
            resp = client.post("/api/workflow/run", json=_make_pipeline_input())

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["kind"] == "not_runnable_docker"
        assert detail["message"] == "docker msg"
        assert detail["hint"] == "docker hint"
        assert called["ran"] is False
        assert _active_jobs == {}

    def test_git_not_ready_rejects_with_kind_tag(self, client, monkeypatch):
        """git not ready (and Docker ok) → 409 not_runnable_git."""
        stub_readiness_probes(monkeypatch, git="fail")
        _active_jobs.clear()
        with stub_pipeline_submission(run_pipeline=lambda **k: 0):
            resp = client.post("/api/workflow/run", json=_make_pipeline_input())
        assert resp.status_code == 409
        assert resp.json()["detail"]["kind"] == "not_runnable_git"
        assert _active_jobs == {}


# =============================================================================
# Status endpoint
# =============================================================================


class TestStatusEndpoint:
    """Test GET /api/workflow/status/{job_id}."""

    def test_unknown_job_returns_not_found(self, client):
        """Unknown job_id returns 404."""
        resp = client.get("/api/workflow/status/nonexistent")
        assert resp.status_code == 404

    def test_pending_status(self, client, db_engine):
        """Job with no runs yet shows pending status."""
        seed_active_job(
            "test-job", alive=True,
            log_dir=None,
        )

        resp = client.get("/api/workflow/status/test-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "pending"

        _active_jobs.clear()

    def test_completed_status(self, client, db_engine):
        """Job with all completed runs shows completed status."""
        with Session(db_engine) as session:
            mod = session.exec(select(Module)).first()
            method = session.exec(select(Method)).first()
            run = Run(
                method_id=method.id,
                pipeline_id="done-job", node_id="preprocess_1",
                status="completed",
                sample="Pa16c",
            )
            session.add(run)
            session.commit()

        seed_active_job(
            "done-job", alive=False,
            log_dir=None,
            step_map={"preprocess_1": "preprocess"},
        )

        resp = client.get("/api/workflow/status/done-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "completed"

        _active_jobs.clear()

    def test_mixed_status_fanout_partial_failure(self, client, db_engine):
        """Fan-out over 4 samples with 1 failed + 3 completed → node 'mixed',
        pipeline 'completed_with_failures'. Exercises the per-sample tally
        path: the node's status counts every sample's run, never just
        whichever row comes last in the DB row order.
        """
        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            # 3 successes + 1 failure — all same (pipeline_id, method).
            for sample in ("s1", "s2", "s3"):
                session.add(Run(
                    method_id=method.id, pipeline_id="mix-job", node_id="preprocess_1",
                    status="completed", sample=sample,
                ))
            session.add(Run(
                method_id=method.id, pipeline_id="mix-job", node_id="preprocess_1",
                status="failed", sample="s4",
                error_message="intentional",
            ))
            session.commit()

        seed_active_job(
            "mix-job", alive=False,
            log_dir=None,
            step_map={"preprocess_1": "preprocess"},
        )

        resp = client.get("/api/workflow/status/mix-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "completed_with_failures", (
            f"expected completed_with_failures, got {data['overall_status']}"
        )
        node = data["node_states"]["preprocess_1"]
        assert node["status"] == "mixed"
        assert node["tally"]["completed"] == 3
        assert node["tally"]["failed"] == 1

        _active_jobs.clear()

    def test_failed_status(self, client, db_engine):
        """Job with a failed run shows failed status."""
        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            run = Run(
                method_id=method.id,
                pipeline_id="fail-job", node_id="preprocess_1",
                status="failed",
                sample="Pa16c",
                error_message="Something went wrong",
            )
            session.add(run)
            session.commit()

        seed_active_job(
            "fail-job", alive=False,
            log_dir=None,
            step_map={"preprocess_1": "preprocess"},
        )

        resp = client.get("/api/workflow/status/fail-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "failed"

        _active_jobs.clear()

    def test_failed_run_error_surfaces_on_node(self, client, db_engine):
        """A failed Run's error_message is attached to node_states[id].error
        so the Inspector can show it without the user opening the log stream.

        A failed node carries its explanation inline, so users need not
        click through to the Output tab and wait for the log stream to
        finish to see what broke.
        """
        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            run = Run(
                method_id=method.id,
                pipeline_id="node-err-job", node_id="preprocess_1",
                status="failed",
                sample="Pa16c",
                error_message="TypeError: 'NoneType' is not subscriptable at line 42",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            expected_run_id = str(run.id)

        seed_active_job(
            "node-err-job", alive=False,
            log_dir=None,
            step_map={"preprocess_1": "preprocess"},
        )

        resp = client.get("/api/workflow/status/node-err-job")
        assert resp.status_code == 200
        data = resp.json()
        node = data["node_states"]["preprocess_1"]
        assert node["status"] == "failed"
        assert "NoneType" in node["error"], (
            f"expected error_message to flow into node_states.error; got {node}"
        )
        assert node["error_run_id"] == expected_run_id
        assert node["error_sample"] == "Pa16c"

        _active_jobs.clear()

    def test_failed_run_error_message_truncated(self, client, db_engine):
        """Oversized tracebacks don't blow up the Inspector — cap at ~600 chars
        with an ellipsis so the UI stays compact.  Users go to Builder Output
        for the full log."""
        huge = "A" * 2000
        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            session.add(Run(
                method_id=method.id, pipeline_id="big-err-job", node_id="preprocess_1",
                status="failed", sample="s1", error_message=huge,
            ))
            session.commit()

        seed_active_job(
            "big-err-job", alive=False,
            log_dir=None,
            step_map={"preprocess_1": "preprocess"},
        )
        resp = client.get("/api/workflow/status/big-err-job")
        node = resp.json()["node_states"]["preprocess_1"]
        assert node["error"].endswith("…")
        assert len(node["error"]) < len(huge)
        _active_jobs.clear()

    def test_cancelled_status(self, client, db_engine):
        """All-cancelled runs derive overall_status="cancelled".

        Without the cancelled arm, _aggregate would return "unknown" for
        the node and the overall chain would fall through to its "running"
        fallback — leaving a cancelled pipeline reporting in-flight
        forever and wedging the canvas Run/Stop button.
        """
        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            for sample in ("s1", "s2"):
                session.add(Run(
                    method_id=method.id, pipeline_id="cancelled-job", node_id="preprocess_1",
                    status="cancelled", sample=sample,
                    error_message="Cancelled by user",
                ))
            session.commit()

        seed_active_job(
            "cancelled-job", alive=False,
            log_dir=None,
            step_map={"preprocess_1": "preprocess"},
        )

        resp = client.get("/api/workflow/status/cancelled-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "cancelled", (
            f"expected cancelled, got {data['overall_status']}"
        )
        assert data["node_states"]["preprocess_1"]["status"] == "cancelled"

        _active_jobs.clear()

    def test_cancelled_with_failed_resolves_to_failed(self, client, db_engine):
        """A real failure dominates a cancellation in overall_status.

        Pins the elif ordering: a node that genuinely errored before the
        cancel landed is more important to surface to the user than the
        fact that other nodes were cancelled in response.
        """
        with Session(db_engine) as session:
            method_a = session.exec(
                select(Method).where(Method.name == "preprocess")
            ).first()
            method_b = session.exec(
                select(Method).where(Method.name == "filter_cells")
            ).first()
            session.add(Run(
                method_id=method_a.id, pipeline_id="mix-cancel-fail-job",
                node_id="preprocess_1",
                status="failed", sample="s1", error_message="boom",
            ))
            session.add(Run(
                method_id=method_b.id, pipeline_id="mix-cancel-fail-job",
                node_id="filter_cells_1",
                status="cancelled", sample="s1",
                error_message="Cancelled by user",
            ))
            session.commit()

        seed_active_job(
            "mix-cancel-fail-job", alive=False,
            log_dir=None,
            step_map={
                "preprocess_1": "preprocess",
                "filter_cells_1": "filter_cells",
            },
        )

        resp = client.get("/api/workflow/status/mix-cancel-fail-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "failed"
        # Lock the joint signal the canvas poller depends on: the downstream
        # node reads 'cancelled' AND the run thread is dead in the SAME
        # response. The frontend waits for thread_alive=False before treating
        # a failed run as done, which is what lets downstream nodes flip from
        # 'pending' to 'cancelled' (see services.ts::pollNodeStatus).
        assert data["node_states"]["filter_cells_1"]["status"] == "cancelled"
        assert data["thread_alive"] is False

        _active_jobs.clear()

    def test_log_content_returned(self, client, db_engine, tmp_path, monkeypatch):
        """Status endpoint returns captured log file content."""
        # Resolve the run-dir through the production helper so the test's
        # on-disk log layout can't drift from where run-step writes per-run
        # logs. the run archive keys off persistence.project_root(), so point that at
        # tmp_path (the canvas root the endpoint already uses) via marker.
        (tmp_path / ".wfc").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".wfc" / "wf-canvas.toml").write_text("", encoding="utf-8")
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))
        from wfc.persistence import reset_engine
        reset_engine()

        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            run = Run(
                method_id=method.id,
                pipeline_id="log-job", node_id="preprocess_1",
                status="completed",
                sample="Pa16c",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

        log_dir = run_archive_dir(get_project_root(), run_id)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stdout.log").write_text(
            "Running rule preprocess...\nFinished.\n", encoding="utf-8",
        )
        (log_dir / "stderr.log").write_text(
            "Warning: low memory\n", encoding="utf-8",
        )

        seed_active_job(
            "log-job", alive=False,
            log_dir=str(log_dir),
            error=None,
            step_map={"preprocess_1": "preprocess"},
        )

        resp = client.get("/api/workflow/status/log-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "completed"
        assert "Running rule preprocess" in data["log"]
        assert "Warning: low memory" in data["log"]
        assert "STDERR" in data["log"]

        _active_jobs.clear()

    def test_error_propagated_on_startup_failure(self, client):
        """When thread dies with error and no runs exist, status shows failed."""
        seed_active_job(
            "err-job", alive=False,
            log_dir=None,
            error="Could not find Snakemake executable",
        )

        resp = client.get("/api/workflow/status/err-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "failed"
        assert data["error"] == "Could not find Snakemake executable"

        _active_jobs.clear()


# =============================================================================
# Pipeline ID passthrough
# =============================================================================


class TestPipelineIdPassthrough:
    """Verify run_pipeline uses a caller-provided pipeline_id."""

    def test_run_pipeline_uses_provided_pipeline_id(self, tmp_path, monkeypatch):
        """When pipeline_id is passed, run_pipeline uses it instead of generating one.

        Drives the real engine internals (load_pipeline + generate_snakefile)
        against a real on-disk pipeline.json — only the Snakemake subprocess
        spawn is stubbed. The caller-provided id must flow through to both the
        log-dir path and the generated Snakefile's embedded PIPELINE_ID.
        """
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
        from wfc.persistence import reset_engine
        reset_engine()

        caller_id = "caller-provided-id-1234"

        # A real (empty) pipeline the real load_pipeline + generate_snakefile
        # consume; run_pipeline also freezes it into the pipeline dir.
        (tmp_path / "pipeline.json").write_text('{"nodes": [], "links": [], "samples": []}')

        with fake_engine_process():
            from wfc.execution import run_pipeline
            run_pipeline(
                pipeline_path=str(tmp_path / "pipeline.json"),
                project_root=str(tmp_path),
                wfc_root=str(tmp_path),
                pipeline_id=caller_id,
            )

            log_dir = tmp_path / ".runs" / "pipelines" / caller_id
            assert log_dir.exists()
            # The real generate_snakefile embeds the id it was handed, proving
            # the caller-provided pipeline_id reached generation (not a UUID).
            snakefile = (log_dir / "Snakefile").read_text(encoding="utf-8")
            assert f'PIPELINE_ID = "{caller_id}"' in snakefile

    def test_run_pipeline_generates_id_when_not_provided(self, tmp_path, monkeypatch):
        """When pipeline_id is omitted, run_pipeline generates its own UUID.

        Real engine internals over a real pipeline.json; Popen-only stub.
        """
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
        from wfc.persistence import reset_engine
        reset_engine()
        (tmp_path / "pipeline.json").write_text('{"nodes": [], "links": [], "samples": []}')

        with fake_engine_process():
            from wfc.execution import run_pipeline
            run_pipeline(
                pipeline_path=str(tmp_path / "pipeline.json"),
                project_root=str(tmp_path),
                wfc_root=str(tmp_path),
            )

            pipelines_dir = tmp_path / ".runs" / "pipelines"
            assert pipelines_dir.exists()
            subdirs = list(pipelines_dir.iterdir())
            assert len(subdirs) == 1
            assert len(subdirs[0].name) == 36

    def test_server_passes_pipeline_id_to_run_pipeline(self, client, tmp_path):
        """The server's pipeline_id is forwarded to run_pipeline."""
        captured_kwargs = {}

        def fake_run_pipeline(**kwargs):
            captured_kwargs.update(kwargs)
            return 0

        with stub_pipeline_submission(run_pipeline=fake_run_pipeline):
            resp = client.post(
                "/api/workflow/run",
                json=_make_pipeline_input(),
            )
            time.sleep(0.5)

        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        assert captured_kwargs.get("pipeline_id") == job_id
        assert captured_kwargs.get("capture_output") is True

        _active_jobs.clear()


# =============================================================================
# Capture output param
# =============================================================================


class TestCaptureOutputParam:
    """Verify capture_output controls whether stdout/stderr are redirected."""

    def test_capture_output_false_no_redirect(self, tmp_path, monkeypatch):
        """When capture_output=False (default), subprocess.Popen gets no file redirects.

        run_pipeline uses subprocess.Popen + .wait() so the canvas cancel
        endpoint can SIGTERM the live process. The stdout/stderr kwargs the
        test inspects come through the real Popen call — only that spawn is
        stubbed; load_pipeline + generate_snakefile run for real.
        """
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
        from wfc.persistence import reset_engine
        reset_engine()
        (tmp_path / "pipeline.json").write_text('{"nodes": [], "links": [], "samples": []}')

        with fake_engine_process() as mock_sp:
            from wfc.execution import run_pipeline
            run_pipeline(
                pipeline_path=str(tmp_path / "pipeline.json"),
                project_root=str(tmp_path),
                wfc_root=str(tmp_path),
            )

            sp_call = mock_sp.call_args
            assert sp_call[1].get("stdout") is None
            assert sp_call[1].get("stderr") is None

    def test_capture_output_true_redirects_to_files(self, tmp_path, monkeypatch):
        """When capture_output=True, stdout/stderr are redirected to log files.

        Real engine internals over a real pipeline.json; Popen-only stub.
        """
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
        from wfc.persistence import reset_engine
        reset_engine()
        (tmp_path / "pipeline.json").write_text('{"nodes": [], "links": [], "samples": []}')

        with fake_engine_process() as mock_sp:
            from wfc.execution import run_pipeline
            run_pipeline(
                pipeline_path=str(tmp_path / "pipeline.json"),
                project_root=str(tmp_path),
                wfc_root=str(tmp_path),
                capture_output=True,
            )

            sp_call = mock_sp.call_args
            assert sp_call[1].get("stdout") is not None
            assert sp_call[1].get("stderr") is not None


# =============================================================================
# The launch: argv, cwd, environment and the written file
# =============================================================================


def _launch_document() -> dict:
    """One selector feeding one method, so the file has a root rule."""
    return {
        "nodes": [
            {"id": "sel", "type": "input_selector", "fan_mode": "out",
             "samples": ["s1"]},
            {"id": "qc", "type": "method", "method": "qc", "module": "demo",
             "script": "methods/qc/qc.py", "env": "container:demo",
             "params": {}},
        ],
        "links": [{"source": "sel", "target": "qc", "target_slot": "data"}],
        "samples": ["s1"],
    }


class TestLaunch:
    """The engine launch over a real frozen document, with only the spawn stubbed."""

    @pytest.mark.parametrize(
        "keep_going, own_path",
        [(False, False), (True, True)],
        ids=["default-file-fail-fast", "callers-file-keep-going"],
    )
    @workflow(
        purpose="run_pipeline launches one engine process over a real frozen "
                "document: the argv carries --cores and --snakefile, plus "
                "--keep-going only when asked; the cwd is the project root; "
                "the environment is the caller's plus PYTHONIOENCODING, "
                "WFC_PIPELINE_ID, WFC_PIPELINE_LOG_DIR and DATABASE_URL; and "
                "the Snakefile, at the default path in the log directory or "
                "the caller's, holds the emitted text and anchors its "
                "callbacks at the project root (Tier 3)",
    )
    def test_launch_argv_cwd_env_and_written_file(
        self, keep_going, own_path, wfc_root, monkeypatch,
    ):
        from wfc import layout
        from wfc.execution import run_pipeline

        口 = Step(step_num=1, name="Freeze a real document; stub only the spawn",
                  purpose="Generation, the write and the endings run for real; "
                          "a sentinel variable in the caller's environment "
                          "must reach the engine")
        root = Path(wfc_root)
        doc_path = root / "pipeline.json"
        doc_path.write_text(json.dumps(_launch_document()), encoding="utf-8")
        monkeypatch.setenv("WFC_LAUNCH_PIN_SENTINEL", "carried")
        pid = "pipe-launch-pin"
        snakefile_path = None
        if own_path:
            (root / "elsewhere").mkdir()
            snakefile_path = str(root / "elsewhere" / "Snakefile")

        with fake_engine_process() as popen:
            run_pipeline(
                pipeline_path=str(doc_path),
                project_root=str(root),
                wfc_root=str(root),
                cores=2,
                snakefile_path=snakefile_path,
                pipeline_id=pid,
                keep_going=keep_going,
            )

        口 = Step(step_num=2, name="One process; the argv names the engine and its flags",
                  purpose="--cores 2 --snakefile <path>, then --keep-going only "
                          "when asked; <path> is the log directory's Snakefile "
                          "or the caller's")
        assert popen.call_count == 1
        argv = popen.call_args.args[0]
        log_dir = layout.pipeline_run_dir(root, pid)
        expected_sf = Path(snakefile_path) if snakefile_path else log_dir / "Snakefile"
        assert "snakemake" in argv, argv
        expected_tail = ["--cores", "2", "--snakefile", str(expected_sf)]
        if keep_going:
            expected_tail.append("--keep-going")
        assert argv[argv.index("--cores"):] == expected_tail

        口 = Step(step_num=3, name="The cwd and the environment",
                  purpose="The project root is the cwd; the caller's environment "
                          "passes through with the four launch variables set")
        kwargs = popen.call_args.kwargs
        assert kwargs["cwd"] == str(root.resolve())
        env = kwargs["env"]
        assert env["PYTHONIOENCODING"] == "utf-8"
        assert env["WFC_PIPELINE_ID"] == pid
        assert env["WFC_PIPELINE_LOG_DIR"] == str(log_dir)
        assert env["DATABASE_URL"] == os.environ["DATABASE_URL"]
        assert env["WFC_LAUNCH_PIN_SENTINEL"] == "carried"

        口 = Step(step_num=4, name="The written file is the emitted text",
                  purpose="It names the pipeline id and the frozen document, "
                          "sets the project root and exports it for every "
                          "callback, and carries the method's rule")
        text = expected_sf.read_text(encoding="utf-8")
        assert text.startswith('"""\nAuto-generated Snakefile')
        assert f'PIPELINE_ID = "{pid}"' in text
        frozen = layout.pipeline_doc_path(root, pid)
        assert f'PIPELINE_JSON = r"{frozen.resolve()}"' in text
        assert f'PROJECT_ROOT = r"{root.resolve()}"' in text
        assert 'os.environ["WFC_PROJECT_ROOT"] = PROJECT_ROOT' in text
        assert "workdir: PROJECT_ROOT" in text
        assert 'os.environ["WFC_PIPELINE_ID"] = PIPELINE_ID' in text

    @workflow(
        purpose="The programs a run needs agree on one interpreter: "
                "run_pipeline launches the launching interpreter with "
                "-m snakemake, and the file it writes runs its onerror "
                "handler through {sys.executable} -m wfc, the form "
                "the rules' run-step and restore-sample lines already use, "
                "so a run from an unactivated environment is recorded the "
                "way it ended (Tier 2)",
    )
    def test_handlers_and_launch_name_the_launching_interpreter(self, wfc_root):
        from wfc import layout
        from wfc.execution import run_pipeline
        from wfc.persistence import Sample, get_session

        root = Path(wfc_root)
        doc_path = root / "pipeline.json"
        doc_path.write_text(json.dumps(_launch_document()), encoding="utf-8")
        # A registered hash for the one sample, so the written file carries
        # the restore rule beside the method rule. push_status is `pushed`
        # because that is what makes this fictional row internally
        # consistent with what the test asserts about it: a row whose bytes
        # are not in this bare fixture's local cache but are in the archive
        # is the documented cold start, and a cold start is exactly the
        # state in which a restore rule has to be emitted. Without it the
        # pipeline-start preflight reads the row as content that is
        # nowhere and refuses before the launch this test is about.
        with get_session() as session:
            session.add(Sample(name="s1", source_path="/src/s1.csv",
                               registered_path="data/samples/s1/s1.csv",
                               file_type="csv", content_hash="c" * 32,
                               push_status="pushed"))
            session.commit()
        pid = "pipe-launch-program"

        with fake_engine_process() as popen:
            run_pipeline(
                pipeline_path=str(doc_path), project_root=str(root),
                wfc_root=str(root), cores=1, pipeline_id=pid,
            )

        argv = popen.call_args.args[0]
        assert argv[:3] == [sys.executable, "-m", "snakemake"], argv

        text = (layout.pipeline_run_dir(root, pid) / "Snakefile").read_text(
            encoding="utf-8",
        )
        # The one callback verb the Snakefile's handlers run: onerror's
        # fail_pipeline, through the launching interpreter.
        assert ('shell(f"{sys.executable} -m wfc fail_pipeline '
                '--pipeline-id {PIPELINE_ID}")') in text
        assert ('"{sys.executable} -m wfc run-step --node-id {params.node_id} '
                '--sample {wildcards.sample} --variant {params.variant}"') in text
        assert ('"{sys.executable} -m wfc restore-sample '
                '--name {wildcards.sample} {params.hash_arg}"') in text
        assert "rule qc:" in text


# =============================================================================
# Parameter sweep pass-through
# =============================================================================

from axiom_annotations import workflow as _workflow


@_workflow(purpose="Verify _enrich_pipeline passes param_sets and "
                   "explicit_combos through unchanged so the engine sees "
                   "the canvas-compiled sweep state (Tier 2).")
def test_enrich_pipeline_passes_param_sets_and_explicit_combos(db_engine):
    """PipelineInput carrying param_sets + explicit_combos must round-trip
    through _enrich_pipeline without modification.  This covers compile
    correctness for the server-side boundary."""
    pipeline = PipelineInput(
        name="sweep_test",
        nodes=[
            PipelineNode(
                id="preprocess_1", type="method",
                method="preprocess", module="data_preprocessing",
                params={"normalize": True},
            ),
            PipelineNode(
                id="filter_1", type="method",
                method="filter_cells", module="data_preprocessing",
                params={"min_quality": 0.5},
            ),
        ],
        links=[
            PipelineLink(source="preprocess_1", target="filter_1",
                         sourceHandle="data", targetHandle="data"),
        ],
        samples=["SampleA", "SampleB"],
        param_sets={
            # Mixed: one sweep variant + one per-sample override (named per
            # the canvas compile convention {sample}__o{n}).
            "filter_1": {
                "strict":       {"min_quality": 0.7},
                "relaxed":      {"min_quality": 0.3},
                "SampleA__o1":  {"min_quality": 0.9},
            },
        },
        explicit_combos=[
            {"sample": "SampleA", "variant": "SampleA__o1"},
            {"sample": "SampleA", "variant": "strict"},
            {"sample": "SampleB", "variant": "relaxed"},
        ],
    )

    result = _enrich_pipeline(pipeline)

    # Pass-through is verbatim — no mutation, no renaming, no filtering.
    assert "param_sets" in result
    assert result["param_sets"] == {
        "filter_1": {
            "strict":       {"min_quality": 0.7},
            "relaxed":      {"min_quality": 0.3},
            "SampleA__o1":  {"min_quality": 0.9},
        },
    }
    assert "explicit_combos" in result
    assert result["explicit_combos"] == [
        {"sample": "SampleA", "variant": "SampleA__o1"},
        {"sample": "SampleA", "variant": "strict"},
        {"sample": "SampleB", "variant": "relaxed"},
    ]
    # Samples also carried through.
    assert result["samples"] == ["SampleA", "SampleB"]


def test_enrich_pipeline_omits_sweep_fields_when_empty(db_engine):
    """A pipeline without sweeps enriches with no param_sets or
    explicit_combos keys at all, not empty ones."""
    pipeline = PipelineInput(**_make_pipeline_input())
    result = _enrich_pipeline(pipeline)
    assert "param_sets" not in result
    assert "explicit_combos" not in result


# =============================================================================
# Pipeline-level error classification (surfaces DirtyRepo/env errors to canvas)
# =============================================================================


class TestClassifyPipelineError:
    """``_classify_pipeline_error`` maps pre_run exceptions to structured
    payloads so the canvas can render them in a banner with a kind/icon and
    an optional follow-up hint — not just a bare message string."""

    def test_dirty_repository_error_gets_hint(self):
        """DirtyRepositoryError is the headline case — a user who clicks Run
        with a dirty tree gets the reason and a hint."""
        from wfc.canvas.submission import _classify_pipeline_error
        from wfc.version import DirtyRepositoryError

        exc = DirtyRepositoryError(
            "Working tree has uncommitted changes to tracked files"
        )
        payload = _classify_pipeline_error(exc)
        assert payload["kind"] == "dirty_repo"
        assert "uncommitted changes" in payload["message"]
        assert payload["hint"], "dirty_repo must carry an actionable hint"

    def test_env_classification_kinds(self):
        """A pre_run lookup miss gets its own kind, and anything else is
        ``unknown``, so the UI can tell a missing method from other failures."""
        from wfc.canvas.submission import _classify_pipeline_error

        cases = [
            (ValueError("Method 'foo' not found in module 'bar'"), "not_found"),
            (RuntimeError("git rev-parse HEAD failed"), "unknown"),
        ]
        for exc, expected_kind in cases:
            payload = _classify_pipeline_error(exc)
            assert payload["kind"] == expected_kind, (
                f"{type(exc).__name__}({exc!s}) classified as "
                f"{payload['kind']!r}, expected {expected_kind!r}"
            )
            assert payload["message"], "every payload must carry a message"


def test_status_endpoint_surfaces_structured_error(client):
    """The /status endpoint passes whatever is stored on the job through —
    when the background thread stored a structured dict, the frontend gets
    the same dict (so it can render kind-specific UI)."""
    seed_active_job(
        "err-job-struct", alive=False,
        log_dir=None,
        step_map={},
        error={
            "kind": "dirty_repo",
            "message": "Working tree has uncommitted changes to tracked files",
            "hint": "Commit or stash your changes, then click Run again.",
        },
    )
    try:
        resp = client.get("/api/workflow/status/err-job-struct")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "failed"
        err = data["error"]
        assert isinstance(err, dict), (
            f"status endpoint must pass structured errors through; got {err!r}"
        )
        assert err["kind"] == "dirty_repo"
        assert "uncommitted" in err["message"]
        assert err.get("hint")
    finally:
        _active_jobs.clear()


# =============================================================================
# Variables on the canvas submission path
# =============================================================================


@workflow(
    purpose="A canvas submission with a variables block and a {$var}-bound "
            "parameter freezes the literal in pipeline.json and the "
            "pre-substitution form in pipeline.editable.json; an unknown name "
            "is refused naming it (Tier 3)",
)
def test_canvas_submission_substitutes_variables_before_the_load(client):
    """Every submission path substitutes before the load: the canvas half."""
    captured: dict = {}

    def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return 0

    Step(step_num=1, name="Submit a pipeline whose parameter is bound to a variable",
         purpose="filter_1.min_quality is a {$var: quality} ref; the variables "
                 "block declares quality = 0.9")
    payload = _make_pipeline_input()
    payload["nodes"][1]["params"] = {"min_quality": {"$var": "quality"}}
    payload["variables"] = {"quality": {"type": "number", "value": 0.9}}
    with stub_pipeline_submission(run_pipeline=fake_run_pipeline):
        resp = client.post("/api/workflow/run", json=payload)
        assert resp.status_code == 200, resp.text
        _active_jobs[resp.json()["job_id"]]["thread"].join(timeout=5)

    Step(step_num=2, name="The frozen document carries the literal; the sidecar the ref",
         purpose="pipeline.json (what run_pipeline is handed) holds 0.9 and no "
                 "variables block; pipeline.editable.json holds the ref and "
                 "the block")
    frozen_path = Path(captured["pipeline_path"])
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    (frozen_filter,) = [n for n in frozen["nodes"] if n["id"] == "filter_1"]
    assert frozen_filter["params"] == {"min_quality": 0.9}
    assert "variables" not in frozen
    editable = json.loads(
        (frozen_path.parent / "pipeline.editable.json").read_text(encoding="utf-8")
    )
    (editable_filter,) = [n for n in editable["nodes"] if n["id"] == "filter_1"]
    assert editable_filter["params"] == {"min_quality": {"$var": "quality"}}
    assert editable["variables"] == {"quality": {"type": "number", "value": 0.9}}

    Step(step_num=3, name="An unknown variable name is refused naming it",
         purpose="HTTP 400 before any run thread is spawned")
    payload["nodes"][1]["params"] = {"min_quality": {"$var": "nope"}}
    with stub_pipeline_submission(run_pipeline=fake_run_pipeline):
        resp = client.post("/api/workflow/run", json=payload)
    assert resp.status_code == 400, resp.text
    assert "nope" in resp.json()["detail"]
    _active_jobs.clear()
