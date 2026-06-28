"""
Unit & integration tests: Gap 14 — Stdout metrics capture.

Story: Every method script prints ``json.dumps(metrics)`` as its last stdout
line.  The ``wfc run-step`` command captures that line, parses it as JSON, and
writes it to a ``metrics.json`` sidecar (ADR 008).

Test coverage (ADR 008 update):
  1. Generated Snakefile rules delegate to ``wfc run-step`` via shell directives.
  2. Each generated rule includes the node_id in its params block.
  3. Generated rules use shell (not run:) — no inline Python execution.
  4. ``complete_run`` (via CLI) stores the metrics dict in ``Run.metrics``
     and writes it to ``meta.json``.

Tests 1-3 are pure string checks on the generated Snakefile text.
Test 4 uses the ``cli`` fixture (DB-backed).
"""

import json
import os

from axiom_annotations import workflow, Step

from wfc.snakemake_gen import StepDef, PipelineDef, generate_snakefile


# =============================================================================
# Helpers
# =============================================================================

def _minimal_pipeline(**step_kwargs):
    """Return a one-step PipelineDef for generator tests."""
    step = StepDef(
        method_name="feature_qc",
        module_name="data_preprocessing",
        script_path="methods/feature_qc/feature_qc.py",
        params={"filters": []},
        **step_kwargs)
    return PipelineDef(steps=[step], samples=["Rep2"])


def _two_step_pipeline():
    """Return a two-step PipelineDef (root → leaf) for rule tests."""
    root = StepDef(
        method_name="csv_filter",
        module_name="csv_tools",
        script_path="methods/csv_filter/csv_filter.py",
        params={})
    leaf = StepDef(
        method_name="feature_qc",
        module_name="data_preprocessing",
        script_path="methods/feature_qc/feature_qc.py",
        params={"filters": []},
        depends_on=["csv_filter"])
    return PipelineDef(steps=[root, leaf], samples=["Rep2"])


def _seed_module_method(cli, module="data_preprocessing", method="feature_qc"):
    """Register a module and a bare-minimum method via the CLI."""
    result = cli("register-module", "--name", module, "--description", "test", "--contracts", "[]")
    assert result.returncode == 0, result.stderr
    method_dir = os.path.join("methods", method)
    os.makedirs(method_dir, exist_ok=True)
    with open(os.path.join(method_dir, f"{method}.py"), "w") as f:
        f.write("def main(df, params): return df\n")
    # ADR-019 Cycle H: execution is container-only, so every registered method
    # must name a built container env. The tmp_project fixture writes a
    # placeholder ``fixture-env`` record so this registration validates
    # Docker-free (no image pull at registration time).
    yaml_path = os.path.join(method_dir, "method.yaml")
    if not os.path.exists(yaml_path):
        with open(yaml_path, "w") as f:
            f.write(
                "inputs:\n"
                "  data:\n"
                "    type: .csv\n"
                "    required: true\n"
                "outputs:\n"
                "  result:\n"
                "    type: .csv\n"
                "    required: true\n"
                "params: {}\n"
                "executor: python\n"
                "env: container:fixture-env\n"
            )
    result = cli("register-method", method_dir, "--module", module)
    assert result.returncode == 0, result.stderr


# =============================================================================
# 1. Generated rules delegate to wfc run-step via shell
# =============================================================================

@workflow(
    purpose="The generated Snakefile rules delegate to wfc run-step via shell "
            "directives — no inline run_method helper or JSON parsing in the Snakefile")
def test_rules_delegate_to_run_step(wfc_root):
    """ADR 008: rules use shell directives, not inline Python run: blocks."""
    口 = Step(step_num=1, name="Generate Snakefile",
             purpose="Produce Snakefile text from a single-step pipeline")
    snakefile = generate_snakefile(_minimal_pipeline(), wfc_root)

    口 = Step(step_num=2, name="Verify shell directive delegates to run-step",
             purpose="Confirm the rule uses shell: with wfc run-step command")
    assert "shell:" in snakefile
    assert "run-step" in snakefile
    assert "--node-id" in snakefile

    口 = Step(step_num=3, name="Verify no inline Python execution logic",
             purpose="Confirm there is no run_method helper or json.loads in the Snakefile")
    assert "run_method" not in snakefile
    assert "json.loads(last_line)" not in snakefile
    assert "run:" not in snakefile.split("rule ")[1]  # no run: block in rules


# =============================================================================
# 2. Each rule includes node_id in params for run-step dispatch
# =============================================================================

@workflow(
    purpose="Each Snakemake rule includes the node_id in its params block so "
            "wfc run-step can identify which step to execute")
def test_rule_includes_node_id_in_params(wfc_root):
    """Generated rules include node_id in params for run-step dispatch."""
    口 = Step(step_num=1, name="Define two-step pipeline",
             purpose="Two steps means two rules — both should include node_id in params")
    pipeline = _two_step_pipeline()

    口 = Step(step_num=2, name="Generate Snakefile",
             purpose="Produce Snakefile text")
    snakefile = generate_snakefile(pipeline, wfc_root)

    口 = Step(step_num=3, name="Check each rule has node_id in params",
             purpose="Each rule should have a params block with node_id")
    assert 'node_id="csv_filter"' in snakefile
    assert 'node_id="feature_qc"' in snakefile
    # Both rules delegate to run-step via shell directives
    # Count "-m wfc run-step" which only appears in shell: lines (not comments)
    occurrences = snakefile.count("-m wfc run-step")
    assert occurrences == 2, (
        f"Expected 2 occurrences of '-m wfc run-step' "
        f"(one per rule), got {occurrences}"
    )


# =============================================================================
# 3. Generated rules use shell (not run:) — no inline execution logic
# =============================================================================

@workflow(
    purpose="ADR 008: Generated rules use shell directives only — no inline "
            "Python run: blocks, no complete_run calls, no metrics_dict handling")
def test_rule_uses_shell_not_run_block(wfc_root):
    """Generated rules have no run: blocks or inline Python execution logic."""
    口 = Step(step_num=1, name="Generate Snakefile",
             purpose="Produce Snakefile text from a single-step pipeline")
    snakefile = generate_snakefile(_minimal_pipeline(), wfc_root)

    口 = Step(step_num=2, name="Verify no inline execution logic in rules",
             purpose="No complete_run, run_method, or metrics handling in the Snakefile rules")
    # ADR 008: all execution logic moved to wfc run-step
    assert "complete_run" not in snakefile.split("rule ")[1]
    assert "metrics_dict" not in snakefile
    assert "run_method" not in snakefile

    口 = Step(step_num=3, name="Verify shell() used for onsuccess/onerror handlers",
             purpose="onsuccess/onerror use Snakemake shell() calls")
    assert "shell(" in snakefile, "onsuccess/onerror should use shell() calls"
    assert "pipeline-summary" in snakefile


# =============================================================================
# 4. complete_run CLI stores metrics in Run.metrics
# =============================================================================

@workflow(
    purpose="When complete_run receives a --metrics JSON argument it stores "
            "the dict in Run.metrics and writes it to meta.json")
def test_complete_run_stores_metrics_in_db(cli):
    """complete_run --metrics '{"n_cells": 980}' → Run.metrics == {"n_cells": 980}."""
    口 = Step(step_num=1, name="Seed module and method",
             purpose="Register the minimum DB fixtures needed for a run")
    _seed_module_method(cli)

    口 = Step(step_num=2, name="Register a run",
             purpose="Create a Run row in status='running'")
    r = cli("register_run", "--method", "feature_qc", "--module", "data_preprocessing",
            "--sample", "Rep2", "--params", "{}")
    assert r.returncode == 0, r.stderr
    run_id = r.stdout.strip()

    口 = Step(step_num=3, name="Create a fake archive output",
             purpose="complete_run requires the archive file to exist before hardlinking")
    archive = os.path.join(".runs", f"{int(run_id):08d}")
    os.makedirs(archive, exist_ok=True)
    output_path = os.path.join(archive, "output.csv")
    with open(output_path, "w") as f:
        f.write("col\n1\n2\n")

    口 = Step(step_num=4, name="Complete the run with metrics",
             purpose="Pass a JSON metrics dict to complete_run via --metrics")
    metrics_payload = json.dumps({"n_cells_before": 1200, "n_cells_after": 980})
    r = cli("complete_run", "--run-id", run_id, "--status", "completed",
            "--output", output_path, "--metrics", metrics_payload)
    assert r.returncode == 0, r.stderr

    口 = Step(step_num=5, name="Verify metrics stored in DB",
             purpose="Query the Run row and confirm Run.metrics matches what was passed")
    from wfc.database import get_session
    from wfc.models import Run

    with get_session() as session:
        run = session.get(Run, int(run_id))
        assert run is not None
        assert run.metrics == {"n_cells_before": 1200, "n_cells_after": 980}

    # ADR-007 Phase 2: meta.json is no longer written to archive.
    # Metrics are stored in the DB (verified in step 5 above).
