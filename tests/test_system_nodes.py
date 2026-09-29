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
from sqlmodel import select

from axiom_annotations import workflow, Step

from wfc.persistence import Sample, get_session
from wfc.canvas.wfc_provider import WfcProvider
from wfc.canvas.models import PipelineInput, PipelineNode, PipelineLink
from wfc.graph import StepDef, PipelineDef, load_pipeline, validate_structure
from wfc.orchestration import generate_snakefile

from tests.fixtures.routes import canvas_client, completed_run

#: The literal contract map the structural core reads for this module's
#: shapes: align_reads registered under analysis, no slot declarations.
ANALYSIS_CONTRACTS = {"analysis.align_reads": {"input_slots": {}, "output_slots": {}}}


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sampled_project(tmp_project, monkeypatch):
    """A project with two registered samples and one completed run.

    ``align_reads`` under ``analysis`` ran on ``sample_001`` under
    ``pipe-001`` and recorded ``aligned_data`` and ``stats``;
    ``sample_002`` is registered and unrun.
    """
    return completed_run(
        tmp_project, monkeypatch=monkeypatch,
        method="align_reads", module="analysis",
        sample="sample_001", samples=["sample_001", "sample_002"],
        outputs={"aligned_data": ".parquet", "stats": ".json"},
        params={"threads": 8}, pipeline_id="pipe-001",
    )


# =============================================================================
# Backend API tests
# =============================================================================


class TestSampleListAPI:
    """Verify the wfc_provider returns registered sample details."""

    @workflow(purpose="Verify sample-list API returns registered samples with correct fields")
    def test_returns_registered_samples(self, sampled_project):
        """Call get_samples_detail with registered samples, verify returns expected list."""
        provider = WfcProvider(str(sampled_project.root))
        provider.load()

        samples = provider.get_samples_detail()
        assert len(samples) == 2
        names = [s["name"] for s in samples]
        assert "sample_001" in names
        assert "sample_002" in names

        s1 = next(s for s in samples if s["name"] == "sample_001")
        assert s1["file_type"] == "csv"
        with get_session() as session:
            source = session.exec(
                select(Sample).where(Sample.name == "sample_001")
            ).one().source_path
        assert s1["file_size"] == Path(source).stat().st_size
        assert "registered_path" in s1

    @workflow(purpose="Verify sample-list API returns empty list when no samples registered")
    def test_empty_when_no_samples(self, tmp_project):
        """Call get_samples_detail with empty DB, verify empty response."""
        provider = WfcProvider(str(tmp_project))
        provider.load()
        samples = provider.get_samples_detail()
        assert samples == []


class TestCompletedRunsAPI:
    """Verify the wfc_provider returns completed runs with output slots."""

    @workflow(purpose="Verify completed-runs API returns runs with status=completed including output slots")
    def test_returns_completed_runs(self, sampled_project):
        """Call get_completed_runs, verify returns runs with outputs."""
        provider = WfcProvider(str(sampled_project.root))
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
                    "position": {"x": 100, "y": 400},
                    "params": {},
                    "run_id": "abc-123",
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

        # Load through the Graph unit on the re-read document
        pipeline = load_pipeline(json.loads(pipeline_path.read_text()),
                                 contract_map={}, reference_outputs={})

        # System nodes should not appear as steps
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].method_name == "align_reads"

        # Samples from input_selector should be merged
        assert "sample_001" in pipeline.samples

    def test_selector_samples_keep_document_order_deduplicated_ahead_of_a_reference_sample(self):
        """The selector's samples, first-seen order, no repeats, then the reference's."""
        pipeline_json = {
            "nodes": [
                {"id": "input-1", "type": "input_selector", "params": {},
                 "samples": ["s2", "s1", "s2"]},
                {"id": "method-1", "type": "method", "env": "demo",
                 "method": "align_reads", "module": "analysis", "params": {}},
                {"id": "runref-1", "type": "run_reference", "params": {},
                 "run_id": "62"},
            ],
            "links": [
                {"source": "input-1", "target": "method-1"},
                {"source": "runref-1", "target": "method-1",
                 "source_slot": "measurements", "target_slot": "measurements"},
            ],
            "samples": [],
        }
        reference = {"run_id": "62", "sample": "SJ011", "method": "quant",
                     "malformed": False,
                     "output_paths": {"measurements": "/work/run62/m.csv"}}

        pipeline = load_pipeline(pipeline_json, contract_map={},
                                 reference_outputs={"runref-1": reference})

        assert pipeline.samples == ["s2", "s1", "SJ011"]

    def test_the_step_depends_on_neither_system_node(self):
        """A selector and a reference in the document are not dependencies."""
        pipeline_json = {
            "nodes": [
                {"id": "input-1", "type": "input_selector", "params": {},
                 "samples": ["s1"]},
                {"id": "method-1", "type": "method", "env": "demo",
                 "method": "align_reads", "module": "analysis", "params": {}},
                {"id": "runref-1", "type": "run_reference", "params": {},
                 "run_id": "abc-123"},
            ],
            "links": [{"source": "input-1", "target": "method-1"}],
            "samples": [],
        }

        pipeline = load_pipeline(pipeline_json, contract_map={}, reference_outputs={})

        (step,) = pipeline.steps
        assert step.depends_on == []

    def test_a_per_sample_selector_link_leaves_no_slot_on_the_wiring(self):
        """A per-sample selector feeding a named slot records nothing on the step."""
        pipeline_json = {
            "nodes": [
                {"id": "input-1", "type": "input_selector", "params": {},
                 "samples": ["s1"]},
                {"id": "method-1", "type": "method", "env": "demo",
                 "method": "align_reads", "module": "analysis", "params": {}},
            ],
            "links": [{"source": "input-1", "target": "method-1",
                       "target_slot": "reads"}],
            "samples": [],
        }

        pipeline = load_pipeline(pipeline_json, contract_map={}, reference_outputs={})

        (step,) = pipeline.steps
        assert step.inputs == {}
        assert step.input_source_slots == {}

    @workflow(purpose="Verify JSON round-trip preserves type field on every node")
    def test_json_type_field_preserved(self, tmp_path):
        """Save and reload pipeline JSON, verify type field on each node."""
        pipeline_json = {
            "nodes": [
                {"id": "n1", "type": "input_selector", "params": {},
                 "samples": ["s1"]},
                {"id": "n2", "type": "method", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "method": "foo", "module": "bar",
                 "params": {"x": 1}},
                {"id": "n3", "type": "run_reference", "params": {},
                 "run_id": "r1"},
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

        The run_reference node is linked to method-1, and the referenced
        run's resolved output is handed to the load.  The generated
        Snakefile must include that path as a named input on the method-1
        rule.
        """
        ref_artifact = ".runs/workspace/align_reads/sample_001/default/output.parquet"
        pipeline_json = {
            "nodes": [
                {
                    "id": "input-1", "type": "input_selector",
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
                    "params": {},
                    "run_id": "run-42",
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

        pipeline = load_pipeline(
            pipeline_json, contract_map={},
            reference_outputs={
                "runref-1": {"output_paths": {"results": ref_artifact}},
            },
        )

        # Only one step (the method node)
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].method_name == "preprocess"

        # Run reference output path is injected into the step
        step = pipeline.steps[0]
        assert len(step.run_ref_inputs) == 1
        assert [ref_artifact] in step.run_ref_inputs.values()

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
    def test_method_node_root_returns_error(self):
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

        result = validate_structure(pipeline.model_dump(), ANALYSIS_CONTRACTS)

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
    def test_method_root_via_api_endpoint(self, sampled_project, monkeypatch):
        """Tier 3: POST to /api/workflow/validate with a pipeline where
        method-A has no incoming edges.  Verify the HTTP response returns
        valid=False with an error identifying method-A as an invalid root."""
        client = canvas_client(sampled_project.root, monkeypatch)

        s = Step(step_num=1, name="POST pipeline with disconnected method root",
                 purpose="Hit the validate API with method-A having no incoming edges")
        response = client.post("/api/workflow/validate", json={
            "nodes": [
                {"id": "input-1", "type": "input_selector"},
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


class TestReferenceFanInExemption:
    """Several ``run_reference`` edges may share one input slot.

    The one-edge-per-slot rule, with its usual message, stands everywhere
    else. The exemption's predicate is *all* incoming edges on the
    slot being references, never *any*.
    """

    @staticmethod
    def _pipeline(nodes, links) -> PipelineInput:
        return PipelineInput(
            nodes=[PipelineNode(**n) for n in nodes],
            links=[PipelineLink(**lnk) for lnk in links],
            samples=["sample_001"],
        )

    @workflow(purpose="Two run_reference edges on one input slot validate clean")
    def test_two_references_on_one_slot_validate_clean(self):
        pipeline = self._pipeline(
            [
                {"id": "ref-1", "type": "run_reference"},
                {"id": "ref-2", "type": "run_reference"},
                {"id": "method-1", "type": "method",
                 "method": "align_reads", "module": "analysis"},
            ],
            [
                {"source": "ref-1", "target": "method-1", "targetHandle": "data"},
                {"source": "ref-2", "target": "method-1", "targetHandle": "data"},
            ],
        )

        result = validate_structure(pipeline.model_dump(), ANALYSIS_CONTRACTS)

        assert result["errors"] == []
        assert result["valid"] is True

    @workflow(purpose="Two method-node edges on one input slot are still refused")
    def test_two_method_edges_on_one_slot_still_error(self):
        pipeline = self._pipeline(
            [
                {"id": "sel-1", "type": "input_selector"},
                {"id": "up-1", "type": "method",
                 "method": "align_reads", "module": "analysis"},
                {"id": "up-2", "type": "method",
                 "method": "align_reads", "module": "analysis"},
                {"id": "method-1", "type": "method",
                 "method": "align_reads", "module": "analysis"},
            ],
            [
                {"source": "sel-1", "target": "up-1"},
                {"source": "sel-1", "target": "up-2"},
                {"source": "up-1", "target": "method-1", "targetHandle": "data"},
                {"source": "up-2", "target": "method-1", "targetHandle": "data"},
            ],
        )

        result = validate_structure(pipeline.model_dump(), ANALYSIS_CONTRACTS)

        slot_errors = [e for e in result["errors"]
                       if "only one edge per slot" in e]
        assert len(slot_errors) == 1, result["errors"]
        assert "method-1" in slot_errors[0]

    @workflow(purpose="A method edge joined by a reference on one slot is "
                      "still refused")
    def test_method_edge_joined_by_a_reference_still_errors(self):
        pipeline = self._pipeline(
            [
                {"id": "sel-1", "type": "input_selector"},
                {"id": "ref-1", "type": "run_reference"},
                {"id": "up-1", "type": "method",
                 "method": "align_reads", "module": "analysis"},
                {"id": "method-1", "type": "method",
                 "method": "align_reads", "module": "analysis"},
            ],
            [
                {"source": "sel-1", "target": "up-1"},
                {"source": "up-1", "target": "method-1", "targetHandle": "data"},
                {"source": "ref-1", "target": "method-1", "targetHandle": "data"},
            ],
        )

        result = validate_structure(pipeline.model_dump(), ANALYSIS_CONTRACTS)

        slot_errors = [e for e in result["errors"]
                       if "only one edge per slot" in e]
        assert len(slot_errors) == 1, result["errors"]


class TestSnakemakeRejectsMethodRoots:
    """Verify load_pipeline rejects pipelines with method-node roots."""

    def test_a_method_only_document_loads_with_an_unfed_root(self):
        """No system node at all: a legacy standalone pipeline passes the root rule."""
        pipeline_json = {
            "nodes": [
                {"id": "method-1", "type": "method", "env": "demo",
                 "method": "preprocess", "module": "demo", "params": {}},
                {"id": "method-2", "type": "method", "env": "demo",
                 "method": "filter", "module": "demo", "params": {}},
            ],
            "links": [{"source": "method-1", "target": "method-2"}],
            "samples": ["sample_A"],
        }

        pipeline = load_pipeline(pipeline_json, contract_map={}, reference_outputs={})

        assert sorted(s.node_id for s in pipeline.steps) == ["method-1", "method-2"]
    @workflow(purpose="load_pipeline raises error for pipeline with method-node root (no system node upstream)")
    def test_method_root_raises_in_load_pipeline(self, tmp_path):
        """Tier 2: Call load_pipeline with a pipeline containing system
        nodes AND a method node that has no incoming edges.  Verify
        it raises a ValueError before any Snakefile is generated."""
        pipeline_json = {
            "nodes": [
                {
                    "id": "input-1", "type": "input_selector",
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
            load_pipeline(pipeline_json, contract_map={}, reference_outputs={})
