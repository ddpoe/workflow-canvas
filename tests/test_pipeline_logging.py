"""
Integration tests for ADR 004: Pipeline Execution Logging.

Tests cover:
  - Run model error fields (US-3)
  - Pipeline ID generation and log directory creation (US-5)
  - Generated Snakefile logger setup (US-1)
  - Generated rules with per-run log handlers (US-2)
  - Generated rules with try/except error capture (US-3)
  - Generated Snakefile summary blocks (US-4)
  - Bidirectional links in generated code (US-2)
  - fail_pipeline safety-net preserves existing error fields (US-3)
"""

import json

import pytest
from sqlmodel import Session, select

from wfc.models import Run
from wfc.snakemake_gen import StepDef, PipelineDef, generate_snakefile


# =============================================================================
# Test 1: Run model error fields (US-3)
# =============================================================================

class TestRunModelErrorFields:
    """The Run model should have nullable error_message and error_traceback fields."""

    def test_run_has_error_fields(self, tmp_project):
        """Run model should accept error_message and error_traceback."""
        from wfc.database import get_session
        from wfc.models import Method, Module

        with get_session() as session:
            mod = Module(name="test_mod")
            session.add(mod)
            session.flush()
            meth = Method(name="test_meth", module_id=mod.id, script_path="run.py")
            session.add(meth)
            session.flush()

            run = Run(
                method_id=meth.id,
                sample="s1",
                status="failed",
                error_message="something broke",
                error_traceback="Traceback (most recent call last):\n  File ...",
            )
            session.add(run)
            session.commit()
            session.refresh(run)

            assert run.error_message == "something broke"
            assert run.error_traceback.startswith("Traceback")

    def test_run_error_fields_default_to_none(self, tmp_project):
        """Error fields should be None by default for successful runs."""
        from wfc.database import get_session
        from wfc.models import Method, Module

        with get_session() as session:
            mod = Module(name="test_mod2")
            session.add(mod)
            session.flush()
            meth = Method(name="test_meth2", module_id=mod.id, script_path="run.py")
            session.add(meth)
            session.flush()

            run = Run(method_id=meth.id, sample="s1", status="completed")
            session.add(run)
            session.commit()
            session.refresh(run)

            assert run.error_message is None
            assert run.error_traceback is None


# =============================================================================
# Test 2: Pipeline ID and log directory creation (US-5)
# =============================================================================

class TestPipelineIdRelocation:
    """Pipeline ID should be generated in generate_snakefile and passed as param."""

    def test_snakefile_uses_passed_pipeline_id(self, wfc_root):
        """When pipeline_id is passed, the Snakefile should use it instead of uuid4."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={"normalize": True},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-pipeline-123")

        assert 'PIPELINE_ID = "test-pipeline-123"' in snakefile
        # Should NOT contain uuid4 generation
        assert "PIPELINE_ID = str(uuid.uuid4())" not in snakefile

    def test_snakefile_without_pipeline_id_falls_back(self, wfc_root):
        """When no pipeline_id is passed, the Snakefile should still have a PIPELINE_ID."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={"normalize": True},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root)

        # Should fall back to uuid generation
        assert "PIPELINE_ID" in snakefile


# =============================================================================
# Test 3: Generated Snakefile contains logger setup (US-1)
# =============================================================================

class TestPipelineLoggerSetup:
    """The generated Snakefile should set up a Python logger."""

    def test_snakefile_contains_logging_import(self, wfc_root):
        """Generated Snakefile should import the logging module."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-123")

        # logging is imported as part of the combined import statement
        assert "logging" in snakefile

    def test_snakefile_contains_file_handler(self, wfc_root):
        """Generated Snakefile should configure a FileHandler for pipeline.log."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-123")

        assert "pipeline.log" in snakefile
        assert "FileHandler" in snakefile
        assert "StreamHandler" in snakefile


# =============================================================================
# Test 4: Per-run log handlers and bidirectional links (US-2)
# =============================================================================

class TestPerRunLogHandlers:
    """ADR 008: Per-run logging is now handled by wfc run-step, not the Snakefile.
    Rules use shell directives that delegate to run-step, which manages its own
    per-run log handlers internally. The Snakefile retains pipeline-level logging."""

    def test_rule_delegates_to_run_step(self, wfc_root):
        """Each generated rule delegates to wfc run-step via shell directive."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-123")

        # Rules use shell directives delegating to run-step
        rule_section = snakefile.split("rule preprocess:")[1].split("\nrule ")[0]
        assert "shell:" in rule_section
        assert "run-step" in rule_section
        # No inline run: blocks with per-run log handlers
        assert "run:" not in rule_section

    def test_no_inline_python_in_rules(self, wfc_root):
        """Rules have no inline Python — no finally blocks or handler cleanup."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-123")

        # No inline Python execution patterns in rules
        rule_section = snakefile.split("rule preprocess:")[1].split("\nrule ")[0]
        assert "finally:" not in rule_section
        assert "removeHandler" not in rule_section

    def test_pipeline_log_still_configured(self, wfc_root):
        """Pipeline-level logging is still configured in the Snakefile preamble."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-123")

        # Pipeline-level logging still present
        assert "pipeline.log" in snakefile
        assert "_pipeline_logger" in snakefile


# =============================================================================
# Test 5: Error capture in generated rules (US-3)
# =============================================================================

class TestErrorCapture:
    """ADR 008: Error capture is now handled by wfc run-step, not the Snakefile.
    Rules use shell directives — try/except and traceback capture are internal
    to the run-step command."""

    def test_rule_uses_shell_not_try_except(self, wfc_root):
        """Generated rule uses shell directive — no try/except in Snakefile rules."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-123")

        # Rules use shell directives, not inline Python
        rule_section = snakefile.split("rule preprocess:")[1].split("\nrule ")[0]
        # Stop at onsuccess/onerror handlers (which are not part of rules)
        rule_section = rule_section.split("\nonsuccess:")[0].split("\nonerror:")[0]
        assert "shell:" in rule_section
        assert "try:" not in rule_section
        assert "except Exception" not in rule_section

    def test_complete_run_accepts_error_args(self, tmp_project, cli):
        """complete_run CLI should accept --error and --traceback args."""
        # Register a module, method, and run first
        cli("register-module", "--name", "test_mod",
            "--contracts", "[]")
        cli("register-method", "methods/transform",
            "--module", "test_mod", "--name", "test_meth",
            "--script", "transform.py")
        result = cli("register_run", "--method", "test_meth",
                      "--module", "test_mod", "--sample", "s1")
        run_id = result.stdout.strip()

        # Complete with error
        result = cli("complete_run", "--run-id", run_id,
                      "--status", "failed",
                      "--error", "something went wrong",
                      "--traceback", "Traceback line 1\nline 2")
        assert result.returncode == 0

        # Verify the error fields are stored
        from wfc.database import get_session
        with get_session() as session:
            run = session.get(Run, int(run_id))
            assert run.error_message == "something went wrong"
            assert run.error_traceback == "Traceback line 1\nline 2"


# =============================================================================
# Test 6: Summary blocks (US-4)
# =============================================================================

class TestSummaryBlocks:
    """Generated Snakefile should have summary computation in onsuccess/onerror."""

    def test_onsuccess_has_summary(self, wfc_root):
        """onsuccess block should compute and write summary."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-123")

        assert "onsuccess:" in snakefile
        # Summary should mention pass/fail/cache counts
        assert "summary" in snakefile.lower() or "SUMMARY" in snakefile

    def test_onerror_has_summary(self, wfc_root):
        """onerror block should compute and write summary."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
            )],
            samples=["Pa16c"],
        )
        snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-123")

        assert "onerror:" in snakefile
        assert "fail_pipeline" in snakefile


# =============================================================================
# Test 7: fail_pipeline preserves existing error fields (US-3)
# =============================================================================

class TestFailPipelineSafetyNet:
    """fail_pipeline should not overwrite existing error fields."""

    def test_fail_pipeline_preserves_error_fields(self, tmp_project, cli):
        """If a run already has error fields set, fail_pipeline should not overwrite."""
        from wfc.database import get_session
        from wfc.models import Method, Module

        with get_session() as session:
            mod = Module(name="test_mod3")
            session.add(mod)
            session.flush()
            meth = Method(name="test_meth3", module_id=mod.id, script_path="run.py")
            session.add(meth)
            session.flush()

            # Create a run that already has error info but is still "running"
            run = Run(
                method_id=meth.id,
                sample="s1",
                status="running",
                pipeline_id="pipe-123",
                error_message="original error",
                error_traceback="original traceback",
            )
            session.add(run)
            session.commit()
            run_id = run.id

        # fail_pipeline should mark as failed but preserve error fields
        result = cli("fail_pipeline", "--pipeline-id", "pipe-123")
        assert result.returncode == 0

        with get_session() as session:
            run = session.get(Run, run_id)
            assert run.status == "failed"
            assert run.error_message == "original error"
            assert run.error_traceback == "original traceback"
