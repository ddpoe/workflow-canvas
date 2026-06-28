"""
Tests for canvas workflow run system.

Covers: pipeline enrichment, run endpoint wiring,
status endpoint, pipeline ID passthrough, and capture_output.
"""

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from wfc.canvas.server import (
    app,
    _enrich_pipeline,
    _active_jobs,
    PipelineInput,
    PipelineNode,
    PipelineLink,
)
from wfc.models import Module, Method, MethodContract, Run


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """In-memory SQLite with wfc schema + seed data."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    # Seed modules and methods
    with Session(engine) as session:
        mod = Module(name="data_preprocessing", description="Preprocessing")
        session.add(mod)
        session.flush()

        m1 = Method(
            name="preprocess", module_id=mod.id,
            script_path="methods/preprocess/preprocess.py",
            env="container:demo",
        )
        m2 = Method(
            name="filter_cells", module_id=mod.id,
            script_path="methods/filter_cells/filter_cells.py",
            env="container:demo",
        )
        session.add_all([m1, m2])
        session.flush()

        # Add contracts with output slots
        mc1 = MethodContract(
            method_id=m1.id,
            input_slots={},
            output_slots={"data": {"type": ".csv"}},
            params_schema={},
        )
        mc2 = MethodContract(
            method_id=m2.id,
            input_slots={"data": {"type": ".csv", "required": True}},
            output_slots={"filtered": {"type": ".h5ad"}},
            params_schema={},
        )
        session.add_all([mc1, mc2])
        session.commit()

    yield engine


@pytest.fixture(autouse=True)
def _ready_preflight(monkeypatch):
    """Default the run-readiness pre-flight (D-6 gate) to healthy.

    The submission gate in ``run_workflow`` calls ``check_docker``/``check_git``
    before spawning the run thread.  The test tmp project has no git repo and
    Docker may be down, so happy-path run tests stub both to ``ok``.  The
    readiness-gate test overrides these to drive the reject path.
    """
    from wfc import preflight
    monkeypatch.setattr(
        preflight, "check_docker",
        lambda *a, **k: preflight.CheckResult("docker", "ok", "ok"),
    )
    monkeypatch.setattr(
        preflight, "check_git",
        lambda *a, **k: preflight.CheckResult("git", "ok", "ok"),
    )


@pytest.fixture
def client(db_engine, tmp_path, monkeypatch):
    """FastAPI test client with seeded DB."""
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))
    # Clear any active jobs from previous tests
    _active_jobs.clear()
    return TestClient(app, raise_server_exceptions=False)


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
# Task 1: Pipeline enrichment (_enrich_pipeline)
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
        assert node["script"] == "methods/preprocess/preprocess.py"
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
# Task 2: Run endpoint
# =============================================================================


class TestRunEndpoint:
    """Test the POST /api/workflow/run endpoint."""

    def test_returns_job_id(self, client):
        """Run endpoint returns a job_id and triggers execution."""
        def fake_run_pipeline(**kwargs):
            return 0

        with patch("wfc.canvas.server.run_pipeline_fn", return_value=fake_run_pipeline):
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

    def test_empty_workflow_rejected(self, client):
        """Pipeline with no nodes is rejected before execution."""
        resp = client.post(
            "/api/workflow/run",
            json=_make_pipeline_input(nodes=[], links=[]),
        )
        assert resp.status_code == 400


class TestRunReadinessGate:
    """D-6 3-lite: submission is gated on Docker/git readiness."""

    def test_docker_down_rejects_with_kind_tag_no_thread(self, client, monkeypatch):
        """Docker down → 409 {kind,message,hint}; no thread/orphan row spawned."""
        from wfc import preflight
        monkeypatch.setattr(
            preflight, "check_docker",
            lambda *a, **k: preflight.CheckResult(
                "docker", "fail",
                "Docker is installed but the daemon is not running.",
                "Start Docker Desktop and try again.",
            ),
        )
        # If the gate failed to short-circuit, run_pipeline_fn would be called.
        called = {"ran": False}

        def _should_not_run(**kwargs):
            called["ran"] = True
            return 0

        _active_jobs.clear()
        with patch("wfc.canvas.server.run_pipeline_fn", return_value=_should_not_run):
            resp = client.post("/api/workflow/run", json=_make_pipeline_input())

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["kind"] == "not_runnable_docker"
        assert "daemon is not running" in detail["message"]
        assert detail["hint"]
        assert called["ran"] is False
        assert _active_jobs == {}

    def test_git_not_ready_rejects_with_kind_tag(self, client, monkeypatch):
        """git not ready (and Docker ok) → 409 not_runnable_git."""
        from wfc import preflight
        monkeypatch.setattr(
            preflight, "check_git",
            lambda *a, **k: preflight.CheckResult(
                "git", "fail", "No git repository here.", "Run `wfc init`.",
            ),
        )
        _active_jobs.clear()
        with patch("wfc.canvas.server.run_pipeline_fn", return_value=lambda **k: 0):
            resp = client.post("/api/workflow/run", json=_make_pipeline_input())
        assert resp.status_code == 409
        assert resp.json()["detail"]["kind"] == "not_runnable_git"
        assert _active_jobs == {}


# =============================================================================
# Task 3: Status endpoint
# =============================================================================


class TestStatusEndpoint:
    """Test GET /api/workflow/status/{job_id}."""

    def test_unknown_job_returns_not_found(self, client):
        """Unknown job_id returns 404."""
        resp = client.get("/api/workflow/status/nonexistent")
        assert resp.status_code == 404

    def test_pending_status(self, client, db_engine):
        """Job with no runs yet shows pending status."""
        _active_jobs["test-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=True)),
            "pipeline_id": "test-job",
            "log_dir": None,
        }

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
                pipeline_id="done-job",
                status="completed",
                sample="Pa16c",
            )
            session.add(run)
            session.commit()

        _active_jobs["done-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=False)),
            "pipeline_id": "done-job",
            "log_dir": None,
            "step_map": {"preprocess_1": "preprocess"},
        }

        resp = client.get("/api/workflow/status/done-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "completed"

        _active_jobs.clear()

    def test_mixed_status_fanout_partial_failure(self, client, db_engine):
        """Fan-out over 4 samples with 1 failed + 3 completed → node 'mixed',
        pipeline 'completed_with_failures'. Exercises the per-sample tally
        path: the old last-write-wins aggregation would have returned
        whatever status happened to come last in the DB row order.
        """
        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            # 3 successes + 1 failure — all same (pipeline_id, method).
            for sample in ("s1", "s2", "s3"):
                session.add(Run(
                    method_id=method.id, pipeline_id="mix-job",
                    status="completed", sample=sample,
                ))
            session.add(Run(
                method_id=method.id, pipeline_id="mix-job",
                status="failed", sample="s4",
                error_message="intentional",
            ))
            session.commit()

        _active_jobs["mix-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=False)),
            "pipeline_id": "mix-job",
            "log_dir": None,
            "step_map": {"preprocess_1": "preprocess"},
        }

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
                pipeline_id="fail-job",
                status="failed",
                sample="Pa16c",
                error_message="Something went wrong",
            )
            session.add(run)
            session.commit()

        _active_jobs["fail-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=False)),
            "pipeline_id": "fail-job",
            "log_dir": None,
            "step_map": {"preprocess_1": "preprocess"},
        }

        resp = client.get("/api/workflow/status/fail-job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "failed"

        _active_jobs.clear()

    def test_failed_run_error_surfaces_on_node(self, client, db_engine):
        """A failed Run's error_message is attached to node_states[id].error
        so the Inspector can show it without the user opening the log stream.

        Regression for the pre-cycle gap where failed nodes turned red on the
        canvas with no inline explanation — users had to click through to the
        Output tab and wait for the log stream to finish to see what broke.
        """
        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            run = Run(
                method_id=method.id,
                pipeline_id="node-err-job",
                status="failed",
                sample="Pa16c",
                error_message="TypeError: 'NoneType' is not subscriptable at line 42",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            expected_run_id = str(run.id)

        _active_jobs["node-err-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=False)),
            "pipeline_id": "node-err-job",
            "log_dir": None,
            "step_map": {"preprocess_1": "preprocess"},
        }

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
                method_id=method.id, pipeline_id="big-err-job",
                status="failed", sample="s1", error_message=huge,
            ))
            session.commit()

        _active_jobs["big-err-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=False)),
            "pipeline_id": "big-err-job",
            "log_dir": None,
            "step_map": {"preprocess_1": "preprocess"},
        }
        resp = client.get("/api/workflow/status/big-err-job")
        node = resp.json()["node_states"]["preprocess_1"]
        assert node["error"].endswith("…")
        assert len(node["error"]) < len(huge)
        _active_jobs.clear()

    def test_cancelled_status(self, client, db_engine):
        """All-cancelled runs derive overall_status="cancelled".

        Without the cancelled arm, _aggregate returned "unknown" for the
        node and the overall chain fell through to its "running"
        fallback — leaving a cancelled pipeline reporting in-flight
        forever and wedging the canvas Run/Stop button.
        """
        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            for sample in ("s1", "s2"):
                session.add(Run(
                    method_id=method.id, pipeline_id="cancelled-job",
                    status="cancelled", sample=sample,
                    error_message="Cancelled by user",
                ))
            session.commit()

        _active_jobs["cancelled-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=False)),
            "pipeline_id": "cancelled-job",
            "log_dir": None,
            "step_map": {"preprocess_1": "preprocess"},
        }

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
                status="failed", sample="s1", error_message="boom",
            ))
            session.add(Run(
                method_id=method_b.id, pipeline_id="mix-cancel-fail-job",
                status="cancelled", sample="s1",
                error_message="Cancelled by user",
            ))
            session.commit()

        _active_jobs["mix-cancel-fail-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=False)),
            "pipeline_id": "mix-cancel-fail-job",
            "log_dir": None,
            "step_map": {
                "preprocess_1": "preprocess",
                "filter_cells_1": "filter_cells",
            },
        }

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

    def test_log_content_returned(self, client, db_engine, tmp_path):
        """Status endpoint returns captured log file content (US-4)."""
        log_dir = tmp_path / "log-job-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stdout.log").write_text(
            "Running rule preprocess...\nFinished.\n", encoding="utf-8",
        )
        (log_dir / "stderr.log").write_text(
            "Warning: low memory\n", encoding="utf-8",
        )

        with Session(db_engine) as session:
            method = session.exec(select(Method)).first()
            run = Run(
                method_id=method.id,
                pipeline_id="log-job",
                status="completed",
                sample="Pa16c",
            )
            session.add(run)
            session.commit()

        _active_jobs["log-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=False)),
            "pipeline_id": "log-job",
            "log_dir": str(log_dir),
            "error": None,
            "step_map": {"preprocess_1": "preprocess"},
        }

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
        _active_jobs["err-job"] = {
            "thread": MagicMock(is_alive=MagicMock(return_value=False)),
            "pipeline_id": "err-job",
            "log_dir": None,
            "error": "Could not find Snakemake executable",
        }

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
        """When pipeline_id is passed, run_pipeline uses it instead of generating one."""
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
        from wfc.database import reset_engine
        reset_engine()

        caller_id = "caller-provided-id-1234"

        # Phase D Pass 2: run_pipeline uses subprocess.Popen + .wait() now.
        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0
        fake_proc.returncode = 0
        with patch("wfc.snakemake_gen.load_pipeline") as mock_load, \
             patch("wfc.snakemake_gen.generate_snakefile", return_value="# fake") as mock_gen, \
             patch("subprocess.Popen", return_value=fake_proc):
            mock_load.return_value = {"nodes": [], "links": [], "samples": []}

            from wfc.cli import run_pipeline
            run_pipeline(
                pipeline_path=str(tmp_path / "pipeline.json"),
                project_root=str(tmp_path),
                wfc_root=str(tmp_path),
                pipeline_id=caller_id,
            )

            gen_call_kwargs = mock_gen.call_args
            assert gen_call_kwargs[1].get("pipeline_id") == caller_id

            log_dir = tmp_path / ".runs" / "pipelines" / caller_id
            assert log_dir.exists()

    def test_run_pipeline_generates_id_when_not_provided(self, tmp_path, monkeypatch):
        """When pipeline_id is omitted, run_pipeline generates its own UUID."""
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
        from wfc.database import reset_engine
        reset_engine()

        # Phase D Pass 2: run_pipeline uses subprocess.Popen + .wait() now.
        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0
        fake_proc.returncode = 0
        with patch("wfc.snakemake_gen.load_pipeline") as mock_load, \
             patch("wfc.snakemake_gen.generate_snakefile", return_value="# fake") as mock_gen, \
             patch("subprocess.Popen", return_value=fake_proc):
            mock_load.return_value = {"nodes": [], "links": [], "samples": []}

            from wfc.cli import run_pipeline
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

        with patch("wfc.canvas.server.run_pipeline_fn", return_value=fake_run_pipeline):
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

        Phase D Pass 2: run_pipeline switched from subprocess.run to
        subprocess.Popen + .wait() so the canvas cancel endpoint can SIGTERM
        the live process. The stdout/stderr kwargs the test inspects come
        through the Popen call now.
        """
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
        from wfc.database import reset_engine
        reset_engine()

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0
        fake_proc.returncode = 0
        with patch("wfc.snakemake_gen.load_pipeline") as mock_load, \
             patch("wfc.snakemake_gen.generate_snakefile", return_value="# fake"), \
             patch("subprocess.Popen", return_value=fake_proc) as mock_sp:
            mock_load.return_value = {"nodes": [], "links": [], "samples": []}

            from wfc.cli import run_pipeline
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

        Phase D Pass 2: see note on Popen mock in the sibling test above.
        """
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
        from wfc.database import reset_engine
        reset_engine()

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0
        fake_proc.returncode = 0
        with patch("wfc.snakemake_gen.load_pipeline") as mock_load, \
             patch("wfc.snakemake_gen.generate_snakefile", return_value="# fake"), \
             patch("subprocess.Popen", return_value=fake_proc) as mock_sp:
            mock_load.return_value = {"nodes": [], "links": [], "samples": []}

            from wfc.cli import run_pipeline
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
# Parameter sweep pass-through (pev-2026-04-17-parameter-sweeps-chip-ux)
# =============================================================================

from axiom_annotations import workflow as _workflow


@_workflow(purpose="Verify _enrich_pipeline passes param_sets and "
                   "explicit_combos through unchanged so the engine sees "
                   "the canvas-compiled sweep state (Tier 2).")
def test_enrich_pipeline_passes_param_sets_and_explicit_combos(db_engine):
    """PipelineInput carrying param_sets + explicit_combos must round-trip
    through _enrich_pipeline without modification.  This covers US-1, US-3
    compile correctness for the server-side boundary."""
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
    """Legacy pipelines without sweeps should not grow new keys — keeps
    the JSON diff clean for pre-cycle pipelines."""
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
        """DirtyRepositoryError is the headline case — user clicked Run with
        a dirty tree and currently sees nothing."""
        from wfc.canvas.server import _classify_pipeline_error
        from wfc.version import DirtyRepositoryError

        exc = DirtyRepositoryError(
            "Working tree has uncommitted changes to tracked files"
        )
        payload = _classify_pipeline_error(exc)
        assert payload["kind"] == "dirty_repo"
        assert "uncommitted changes" in payload["message"]
        assert payload["hint"], "dirty_repo must carry an actionable hint"

    def test_env_classification_kinds(self):
        """Each pre_run env-failure mode gets a distinct kind so the UI can
        differentiate spec bugs from missing locks from bad env names."""
        from wfc.canvas.server import _classify_pipeline_error

        cases = [
            (
                ValueError("Malformed pixi env spec 'pixi:': expected 'pixi:<name>'"),
                "env_spec",
            ),
            (
                FileNotFoundError("No pixi.lock found for project 'wcia'."),
                "env_lock_missing",
            ),
            (
                KeyError(
                    "pixi.lock at /p/wcia/pixi.lock has no env named 'missing'. "
                    "Available envs: ['cc-mapping']"
                ),
                "env_name_missing",
            ),
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
    _active_jobs["err-job-struct"] = {
        "thread": MagicMock(is_alive=MagicMock(return_value=False)),
        "pipeline_id": "err-job-struct",
        "log_dir": None,
        "step_map": {},
        "error": {
            "kind": "dirty_repo",
            "message": "Working tree has uncommitted changes to tracked files",
            "hint": "Commit or stash your changes, then click Run again.",
        },
    }
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
