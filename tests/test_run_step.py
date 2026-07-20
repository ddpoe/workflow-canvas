"""
Tests for ADR 008: run-step execution layer.

Covers:
  - Single results channel (run-step reads _wfc_results.json)
  - run-step command (success, cache hit, error capture, inline fallback)
  - pipeline-summary aggregation
  - Simplified _generate_rule output
  - nextflow_gen removal
"""

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from tests.conftest import requires_docker

from axiom_annotations import workflow, Step


# =============================================================================
# Single results channel (ADR-020) — metrics + outputs flow through
# `_wfc_results.json`; the legacy `metrics.json` read is gone.
# =============================================================================

class TestSingleResultsChannel:
    """`run_step` collects results solely from `_wfc_results.json`.

    The client-side `RunContext` surface (env parsing, `save_artifact`,
    `log_metric`, `_finalize`) is covered by `wfc_client/tests/`. These
    tests pin the *host* contract: metrics arrive through the manifest and
    no `metrics.json` is read (so metrics cannot double-count).
    """

    def test_manifest_is_the_metrics_channel(self, tmp_path):
        """Host reads metrics from `_wfc_results.json`, not `metrics.json`."""
        from wfc.manifest import read_results_manifest

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        # A stray legacy metrics.json must be ignored entirely.
        (run_dir / "metrics.json").write_text(json.dumps({"n_cells": 999}))
        (run_dir / "_wfc_results.json").write_text(
            json.dumps({"outputs": {}, "metrics": {"n_cells": 42}})
        )

        result = read_results_manifest(run_dir)
        assert result is not None
        assert result.metrics == {"n_cells": 42}, \
            "metrics must come from _wfc_results.json, not metrics.json"

    def test_no_manifest_yields_no_metrics(self, tmp_path):
        """A pure outputs-only Tier-2 run (no manifest) yields no metrics."""
        from wfc.manifest import read_results_manifest

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "output.parquet").write_text("fake")
        # No _wfc_results.json — outputs are located by the run_dir scan,
        # metrics default to empty. There is no metrics.json fallback.
        assert read_results_manifest(run_dir) is None


# =============================================================================
# Helpers for run-step tests
# =============================================================================

def _seed_module_method(cli, module="test_mod", method="test_method",
                        image_digest=None):
    """Register a module and method via CLI so pre_run can reference them.

    Args:
        cli: In-process CLI runner fixture.
        module: Module name to register.
        method: Method name to register.
        image_digest: Optional bare sha256 hex of a real, locally-built
            container image (from the ``fixture_container_image`` session
            fixture). When provided, the method's env is the direct
            digest-pinned escape hatch
            ``container:docker://local/wfc-test-minimal@sha256:<digest>`` — the
            ONE env form ``run_step`` resolves for an inline-args (no
            ``--pipeline-json``) invocation, so a subsequent ``run-step``
            genuinely dispatches ``docker run`` against a runnable image (the
            integration-tier path). When omitted, the method uses the same
            escape-hatch shape but a fake ``local/x`` digest which
            ``_resolve_env`` validates by SHAPE only (no manifest lookup, no
            Docker) — for non-execution registration/error-path unit tests.

    Returns:
        The relative path to the registered method script.
    """
    import subprocess
    result = cli("register-module", "--name", module, "--description", "test module", "--contracts", "[]")
    assert result.returncode == 0, result.stderr
    method_dir = os.path.join("methods", method)
    os.makedirs(method_dir, exist_ok=True)
    script_name = f"{method}.py"
    script_path = os.path.join(method_dir, script_name)
    if not os.path.exists(script_path):
        with open(script_path, "w") as f:
            f.write("def main(df, params): return df\n")
    # ADR-019 Cycle H: a method must name a built container env. These tests
    # invoke run-step with INLINE args (no --pipeline-json), so the only env
    # form run_step resolves is the per-method direct digest-pinned escape
    # hatch (container:docker://...@sha256:...). For the integration tier we
    # substitute the REAL session-built image digest so docker run launches a
    # runnable image; for unit tests a fake local/x digest validates by shape.
    digest = image_digest if image_digest is not None else "a" * 64
    repo = "local/wfc-test-minimal" if image_digest is not None else "local/x"
    env_field = f"container:docker://{repo}@sha256:{digest}"
    yaml_path = os.path.join(method_dir, "method.yaml")
    if not os.path.exists(yaml_path):
        with open(yaml_path, "w") as f:
            f.write(
                "inputs:\n  data:\n    type: .csv\n"
                "outputs:\n  output:\n    type: .parquet\n"
                "params: {}\nexecutor: python\n"
                f"env: {env_field}\n"
            )
    result = cli("register-method", method_dir, "--module", module)
    assert result.returncode == 0, result.stderr
    # Commit so git is clean for pre_run's dirty check
    subprocess.run(["git", "add", "-A"], capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], capture_output=True)
    return script_path


def _make_method_script(tmp_path, name="test_method", content=None):
    """Create a simple method script that writes the `_wfc_results.json` manifest."""
    if content is None:
        content = textwrap.dedent("""\
            import json, os
            from pathlib import Path
            run_dir = Path(os.environ["WFC_RUN_DIR"])
            # Write a simple output file
            out = run_dir / "output.parquet"
            out.write_text("fake parquet data")
            # Write the single results channel (ADR-020): outputs + metrics.
            results = {"outputs": {}, "metrics": {"n_cells": 42}}
            (run_dir / "_wfc_results.json").write_text(json.dumps(results))
        """)
    script_path = tmp_path / "methods" / name / f"{name}.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(content)
    return str(script_path)


# =============================================================================
# run-step tests (Task 2 -- US-1, US-5)
# =============================================================================

class TestRunStep:
    """Tests for the wfc run-step command."""

    @pytest.mark.integration
    @requires_docker
    def test_run_step_inline_success(self, cli, tmp_project, fixture_container_image):
        """run-step with inline args executes a method and exits 0."""
        _seed_module_method(cli, image_digest=fixture_container_image)

        # Create a method script that writes _wfc_results.json
        script = _make_method_script(tmp_project)

        # Create a sample input file for --ref-input
        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(tmp_project / "methods" / "test_method" / "test_method.py"),
            "--pipeline-id", "test-pipeline-001",
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode == 0, f"run-step failed: {result.stderr}"

    def test_run_step_inline_missing_args(self, cli, tmp_project):
        """run-step without --pipeline-json requires --method, --module, --script."""
        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
        )
        assert result.returncode != 0, "Should fail when inline args are missing"

    def test_run_step_error_capture(self, cli, tmp_project):
        """run-step captures errors and exits non-zero when method fails."""
        _seed_module_method(cli)

        # Create a failing method script
        fail_script = _make_method_script(
            tmp_project,
            content=textwrap.dedent("""\
                raise RuntimeError("intentional test failure")
            """),
        )

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(tmp_project / "methods" / "test_method" / "test_method.py"),
            "--pipeline-id", "test-pipeline-002",
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode != 0, "Should fail when method raises"

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows file locking prevents hardlink on open file")
    def test_run_step_cache_hit(self, cli, tmp_project):
        """run-step detects cache hit and restores output without re-running."""
        _seed_module_method(cli)

        script = _make_method_script(tmp_project)

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        # First run creates the cache entry
        r1 = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(tmp_project / "methods" / "test_method" / "test_method.py"),
            "--pipeline-id", "test-pipeline-003",
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert r1.returncode == 0, f"First run failed: {r1.stderr}"

        # Second run should hit cache
        r2 = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(tmp_project / "methods" / "test_method" / "test_method.py"),
            "--pipeline-id", "test-pipeline-004",
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert r2.returncode == 0, f"Cache hit run failed: {r2.stderr}"

    def test_run_step_unresolvable_parent_fails_loudly(self, cli, tmp_project):
        """A non-root step whose parent slot resolves to nothing exits 1 at
        wiring time with a clear error — never a silent empty slot_paths."""
        _seed_module_method(cli)
        _make_method_script(tmp_project)

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(tmp_project / "methods" / "test_method" / "test_method.py"),
            "--pipeline-id", "test-pipeline-006",
            "--git-commit", "abc123",
            "--parent-run-id", "data:999999",
        )
        assert result.returncode != 0, "run-step must fail on an unresolvable parent"
        assert "could not resolve input" in result.stderr

    @pytest.mark.integration
    @requires_docker
    def test_run_step_reads_results_manifest(self, cli, tmp_project, fixture_container_image):
        """run-step reads metrics from `_wfc_results.json` and passes to complete_run."""
        _seed_module_method(cli, image_digest=fixture_container_image)

        script = _make_method_script(tmp_project)

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(tmp_project / "methods" / "test_method" / "test_method.py"),
            "--pipeline-id", "test-pipeline-005",
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode == 0, f"run-step failed: {result.stderr}"

        # Verify the metrics were stored in the run
        from wfc.database import get_session
        from wfc.models import Run
        from sqlmodel import select
        with get_session() as session:
            runs = session.exec(
                select(Run).where(Run.status == "completed")
            ).all()
            # At least one completed run with metrics
            completed_with_metrics = [r for r in runs if r.metrics and r.metrics.get("n_cells") == 42]
            assert len(completed_with_metrics) >= 1, \
                f"Expected a run with n_cells=42 in metrics, got: {[r.metrics for r in runs]}"

    @pytest.mark.integration
    @requires_docker
    def test_run_step_tees_stdout_to_per_run_log(self, cli, tmp_project, fixture_container_image):
        """run-step writes the method's stdout to .runs/<run_id>/stdout.log."""
        _seed_module_method(cli, image_digest=fixture_container_image)

        _make_method_script(
            tmp_project,
            content=textwrap.dedent("""\
                import json, os
                from pathlib import Path
                print("hello from stdout line 1")
                print("hello from stdout line 2")
                run_dir = Path(os.environ["WFC_RUN_DIR"])
                (run_dir / "output.parquet").write_text("fake")
                (run_dir / "_wfc_results.json").write_text(json.dumps({"outputs": {}, "metrics": {"n_cells": 1}}))
            """),
        )

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(tmp_project / "methods" / "test_method" / "test_method.py"),
            "--pipeline-id", "stdout-log-test",
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode == 0, f"run-step failed: {result.stderr}"

        from wfc.database import get_session
        from wfc.models import Run
        from sqlmodel import select
        with get_session() as session:
            run = session.exec(
                select(Run).where(Run.pipeline_id == "stdout-log-test")
            ).first()
            assert run is not None
            run_id = run.id

        stdout_log = tmp_project / ".runs" / f"{run_id:08d}" / "stdout.log"
        assert stdout_log.exists(), f"stdout.log missing at {stdout_log}"
        content = stdout_log.read_text(encoding="utf-8")
        assert "hello from stdout line 1" in content
        assert "hello from stdout line 2" in content

    @pytest.mark.integration
    @requires_docker
    def test_run_step_tees_stderr_across_crash(self, cli, tmp_project, fixture_container_image):
        """run-step captures stderr up to and including the crash traceback."""
        _seed_module_method(cli, image_digest=fixture_container_image)

        _make_method_script(
            tmp_project,
            content=textwrap.dedent("""\
                import sys
                print("about to crash", file=sys.stderr)
                raise RuntimeError("boom")
            """),
        )

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(tmp_project / "methods" / "test_method" / "test_method.py"),
            "--pipeline-id", "stderr-log-test",
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode != 0

        from wfc.database import get_session
        from wfc.models import Run
        from sqlmodel import select
        with get_session() as session:
            run = session.exec(
                select(Run).where(Run.pipeline_id == "stderr-log-test")
            ).first()
            assert run is not None
            run_id = run.id

        stderr_log = tmp_project / ".runs" / f"{run_id:08d}" / "stderr.log"
        assert stderr_log.exists(), f"stderr.log missing at {stderr_log}"
        content = stderr_log.read_text(encoding="utf-8")
        assert "about to crash" in content
        assert "RuntimeError" in content and "boom" in content


@workflow(
    purpose="Root method node without --ref-input fails with error naming the missing flag",
)
def test_root_node_without_ref_input_raises_error(cli, tmp_project):
    """Tier 3: Run a root method node without --ref-input, verify run_step
    raises an error with a message naming the --ref-input flag."""

    s = Step(step_num=1, name="Register a method",
             purpose="Set up a method so run-step can find it")
    _seed_module_method(cli)
    _make_method_script(tmp_project)

    s = Step(step_num=2, name="Run root node without --ref-input",
             purpose="Call run-step on a standalone root node with no input source")
    result = cli(
        "run-step",
        "--node-id", "test_method",
        "--sample", "S1",
        "--variant", "default",
        "--method", "test_method",
        "--module", "test_mod",
        "--script", str(tmp_project / "methods" / "test_method" / "test_method.py"),
        "--pipeline-id", "root-no-ref-input-001",
        "--git-commit", "abc123",
    )

    s = Step(step_num=3, name="Verify error mentions --ref-input",
             purpose="Confirm the error message names the --ref-input flag")
    assert result.returncode != 0, (
        "Expected non-zero exit code for root node without --ref-input"
    )
    combined = result.stderr + result.stdout
    assert "--ref-input" in combined, (
        f"Error message should name the --ref-input flag: {combined!r}"
    )


# =============================================================================
# ADR-010 slot-aware workspace output path tests
# =============================================================================

def _write_pipeline_json(
    tmp_project,
    node_id: str,
    method: str,
    module: str,
    script_path: str,
    slot_outputs: dict,
    slot_types: dict,
):
    """Write a minimal pipeline JSON with one node carrying slot metadata."""
    pj = {
        "nodes": [{
            "id": node_id,
            "method": method,
            "module": module,
            "script": script_path,
            "params": {},
            "slot_outputs": slot_outputs,
            "slot_types": slot_types,
            "env": "container:demo",
        }],
        "links": [],
        "samples": ["S1"],
    }
    pj_path = tmp_project / "pipeline.json"
    pj_path.write_text(json.dumps(pj))
    return pj_path


class TestADR010SlotShapes:
    """End-to-end tests for ADR-010 — run-step honors contract-declared
    output slots as the single source of truth for workspace paths."""

    @pytest.mark.integration
    @requires_docker
    def test_non_parquet_file_slot_published(self, cli, tmp_project, fixture_container_image):
        """US-1: a method with a single .json slot runs end-to-end and
        the workspace contains the file under the slot-declared name."""
        _seed_module_method(cli, image_digest=fixture_container_image)

        script_content = textwrap.dedent("""\
            import json, os
            from pathlib import Path
            run_dir = Path(os.environ["WFC_RUN_DIR"])
            (run_dir / "extraction_config.json").write_text('{"k": 1}')
            (run_dir / "_wfc_results.json").write_text(json.dumps({"outputs": {}, "metrics": {"ok": True}}))
        """)
        script = _make_method_script(tmp_project, content=script_content)

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        pj = _write_pipeline_json(
            tmp_project,
            node_id="test_method",
            method="test_method",
            module="test_mod",
            script_path=str(tmp_project / "methods" / "test_method" / "test_method.py"),
            slot_outputs={"config": "extraction_config.json"},
            slot_types={"config": "JSON"},
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--pipeline-id", "adr010-file-slot-001",
            "--pipeline-json", str(pj),
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode == 0, f"run-step failed: {result.stderr}"

        # ADR-018: workspace is gone; assert sentinel + run-archive file instead.
        sentinel = (
            tmp_project / ".runs" / "sentinels" / "adr010-file-slot-001"
            / "test_method" / "S1" / "default" / ".complete"
        )
        assert sentinel.exists(), f"sentinel missing: {sentinel}. stderr={result.stderr}"
        # Source-of-truth file lives in the run-archive dir.
        from wfc.database import get_session
        from wfc.models import Run, RunOutput
        from sqlmodel import select
        with get_session() as session:
            ro = session.exec(select(RunOutput).where(RunOutput.output_name == "extraction_config.json")).first()
            assert ro is not None and Path(ro.artifact_path).read_text() == '{"k": 1}'

    @pytest.mark.integration
    @requires_docker
    def test_directory_slot_published(self, cli, tmp_project, fixture_container_image):
        """US-2: a directory slot — every child in run_dir/<slot> lands
        in the workspace under the slot-declared name."""
        _seed_module_method(cli, image_digest=fixture_container_image)

        script_content = textwrap.dedent("""\
            import json, os
            from pathlib import Path
            run_dir = Path(os.environ["WFC_RUN_DIR"])
            tiles = run_dir / "tiles_dir"
            tiles.mkdir()
            (tiles / "tile_000.png").write_text("img0")
            (tiles / "tile_001.png").write_text("img1")
            (run_dir / "_wfc_results.json").write_text(json.dumps({"outputs": {}, "metrics": {"n_tiles": 2}}))
        """)
        script = _make_method_script(tmp_project, content=script_content)

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        pj = _write_pipeline_json(
            tmp_project,
            node_id="test_method",
            method="test_method",
            module="test_mod",
            script_path=str(tmp_project / "methods" / "test_method" / "test_method.py"),
            slot_outputs={"tiles_dir": "tiles_dir"},
            slot_types={"tiles_dir": "directory"},
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--pipeline-id", "adr010-dir-slot-001",
            "--pipeline-json", str(pj),
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode == 0, f"run-step failed: {result.stderr}"

        # ADR-018: sentinel + run-archive dir replaces workspace publish.
        sentinel = (
            tmp_project / ".runs" / "sentinels" / "adr010-dir-slot-001"
            / "test_method" / "S1" / "default" / ".complete"
        )
        assert sentinel.exists(), f"sentinel missing: {sentinel}"
        from wfc.database import get_session
        from wfc.models import RunOutput
        from sqlmodel import select
        with get_session() as session:
            ro = session.exec(select(RunOutput).where(RunOutput.output_name == "tiles_dir")).first()
            assert ro is not None
            ws_dir = Path(ro.artifact_path)
            assert ws_dir.exists() and ws_dir.is_dir(), f"archive dir missing: {ws_dir}"
            assert (ws_dir / "tile_000.png").exists()
            assert (ws_dir / "tile_001.png").exists()

    @pytest.mark.integration
    @requires_docker
    def test_multi_slot_mixed_file_and_dir(self, cli, tmp_project, fixture_container_image):
        """US-3: a multi-slot method (file + directory) publishes every
        declared slot to the workspace under the declared name."""
        _seed_module_method(cli, image_digest=fixture_container_image)

        script_content = textwrap.dedent("""\
            import json, os
            from pathlib import Path
            run_dir = Path(os.environ["WFC_RUN_DIR"])
            (run_dir / "extraction_config.json").write_text('{"version": 2}')
            tiles = run_dir / "tiles_dir"
            tiles.mkdir()
            (tiles / "tile_000.png").write_text("t0")
            (run_dir / "_wfc_results.json").write_text(json.dumps({"outputs": {}, "metrics": {"ok": True}}))
        """)
        script = _make_method_script(tmp_project, content=script_content)

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        pj = _write_pipeline_json(
            tmp_project,
            node_id="test_method",
            method="test_method",
            module="test_mod",
            script_path=str(tmp_project / "methods" / "test_method" / "test_method.py"),
            slot_outputs={
                "config": "extraction_config.json",
                "tiles_dir": "tiles_dir",
            },
            slot_types={"config": "JSON", "tiles_dir": "directory"},
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--pipeline-id", "adr010-multi-slot-001",
            "--pipeline-json", str(pj),
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode == 0, f"run-step failed: {result.stderr}"

        # ADR-018: assert sentinel + run-archive outputs (workspace is gone).
        sentinel = (
            tmp_project / ".runs" / "sentinels" / "adr010-multi-slot-001"
            / "test_method" / "S1" / "default" / ".complete"
        )
        assert sentinel.exists(), f"sentinel missing: {sentinel}"
        from wfc.database import get_session
        from wfc.models import RunOutput
        from sqlmodel import select
        with get_session() as session:
            cfg_ro = session.exec(select(RunOutput).where(RunOutput.output_name == "extraction_config.json")).first()
            tiles_ro = session.exec(select(RunOutput).where(RunOutput.output_name == "tiles_dir")).first()
            assert cfg_ro is not None and Path(cfg_ro.artifact_path).read_text() == '{"version": 2}'
            assert tiles_ro is not None and (Path(tiles_ro.artifact_path) / "tile_000.png").exists()

    @pytest.mark.integration
    @requires_docker
    def test_missing_slot_raises(self, cli, tmp_project, fixture_container_image):
        """Negative test: a method that declares a slot but fails to
        produce it fails the run with a clear RuntimeError-style message
        naming the method and slot."""
        _seed_module_method(cli, image_digest=fixture_container_image)

        # Method produces extraction_config.json but NOT the declared
        # "labels" slot — should fail with a clear error.
        script_content = textwrap.dedent("""\
            import json, os
            from pathlib import Path
            run_dir = Path(os.environ["WFC_RUN_DIR"])
            (run_dir / "extraction_config.json").write_text('{}')
            (run_dir / "_wfc_results.json").write_text('{"outputs": {}, "metrics": {}}')
        """)
        script = _make_method_script(tmp_project, content=script_content)

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        pj = _write_pipeline_json(
            tmp_project,
            node_id="test_method",
            method="test_method",
            module="test_mod",
            script_path=str(tmp_project / "methods" / "test_method" / "test_method.py"),
            slot_outputs={
                "config": "extraction_config.json",
                "labels": "labels.csv",
            },
            slot_types={"config": "JSON", "labels": "CSV"},
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--pipeline-id", "adr010-missing-slot-001",
            "--pipeline-json", str(pj),
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode != 0, \
            f"Expected failure for missing slot, got rc=0.\nstderr={result.stderr}"
        # Error message should name the method and the missing slot
        combined = result.stderr + result.stdout
        assert "labels" in combined, \
            f"Error message should name the missing slot 'labels': {combined!r}"
        assert "test_method" in combined, \
            f"Error message should name the method 'test_method': {combined!r}"

    @pytest.mark.integration
    @requires_docker
    def test_cached_branch_restores_every_slot(self, cli, tmp_project, fixture_container_image):
        """US-4: re-running the same multi-slot method takes the CACHED
        branch and restores every declared slot byte-identical."""
        _seed_module_method(cli, image_digest=fixture_container_image)

        script_content = textwrap.dedent("""\
            import json, os
            from pathlib import Path
            run_dir = Path(os.environ["WFC_RUN_DIR"])
            (run_dir / "extraction_config.json").write_text('{"v": 7}')
            (run_dir / "labels.csv").write_text("id,label\\n1,A\\n")
            (run_dir / "_wfc_results.json").write_text('{"outputs": {}, "metrics": {}}')
        """)
        script = _make_method_script(tmp_project, content=script_content)

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        pj = _write_pipeline_json(
            tmp_project,
            node_id="test_method",
            method="test_method",
            module="test_mod",
            script_path=str(tmp_project / "methods" / "test_method" / "test_method.py"),
            slot_outputs={
                "config": "extraction_config.json",
                "labels": "labels.csv",
            },
            slot_types={"config": "JSON", "labels": "CSV"},
        )

        # Fresh run
        r1 = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--pipeline-id", "adr010-cached-001",
            "--pipeline-json", str(pj),
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert r1.returncode == 0, f"First run failed: {r1.stderr}"

        # Second run: should be CACHED and restore both slots
        r2 = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--pipeline-id", "adr010-cached-002",
            "--pipeline-json", str(pj),
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert r2.returncode == 0, f"Cache-hit run failed: {r2.stderr}"

        # ADR-018: cache-hit branch touches the sentinel; outputs live in
        # the source run's archive dir (and the DVC cache once archived).
        sentinel2 = (
            tmp_project / ".runs" / "sentinels" / "adr010-cached-002"
            / "test_method" / "S1" / "default" / ".complete"
        )
        assert sentinel2.exists(), f"cache-hit sentinel missing: {sentinel2}"
        # The source run's outputs (from r1) are still on disk; cache-hit
        # just signals Snakemake — no workspace publish needed.
        from wfc.database import get_session
        from wfc.models import RunOutput
        from sqlmodel import select
        with get_session() as session:
            cfg_ro = session.exec(select(RunOutput).where(RunOutput.output_name == "extraction_config.json")).first()
            labels_ro = session.exec(select(RunOutput).where(RunOutput.output_name == "labels.csv")).first()
            assert cfg_ro is not None and Path(cfg_ro.artifact_path).read_text() == '{"v": 7}'
            assert labels_ro is not None and Path(labels_ro.artifact_path).read_text() == "id,label\n1,A\n"


class TestCanonicalWorkdirOutput:
    """ADR-020 canonical ``ctx.workdir`` -> ``ctx.save_artifact`` round-trip.

    The recommended Tier-1 pattern writes a declared output UNDER
    ``WFC_RUN_DIR/_workdir/`` and records its run-dir-relative path in
    ``_wfc_results.json``. The whole reason the manifest matters over a plain
    ``run_dir`` scan is this branch: a file nested in ``_workdir/`` is invisible
    to a top-level scan, so the host MUST resolve the manifest-recorded path to
    archive it.

    This drives the REAL ``run_step`` collect-outputs path host-side (no
    Docker): ``_run_method_subprocess`` is monkeypatched to simulate the
    container — it writes ``_workdir/clean.csv`` plus the manifest recording
    ``clean: _workdir/clean.csv`` (save_artifact name == declared slot name).
    """

    def test_run_step_archives_workdir_nested_output_from_manifest(
        self, cli, tmp_project, monkeypatch
    ):
        """The declared output lives under ``_workdir/``; the host resolves it
        from ``_wfc_results.json`` into a ``RunOutput`` row, and
        ``archive_outputs`` hashes + DVC-caches THAT nested file.

        Fails under a bare ``run_dir`` scan: ``run_dir/clean.csv`` never
        exists, so the missing-slot guard would abort the run before any
        ``RunOutput`` row is written.
        """
        import hashlib

        # Shape-validated escape-hatch env (no real image; subprocess is mocked).
        _seed_module_method(cli)
        _make_method_script(tmp_project)

        src_file = tmp_project / "_src_input.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        # The single declared slot's name ("clean") == the save_artifact name
        # the method records in the manifest. The file is nested in _workdir/.
        clean_bytes = b"id,value\n1,10\n2,20\n"

        def _fake_subprocess(cmd, *, cwd, env, stdout_log, stderr_log):
            """Stand in for the container: write the _workdir-nested output and
            the manifest recording its run-dir-relative path, then succeed."""
            import subprocess as _sp

            run_dir = Path(env["WFC_RUN_DIR"])
            workdir = run_dir / "_workdir"
            workdir.mkdir(parents=True, exist_ok=True)
            (workdir / "clean.csv").write_bytes(clean_bytes)
            results = {
                "outputs": {"clean": "_workdir/clean.csv"},
                "metrics": {"kept_rows": 2},
            }
            (run_dir / "_wfc_results.json").write_text(json.dumps(results))
            stdout_log.parent.mkdir(parents=True, exist_ok=True)
            stdout_log.write_text("")
            stderr_log.write_text("")
            return _sp.CompletedProcess(args=cmd, returncode=0, stdout=None, stderr=None)

        monkeypatch.setattr("wfc.cli._run_method_subprocess", _fake_subprocess)

        pj = _write_pipeline_json(
            tmp_project,
            node_id="test_method",
            method="test_method",
            module="test_mod",
            script_path=str(tmp_project / "methods" / "test_method" / "test_method.py"),
            slot_outputs={"clean": "clean.csv"},
            slot_types={"clean": "CSV"},
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--pipeline-id", "adr020-workdir-001",
            "--pipeline-json", str(pj),
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert result.returncode == 0, (
            "run-step must succeed by resolving the _workdir-nested output from "
            f"the manifest (a bare run_dir scan would fail).\nstderr={result.stderr}"
            f"\nstdout={result.stdout}"
        )

        from wfc.database import get_session
        from wfc.models import Run, RunOutput
        from wfc.provenance import archive_outputs
        from sqlmodel import select

        # The RunOutput row must point at the _workdir-nested file — proving the
        # host consulted the manifest, not a top-level run_dir scan.
        with get_session() as session:
            run = session.exec(
                select(Run).where(Run.status == "completed")
            ).first()
            assert run is not None, "no completed Run row"
            run_id = run.id
            assert run.metrics == {"kept_rows": 2}, run.metrics
            ro = session.exec(
                select(RunOutput).where(RunOutput.run_id == run_id)
            ).first()
            assert ro is not None, "no RunOutput row written"
            artifact = Path(ro.artifact_path)
            assert artifact.parent.name == "_workdir", (
                f"RunOutput must point at the _workdir-nested file, got "
                f"{artifact} — a run_dir scan would have used a top-level path"
            )
            assert artifact.read_bytes() == clean_bytes
            assert ro.content_hash is None, "deferred archiving: hash is NULL pre-sweep"

        # The existing archive sweep hashes + DVC-caches the _workdir file.
        archive_outputs(project_dir=tmp_project, run_id=run_id)
        expected_hash = hashlib.md5(clean_bytes).hexdigest()
        with get_session() as session:
            ro = session.exec(
                select(RunOutput).where(RunOutput.run_id == run_id)
            ).first()
            assert ro.content_hash == expected_hash, (
                f"archive must hash the _workdir file bytes: "
                f"{ro.content_hash} != {expected_hash}"
            )
            cache_path = (
                tmp_project / ".dvc" / "cache" / "files" / "md5"
                / expected_hash[:2] / expected_hash[2:]
            )
            assert cache_path.exists(), (
                f"_workdir output not DVC-cached at {cache_path}"
            )


class TestParentLookupPipelineScoped:
    """Regression: parent run_id sidecar lookup must be pipeline-scoped.

    Since workspace outputs are written under
    ``.runs/workspace/<pipeline_id>/<node_id>/<sample>/<variant>/``, the
    sidecar lookup that resolves a downstream node's parent ``run_id`` must
    include the same ``pipeline_id``. Without that scoping, the lookup reads
    from a legacy un-scoped path and either finds nothing (downstream gets
    treated as root) or finds a stale sidecar from a prior session — leaving
    ``WFC_INPUT_PATHS`` pointing at the wrong file or empty.
    """

    @pytest.mark.integration
    @requires_docker
    def test_downstream_receives_upstream_output(self, cli, tmp_project, fixture_container_image):
        """A two-node pipeline linked by slot: after running node_1 and
        then node_2 under the same pipeline_id, node_2's _run_context.json
        must carry an input_path inside the pipeline-scoped workspace, and
        the method must be able to read node_1's actual output from it."""
        _seed_module_method(cli, module="upstream_mod", method="upstream", image_digest=fixture_container_image)
        _seed_module_method(cli, module="downstream_mod", method="downstream", image_digest=fixture_container_image)

        upstream_script = _make_method_script(
            tmp_project,
            name="upstream",
            content=textwrap.dedent("""\
                import os
                from pathlib import Path
                run_dir = Path(os.environ["WFC_RUN_DIR"])
                (run_dir / "data.json").write_text('{"upstream": "ok"}')
                (run_dir / "_wfc_results.json").write_text('{"outputs": {}, "metrics": {}}')
            """),
        )
        downstream_script = _make_method_script(
            tmp_project,
            name="downstream",
            content=textwrap.dedent("""\
                import json, os, sys
                from pathlib import Path
                run_dir = Path(os.environ["WFC_RUN_DIR"])
                slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
                data_paths = slot_paths.get("data", [])
                if not data_paths:
                    sys.stderr.write("WFC_INPUT_PATHS had no data slot\\n")
                    sys.exit(2)
                content = Path(data_paths[0]).read_text()
                (run_dir / "echo.json").write_text(content)
                (run_dir / "_wfc_results.json").write_text('{"outputs": {}, "metrics": {}}')
            """),
        )

        src_file = tmp_project / "_src.csv"
        src_file.write_text("col\n1\n")
        cli("register-sample", "--name", "S1", "--source", str(src_file))

        pipeline_id = "regression-parent-lookup-001"
        pj = {
            "nodes": [
                {
                    "id": "node_1",
                    "method": "upstream",
                    "module": "upstream_mod",
                    "script": upstream_script,
                    "params": {},
                    "slot_outputs": {"data": "data.json"},
                    "slot_types": {"data": "JSON"},
                    "env": "container:demo",
                },
                {
                    "id": "node_2",
                    "method": "downstream",
                    "module": "downstream_mod",
                    "script": downstream_script,
                    "params": {},
                    "slot_outputs": {"echo": "echo.json"},
                    "slot_types": {"echo": "JSON"},
                    "env": "container:demo",
                },
            ],
            "links": [
                {
                    "source": "node_1",
                    "target": "node_2",
                    "source_slot": "data",
                    "target_slot": "data",
                },
            ],
            "samples": ["S1"],
        }
        pj_path = tmp_project / "pipeline.json"
        pj_path.write_text(json.dumps(pj))

        r1 = cli(
            "run-step",
            "--node-id", "node_1",
            "--sample", "S1",
            "--variant", "default",
            "--pipeline-id", pipeline_id,
            "--pipeline-json", str(pj_path),
            "--git-commit", "abc123",
            "--ref-input", f"data={src_file}",
        )
        assert r1.returncode == 0, f"node_1 run-step failed: {r1.stderr}"

        r2 = cli(
            "run-step",
            "--node-id", "node_2",
            "--sample", "S1",
            "--variant", "default",
            "--pipeline-id", pipeline_id,
            "--pipeline-json", str(pj_path),
            "--git-commit", "abc123",
        )
        assert r2.returncode == 0, (
            "node_2 run-step failed — parent sidecar lookup probably isn't "
            f"pipeline-scoped so WFC_INPUT_PATHS was empty. stderr={r2.stderr}"
        )

        # Find the downstream run archive by scanning for its method_name.
        runs_root = tmp_project / ".runs"
        archives = sorted(p for p in runs_root.iterdir() if p.name.isdigit())
        downstream_run = None
        for arc in archives:
            ctx_file = arc / "_run_context.json"
            if ctx_file.exists():
                ctx = json.loads(ctx_file.read_text())
                if ctx.get("method_name") == "downstream":
                    downstream_run = arc
                    break
        assert downstream_run is not None, (
            f"no downstream run archive found under {runs_root}"
        )
        ctx = json.loads((downstream_run / "_run_context.json").read_text())
        assert ctx["slot_paths"], (
            f"downstream _run_context.json has empty slot_paths: {ctx}"
        )
        # resolve_input() returns the archived output path, not the
        # workspace hardlink — so we assert on filename, not full path.
        data_paths = ctx["slot_paths"].get("data", [])
        assert data_paths and data_paths[0].endswith("data.json"), (
            f"downstream slot_paths['data'] should point at upstream's data.json "
            f"(not the sample file); got: {ctx['slot_paths']}"
        )

        echo = (downstream_run / "echo.json").read_text()
        assert echo == '{"upstream": "ok"}', (
            f"downstream should have echoed upstream's data.json content; "
            f"got: {echo!r}"
        )


class TestADR010SnakemakeGen:
    """ADR-010/018: _generate_rule emits a single sentinel per rule.

    Pre-ADR-018, directory slots were emitted as ``slot=directory("...")``
    and file slots as ``slot="..."``.  Now both collapse to a uniform
    sentinel output — the data still lives in the run-staging dir / DVC
    cache, but the Snakefile doesn't reference it.
    """

    def test_directory_slot_emits_sentinel(self, wfc_root):
        """ADR-018: directory slots collapse to a sentinel; no directory(...) wrapper."""
        from wfc.snakemake_gen import generate_snakefile, PipelineDef, StepDef
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="tile_gen",
                module_name="test_mod",
                script_path="methods/tile_gen/tile_gen.py",
                params={},
                slot_outputs={"tiles_dir": "tiles_dir"},
                slot_types={"tiles_dir": "directory"},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root, project_root=wfc_root)
        rule_block = content.split("rule tile_gen:")[1].split("\nrule ")[0]
        assert "directory(" not in rule_block
        assert ".runs/sentinels/" in rule_block
        assert ".complete" in rule_block

    def test_file_slot_emits_sentinel(self, wfc_root):
        """ADR-018: file slots also collapse to the same sentinel shape."""
        from wfc.snakemake_gen import generate_snakefile, PipelineDef, StepDef
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="extractor",
                module_name="test_mod",
                script_path="methods/extractor/extractor.py",
                params={},
                slot_outputs={"config": "extraction_config.json"},
                slot_types={"config": "JSON"},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root, project_root=wfc_root)
        rule_block = content.split("rule extractor:")[1].split("\nrule ")[0]
        assert "directory(" not in rule_block
        # No per-slot named output identifiers in the rule output block
        # (the only `config=` we expect is gone; sentinel is unnamed)
        output_section = rule_block.split("output:")[1].split("params:")[0]
        assert "config=" not in output_section
        assert ".runs/sentinels/" in rule_block
        assert ".complete" in rule_block


# =============================================================================
# pipeline-summary tests (Task 3 -- US-3)
# =============================================================================

class TestPipelineSummary:
    """Tests for the wfc pipeline-summary command."""

    def test_pipeline_summary_aggregation(self, cli, tmp_project):
        """pipeline-summary reads outcome sidecars and prints summary."""
        # Create mock outcome sidecar files
        pipeline_id = "test-summary-001"
        outcomes_dir = tmp_project / ".runs" / "pipelines" / pipeline_id / "outcomes"
        outcomes_dir.mkdir(parents=True)

        outcomes = [
            {"node_id": "step_a", "sample": "S1", "variant": "default",
             "status": "completed", "run_id": 1, "elapsed": 2.5},
            {"node_id": "step_b", "sample": "S1", "variant": "default",
             "status": "cached", "run_id": 2, "elapsed": 0.1},
            {"node_id": "step_c", "sample": "S1", "variant": "default",
             "status": "failed", "run_id": 3, "elapsed": 1.0,
             "error": "something broke"},
        ]
        for i, outcome in enumerate(outcomes):
            (outcomes_dir / f"outcome_{i}.json").write_text(json.dumps(outcome))

        result = cli("pipeline-summary", "--pipeline-id", pipeline_id)
        assert result.returncode == 0, f"pipeline-summary failed: {result.stderr}"
        assert "Completed: 1" in result.stdout or "Passed: 1" in result.stdout
        assert "Cached: 1" in result.stdout
        assert "Failed: 1" in result.stdout

    def test_pipeline_summary_no_outcomes(self, cli, tmp_project):
        """pipeline-summary with no outcome files still works."""
        pipeline_id = "test-summary-empty"
        outcomes_dir = tmp_project / ".runs" / "pipelines" / pipeline_id / "outcomes"
        outcomes_dir.mkdir(parents=True)

        result = cli("pipeline-summary", "--pipeline-id", pipeline_id)
        assert result.returncode == 0, f"pipeline-summary failed: {result.stderr}"
        assert "Total: 0" in result.stdout or "Total runs: 0" in result.stdout


# =============================================================================
# Simplified _generate_rule tests (Task 4 -- US-2)
# =============================================================================

class TestGenerateRuleSimplified:
    """Tests for the simplified _generate_rule output."""

    def test_rule_uses_shell_not_run(self, wfc_root):
        """Generated rules use shell: directive, not run: blocks."""
        from wfc.snakemake_gen import generate_snakefile, PipelineDef, StepDef
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="test_mod",
                script_path="methods/preprocess/preprocess.py",
                params={"k": 1},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root, project_root=wfc_root)
        # Should contain shell: and NOT contain run:
        assert "shell:" in content, "Rule should use shell: directive"
        lines = content.split("\n")
        # Filter to just rule body lines
        in_rule = False
        for line in lines:
            if line.startswith("rule preprocess:"):
                in_rule = True
            elif in_rule and line and not line.startswith(" ") and not line.startswith("\t"):
                in_rule = False
            elif in_rule and line.strip() == "run:":
                pytest.fail("Rule should not contain 'run:' block")

    def test_preamble_no_helper_functions(self, wfc_root):
        """Generated Snakefile preamble should not contain any helper functions
        (run_method, write_run_context, read_parent_id, wfc_cmd).
        onsuccess/onerror use Snakemake's built-in shell() instead."""
        from wfc.snakemake_gen import generate_snakefile, PipelineDef, StepDef
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="test_mod",
                script_path="methods/preprocess/preprocess.py",
                params={"k": 1},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root, project_root=wfc_root)
        assert "def run_method(" not in content, "run_method helper should be removed"
        assert "def write_run_context(" not in content, "write_run_context helper should be removed"
        assert "def read_parent_id(" not in content, "read_parent_id helper should be removed"
        # onsuccess/onerror use shell() instead
        assert "shell(" in content, "onsuccess/onerror should use shell() calls"

    def test_preamble_has_pipeline_env_vars(self, wfc_root):
        """Generated preamble sets WFC_PIPELINE_JSON and WFC_PIPELINE_ID as env vars."""
        from wfc.snakemake_gen import generate_snakefile, PipelineDef, StepDef
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="test_mod",
                script_path="methods/preprocess/preprocess.py",
                params={"k": 1},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root, project_root=wfc_root)
        assert "WFC_PIPELINE_JSON" in content, "Should set WFC_PIPELINE_JSON env var"
        assert "WFC_PIPELINE_ID" in content, "Should set WFC_PIPELINE_ID env var"

    def test_shell_calls_run_step(self, wfc_root):
        """Shell directive calls wfc run-step with node-id, sample, variant."""
        from wfc.snakemake_gen import generate_snakefile, PipelineDef, StepDef
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="test_mod",
                script_path="methods/preprocess/preprocess.py",
                params={"k": 1},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root, project_root=wfc_root)
        assert "run-step" in content, "Shell should call wfc run-step"

    def test_no_brace_wildcards_in_shell(self, wfc_root):
        """Shell strings should not contain {CONSTANT} patterns that Snakemake would interpret as wildcards."""
        from wfc.snakemake_gen import generate_snakefile, PipelineDef, StepDef
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="test_mod",
                script_path="methods/preprocess/preprocess.py",
                params={"k": 1},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root, project_root=wfc_root)
        # Find shell: lines and check for forbidden patterns
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"') and "PIPELINE_ID" in stripped:
                # This is inside a shell string -- {PIPELINE_ID} is bad
                assert "{PIPELINE_ID}" not in stripped, \
                    f"Shell string contains {{PIPELINE_ID}} which Snakemake interprets as wildcard: {stripped}"


# =============================================================================
# nextflow_gen removal tests (Task 5 -- US-4)
# =============================================================================

class TestNextflowGenRemoval:
    """Tests confirming nextflow_gen.py is deleted."""

    def test_nextflow_gen_not_importable(self):
        """wfc.nextflow_gen should not be importable after deletion."""
        with pytest.raises(ImportError):
            import wfc.nextflow_gen  # noqa: F401

    def test_no_nextflow_imports_in_codebase(self):
        """No Python files should import nextflow_gen."""
        import wfc
        wfc_dir = Path(wfc.__file__).parent
        for py_file in wfc_dir.rglob("*.py"):
            source = py_file.read_text(errors="replace")
            assert "nextflow_gen" not in source, \
                f"{py_file.name} still references nextflow_gen"
