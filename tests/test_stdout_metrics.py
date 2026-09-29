"""
Unit & integration tests: Stdout metrics capture.

Story: the generated Snakefile hands every step to ``wfc run-step``, which
owns metrics capture; ``complete_run`` stores a run's metrics in the database.

Test coverage:
  1. Generated Snakefile rules delegate to ``wfc run-step`` via shell directives.
  2. Each generated rule includes the node_id in its params block.
  3. Generated rules use shell (not run:) — no inline Python execution.
  4. ``complete_run`` (via CLI) stores the metrics dict in ``Run.metrics``.

Tests 1-3 are pure string checks on the generated Snakefile text.
Test 4 uses the ``cli`` fixture (DB-backed).
"""

import json
import os
from pathlib import Path

from axiom_annotations import workflow, Step

from tests.conftest import register_test_method
from wfc.graph import StepDef, PipelineDef
from wfc.orchestration import generate_snakefile


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


def _register_feature_qc(module="data_preprocessing", method="feature_qc"):
    """Declare ``feature_qc``'s bare-minimum contract and register it.

    The registration is production's (``register_test_method``); what this
    module owns is the contract: one required ``data`` input, one ``result``
    output.
    """
    project = Path.cwd()  # tmp_project pins cwd at the project root
    method_dir = project / "methods" / method
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / f"{method}.py").write_text("def main(df, params): return df\n")
    # Execution is container-only, so the contract names a built container
    # env. tmp_project writes the placeholder ``fixture-env`` record, so the
    # registration validates Docker-free (no image pull).
    contract = method_dir / "method.yaml"
    if not contract.exists():
        contract.write_text(
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
            "env: fixture-env\n"
        )
    register_test_method(project, module_name=module, method_dir=method_dir,
                         method_name=method)


# =============================================================================
# 1. Generated rules delegate to wfc run-step via shell
# =============================================================================

@workflow(
    purpose="The generated Snakefile rules delegate to wfc run-step via shell "
            "directives — the rule body is not an inline Python run: block")
def test_rules_delegate_to_run_step(wfc_root):
    """Rules use shell directives, not inline Python run: blocks."""
    口 = Step(step_num=1, name="Generate Snakefile",
             purpose="Produce Snakefile text from a single-step pipeline")
    snakefile = generate_snakefile(_minimal_pipeline(), wfc_root)

    口 = Step(step_num=2, name="Verify shell directive delegates to run-step",
             purpose="Confirm the rule uses shell: with wfc run-step command")
    assert "shell:" in snakefile
    assert "run-step" in snakefile
    assert "--node-id" in snakefile

    口 = Step(step_num=3, name="Verify no inline Python run block",
             purpose="Confirm the rule body is a shell delegation, not a run: block")
    rule_block = snakefile.split("rule feature_qc:")[1].split("\nrule ")[0]
    assert "shell:" in rule_block, rule_block
    assert "run:" not in rule_block


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
    purpose="Generated rules use shell directives only — no inline "
            "Python run: blocks and no complete_run calls in the rules")
def test_rule_uses_shell_not_run_block(wfc_root):
    """Generated rules have no run: blocks or inline Python execution logic."""
    口 = Step(step_num=1, name="Generate Snakefile",
             purpose="Produce Snakefile text from a single-step pipeline")
    snakefile = generate_snakefile(_minimal_pipeline(), wfc_root)

    口 = Step(step_num=2, name="Verify no inline execution logic in rules",
             purpose="The step's rule is a shell delegation with no run: block and no complete_run call")
    # all execution logic lives in wfc run-step
    rule_block = snakefile.split("rule feature_qc:")[1].split("\nrule ")[0]
    assert "shell:" in rule_block, rule_block
    assert "run:" not in rule_block
    assert "complete_run" not in rule_block

    口 = Step(step_num=3, name="Verify shell() used for the onerror handler",
             purpose="onerror delegates to wfc fail_pipeline through a "
                     "Snakemake shell() call")
    assert "shell(" in snakefile, "onerror should use a shell() call"
    assert "fail_pipeline" in snakefile


# =============================================================================
# 4. complete_run CLI stores metrics in Run.metrics
# =============================================================================

@workflow(
    purpose="When complete_run receives a --metrics JSON argument it stores "
            "the dict in Run.metrics")
def test_complete_run_stores_metrics_in_db(cli):
    """complete_run --metrics '{"n_cells": 980}' → Run.metrics == {"n_cells": 980}."""
    口 = Step(step_num=1, name="Seed module and method",
             purpose="Register the minimum DB fixtures needed for a run")
    _register_feature_qc()

    口 = Step(step_num=2, name="Register a run",
             purpose="Create a Run row in status='running'")
    r = cli("register_run", "--method", "feature_qc", "--module", "data_preprocessing",
            "--sample", "Rep2", "--params", "{}")
    assert r.returncode == 0, r.stderr
    run_id = r.stdout.strip()

    口 = Step(step_num=3, name="Create a fake archive output",
             purpose="Write the archive file and the RunOutput row the collect "
                     "phase records for it; complete_run updates that row by path")
    archive = os.path.join(".runs", f"{int(run_id):08d}")
    os.makedirs(archive, exist_ok=True)
    output_path = os.path.join(archive, "output.csv")
    with open(output_path, "w") as f:
        f.write("col\n1\n2\n")

    from wfc.persistence import get_session, RunOutput
    with get_session() as session:
        session.add(RunOutput(run_id=int(run_id), slot="result",
                              output_name="output.csv",
                              artifact_path=output_path,
                              artifact_type="method_file"))
        session.commit()

    口 = Step(step_num=4, name="Complete the run with metrics",
             purpose="Pass a JSON metrics dict to complete_run via --metrics")
    metrics_payload = json.dumps({"n_cells_before": 1200, "n_cells_after": 980})
    r = cli("complete_run", "--run-id", run_id, "--status", "completed",
            "--output", output_path, "--metrics", metrics_payload)
    assert r.returncode == 0, r.stderr

    口 = Step(step_num=5, name="Verify metrics stored in DB",
             purpose="Query the Run row and confirm Run.metrics matches what was passed")
    from wfc.persistence import Run

    with get_session() as session:
        run = session.get(Run, int(run_id))
        assert run is not None
        assert run.metrics == {"n_cells_before": 1200, "n_cells_after": 980}
