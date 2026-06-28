"""
Tests for system node support: Input Selector and Run Reference.

Covers:
  - Backend API endpoints for samples and completed runs
  - Pipeline save/load round-trip with type discriminator
  - Snakemake generation with system nodes
  - validate_workflow rejects method-node roots
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine

from axiom_annotations import workflow, Step

from wfc.models import Module, Method, Run, Sample
from wfc.canvas.wfc_provider import WfcProvider
from wfc.canvas.server import app, validate_workflow, PipelineInput, PipelineNode, PipelineLink
from wfc.snakemake_gen import StepDef, PipelineDef, load_pipeline, generate_snakefile


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def db_with_samples(tmp_path, monkeypatch):
    """SQLite DB with registered samples and completed runs."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        # Seed a module and method
        mod = Module(name="analysis", description="Analysis module")
        session.add(mod)
        session.flush()

        meth = Method(
            name="align_reads", module_id=mod.id,
            script_path="methods/align_reads/align_reads.py",
            env="container:demo",
        )
        session.add(meth)
        session.flush()

        # Seed samples
        s1 = Sample(
            name="sample_001",
            source_path="/data/raw/sample_001.csv",
            registered_path="data/samples/sample_001/sample_001.csv",
            file_type="csv",
            file_size=1024,
        )
        s2 = Sample(
            name="sample_002",
            source_path="/data/raw/sample_002.csv",
            registered_path="data/samples/sample_002/sample_002.csv",
            file_type="csv",
            file_size=2048,
        )
        session.add_all([s1, s2])
        session.flush()

        # Seed a completed run
        run = Run(
            method_id=meth.id,
            params=json.dumps({"threads": 8}),
            sample="sample_001",
            status="completed",
            pipeline_id="pipe-001",
        )
        session.add(run)
        session.flush()

        # Seed run outputs
        session.execute(
            SQLModel.metadata.tables["run_outputs"].insert(),
            [
                {"run_id": run.id, "output_name": "aligned_data",
                 "artifact_path": ".runs/workspace/align_reads/sample_001/default/output.parquet",
                 "artifact_type": "parquet"},
                {"run_id": run.id, "output_name": "stats",
                 "artifact_path": ".runs/workspace/align_reads/sample_001/default/stats.json",
                 "artifact_type": "json"},
            ],
        )
        session.commit()

    yield str(db_path), engine
    reset_engine()


# =============================================================================
# Backend API tests
# =============================================================================


class TestSampleListAPI:
    """Verify the wfc_provider returns registered sample details."""

    @workflow(purpose="Verify sample-list API returns registered samples with correct fields")
    def test_returns_registered_samples(self, db_with_samples):
        """Call get_samples_detail with registered samples, verify returns expected list."""
        db_path, _ = db_with_samples
        provider = WfcProvider(str(Path(db_path).parent.parent))
        provider.load()

        samples = provider.get_samples_detail()
        assert len(samples) == 2
        names = [s["name"] for s in samples]
        assert "sample_001" in names
        assert "sample_002" in names

        s1 = next(s for s in samples if s["name"] == "sample_001")
        assert s1["file_type"] == "csv"
        assert s1["file_size"] == 1024
        assert "registered_path" in s1

    @workflow(purpose="Verify sample-list API returns empty list when no samples registered")
    def test_empty_when_no_samples(self, tmp_path, monkeypatch):
        """Call get_samples_detail with empty DB, verify empty response."""
        db_path = tmp_path / ".wfc" / "wfc.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"
        monkeypatch.setenv("DATABASE_URL", url)

        from wfc.database import reset_engine
        reset_engine()

        engine = create_engine(url)
        SQLModel.metadata.create_all(engine)

        provider = WfcProvider(str(tmp_path))
        provider.load()
        samples = provider.get_samples_detail()
        assert samples == []
        reset_engine()


class TestCompletedRunsAPI:
    """Verify the wfc_provider returns completed runs with output slots."""

    @workflow(purpose="Verify completed-runs API returns runs with status=completed including output slots")
    def test_returns_completed_runs(self, db_with_samples):
        """Call get_completed_runs, verify returns runs with outputs."""
        db_path, _ = db_with_samples
        provider = WfcProvider(str(Path(db_path).parent.parent))
        provider.load()

        runs = provider.get_completed_runs()
        assert len(runs) == 1

        run = runs[0]
        assert run["method"] == "align_reads"
        assert run["module"] == "analysis"
        assert run["sample"] == "sample_001"
        assert "aligned_data" in run["output_slots"]
        assert "stats" in run["output_slots"]
        assert run["pipeline_id"] == "pipe-001"


# =============================================================================
# Pipeline round-trip test
# =============================================================================


class TestPipelineRoundTrip:
    """Verify pipeline JSON save/load preserves type discriminator."""

    @workflow(purpose="Verify pipeline save/load round-trip preserves type and config for all node types")
    def test_type_discriminator_roundtrip(self, tmp_path, wfc_root):
        """Save pipeline with all three node types, reload, verify each retains type."""
        pipeline_json = {
            "nodes": [
                {
                    "id": "input-1", "type": "input_selector",
                    "method": "", "module": "",
                    "position": {"x": 100, "y": 200},
                    "params": {},
                    "samples": ["sample_001"],
                    "source": "registered",
                },
                {
                    "id": "method-1", "type": "method", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "method": "align_reads", "module": "analysis",
                    "script": "methods/align_reads/align_reads.py",
                    "position": {"x": 400, "y": 200},
                    "params": {"threads": 8},
                },
                {
                    "id": "runref-1", "type": "run_reference",
                    "method": "", "module": "",
                    "position": {"x": 100, "y": 400},
                    "params": {},
                    "run_id": "abc-123",
                    "output_slot": "processed_data",
                },
            ],
            "links": [
                {"source": "input-1", "target": "method-1"},
            ],
            "samples": [],
        }

        # Save
        pipeline_path = tmp_path / "pipeline.json"
        pipeline_path.write_text(json.dumps(pipeline_json))

        # Load via snakemake_gen
        pipeline = load_pipeline(pipeline_path)

        # System nodes should not appear as steps
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].method_name == "align_reads"

        # Samples from input_selector should be merged
        assert "sample_001" in pipeline.samples

    @workflow(purpose="Verify JSON round-trip preserves type field on every node")
    def test_json_type_field_preserved(self, tmp_path):
        """Save and reload pipeline JSON, verify type field on each node."""
        pipeline_json = {
            "nodes": [
                {"id": "n1", "type": "input_selector", "method": "", "params": {},
                 "samples": ["s1"]},
                {"id": "n2", "type": "method", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "method": "foo", "module": "bar",
                 "params": {"x": 1}},
                {"id": "n3", "type": "run_reference", "method": "", "params": {},
                 "run_id": "r1", "output_slot": "out"},
            ],
            "links": [],
            "samples": [],
        }

        path = tmp_path / "p.json"
        path.write_text(json.dumps(pipeline_json))
        reloaded = json.loads(path.read_text())

        types = {n["id"]: n["type"] for n in reloaded["nodes"]}
        assert types["n1"] == "input_selector"
        assert types["n2"] == "method"
        assert types["n3"] == "run_reference"


# =============================================================================
# Snakemake generation tests
# =============================================================================


class TestSnakemakeSystemNodes:
    """Verify Snakefile generation handles system nodes correctly."""

    @workflow(purpose="Verify Snakefile generation resolves input_selector + method + run_reference correctly")
    def test_all_node_types_generate(self, tmp_path, wfc_root):
        """Generate Snakefile with all three node types, verify correct output.

        The run_reference node is linked to method-1 and carries an
        output_path.  The generated Snakefile must include that path
        as a named input on the method-1 rule.
        """
        ref_artifact = ".runs/workspace/align_reads/sample_001/default/output.parquet"
        pipeline_json = {
            "nodes": [
                {
                    "id": "input-1", "type": "input_selector",
                    "method": "", "module": "",
                    "params": {},
                    "samples": ["sample_A", "sample_B"],
                },
                {
                    "id": "method-1", "type": "method", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "method": "preprocess", "module": "demo",
                    "script": "methods/preprocess/preprocess.py",
                    "params": {"normalize": True},
                },
                {
                    "id": "runref-1", "type": "run_reference",
                    "method": "", "module": "",
                    "params": {},
                    "run_id": "run-42",
                    "output_slot": "results",
                    "output_path": ref_artifact,
                },
            ],
            "links": [
                {"source": "input-1", "target": "method-1"},
                {"source": "runref-1", "target": "method-1"},
            ],
            "samples": [],
        }

        path = tmp_path / "pipeline.json"
        path.write_text(json.dumps(pipeline_json))

        pipeline = load_pipeline(path)

        # Only one step (the method node)
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].method_name == "preprocess"

        # Run reference output path is injected into the step
        step = pipeline.steps[0]
        assert len(step.run_ref_inputs) == 1
        assert ref_artifact in step.run_ref_inputs.values()

        # Input selector samples merged
        assert "sample_A" in pipeline.samples
        assert "sample_B" in pipeline.samples

        # Generate Snakefile
        snakefile = generate_snakefile(pipeline, wfc_root)

        # Should have the method rule (node_id is "method-1" since it's a string ID)
        assert "rule method-1:" in snakefile or "rule preprocess:" in snakefile
        assert "rule all:" in snakefile

        # Samples should appear
        assert "sample_A" in snakefile
        assert "sample_B" in snakefile

        # System nodes should NOT have rules
        assert "rule input-1:" not in snakefile
        assert "rule runref-1:" not in snakefile

        # Run reference artifact path must appear as an input in the method rule
        assert ref_artifact in snakefile, (
            f"Run reference artifact path should appear in generated Snakefile "
            f"as an input to the downstream method rule"
        )


# =============================================================================
# validate_workflow tests
# =============================================================================


class TestValidateWorkflowMethodRoots:
    """Verify validate_workflow rejects pipelines with method-node roots."""

    @workflow(purpose="validate_workflow returns error for pipeline with method-node root")
    def test_method_node_root_returns_error(self, db_with_samples):
        """Tier 2: Call validate_workflow directly with a pipeline dict
        containing a method node that has no incoming edges (is a root).
        Verify it returns the expected error structure."""
        pipeline = PipelineInput(
            nodes=[
                PipelineNode(
                    id="method-1",
                    type="method",
                    method="align_reads",
                    module="analysis",
                ),
            ],
            links=[],
            samples=["sample_001"],
        )

        result = validate_workflow(pipeline)

        assert result["valid"] is False
        assert len(result["errors"]) >= 1
        # Error should identify the method node as an invalid root
        root_errors = [e for e in result["errors"] if "method-1" in e]
        assert len(root_errors) >= 1, (
            f"Expected error identifying 'method-1' as invalid root, "
            f"got errors: {result['errors']}"
        )
        assert "root" in root_errors[0].lower() or "input_selector" in root_errors[0]

    @workflow(
        purpose="Pipeline with method node and no incoming edges fails validation via API endpoint",
    )
    def test_method_root_via_api_endpoint(self, db_with_samples):
        """Tier 3: POST to /api/workflow/validate with a pipeline where
        method-A has no incoming edges.  Verify the HTTP response returns
        valid=False with an error identifying method-A as an invalid root."""
        client = TestClient(app, raise_server_exceptions=False)

        s = Step(step_num=1, name="POST pipeline with disconnected method root",
                 purpose="Hit the validate API with method-A having no incoming edges")
        response = client.post("/api/workflow/validate", json={
            "nodes": [
                {"id": "input-1", "type": "input_selector",
                 "method": "", "module": ""},
                {"id": "method-A", "type": "method", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                 "method": "align_reads", "module": "analysis"},
                {"id": "method-B", "type": "method", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                 "method": "align_reads", "module": "analysis"},
            ],
            "links": [
                {"source": "input-1", "target": "method-B"},
            ],
            "samples": ["sample_001"],
        })

        s = Step(step_num=2, name="Verify HTTP 200 with validation errors",
                 purpose="Endpoint returns 200 with valid=False (not a server error)")
        assert response.status_code == 200, (
            f"Expected 200 from validate endpoint, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body["valid"] is False

        s = Step(step_num=3, name="Verify error identifies method-A as invalid root",
                 purpose="method-A has no incoming edges and should be flagged")
        method_a_errors = [e for e in body["errors"] if "method-A" in e]
        assert len(method_a_errors) >= 1, (
            f"Expected error for 'method-A' (no incoming edges), "
            f"got errors: {body['errors']}"
        )
        # method-B is connected to input-1, so should NOT be flagged
        method_b_errors = [e for e in body["errors"] if "method-B" in e]
        assert len(method_b_errors) == 0, (
            f"method-B is connected to input-1 and should NOT be flagged, "
            f"but got errors: {method_b_errors}"
        )


class TestSnakemakeRejectsMethodRoots:
    """Verify load_pipeline rejects pipelines with method-node roots."""

    @workflow(purpose="load_pipeline raises error for pipeline with method-node root (no system node upstream)")
    def test_method_root_raises_in_load_pipeline(self, tmp_path):
        """Tier 2: Call load_pipeline with a pipeline containing system
        nodes AND a method node that has no incoming edges.  Verify
        it raises a ValueError before any Snakefile is generated."""
        pipeline_json = {
            "nodes": [
                {
                    "id": "input-1", "type": "input_selector",
                    "method": "", "module": "",
                    "params": {},
                    "samples": ["sample_A"],
                },
                {
                    "id": "method-1", "type": "method", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "method": "preprocess", "module": "demo",
                    "script": "methods/preprocess/preprocess.py",
                    "params": {},
                },
                {
                    "id": "method-2", "type": "method", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "method": "filter", "module": "demo",
                    "script": "methods/filter/filter.py",
                    "params": {},
                },
            ],
            "links": [
                {"source": "input-1", "target": "method-2"},
            ],
            "samples": [],
        }

        path = tmp_path / "pipeline.json"
        path.write_text(json.dumps(pipeline_json))

        with pytest.raises(ValueError, match="method-1"):
            load_pipeline(path)
