"""
Unit Tests: check_cache and register_run

Validates the caching layer that Snakemake rules use to skip redundant work:
  - ``register_run`` stores slot names in ``RunInput.input_name``
  - ``check_cache`` matches on the full ``(slot, parent_id)`` tuple set
  - Cache misses on wrong slots, missing parents, or different params
  - Cache hits return the most recent matching run
  - Sample-conditional params (Differential QC scenario) produce isolated
    cache entries with no cross-sample false hits
"""

import json
import os

from axiom_annotations import workflow, Step


# =============================================================================
# Helpers
# =============================================================================

def _seed_module_method(cli, module="csv_tools", method="csv_merge"):
    """Register a module and method via CLI so register_run can reference them."""
    result = cli("register-module", "--name", module, "--description", "test module", "--contracts", "[]")
    assert result.returncode == 0, result.stderr
    method_dir = os.path.join("methods", method)
    os.makedirs(method_dir, exist_ok=True)
    script_name = f"{method}.py"
    script_path = os.path.join(method_dir, script_name)
    if not os.path.exists(script_path):
        with open(script_path, "w") as f:
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


def _register_and_complete(cli, method, module, sample, params="{}", parent_run_ids=None):
    """Register a run, create a fake archive, complete it. Returns run ID string."""
    args = ["register_run", "--method", method, "--module", module,
            "--sample", sample, "--params", params]
    if parent_run_ids:
        args += ["--parent-run-id"] + list(parent_run_ids)
    r = cli(*args)
    assert r.returncode == 0, r.stderr
    run_id = r.stdout.strip()

    archive = os.path.join(".runs", f"{int(run_id):08d}")
    os.makedirs(archive, exist_ok=True)
    with open(os.path.join(archive, "output.csv"), "w") as f:
        f.write("col\n1\n")
    cli("complete_run", "--run-id", run_id, "--status", "completed",
        "--output", os.path.join(archive, "output.csv"))
    return run_id


# =============================================================================
# Tests
# =============================================================================

def test_slot_matching(cli):
    """register_run with slot:id parents → check_cache finds exact match,
    rejects mismatched slots or missing parents."""

    _seed_module_method(cli)

    # Two upstream runs (no parents — simulating root filter nodes)
    run_a = _register_and_complete(cli, "csv_merge", "csv_tools", "S1")
    run_b = _register_and_complete(cli, "csv_merge", "csv_tools", "S1")

    # Merge run with two parents on the "sources" slot
    merge_id = _register_and_complete(
        cli, "csv_merge", "csv_tools", "S1",
        parent_run_ids=[f"sources:{run_a}", f"sources:{run_b}"])

    # ── Verify RunInput rows store the slot name ──────────────────────────
    from wfc.database import get_session
    from wfc.models import RunInput
    from sqlmodel import select

    with get_session() as session:
        inputs = session.exec(
            select(RunInput).where(RunInput.run_id == int(merge_id))
        ).all()
        actual = {(ri.input_name, ri.source_run_id) for ri in inputs}
        assert actual == {("sources", int(run_a)), ("sources", int(run_b))}

    # ── Exact match → cache hit ───────────────────────────────────────────
    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", "{}",
            "--parent-run-id", f"sources:{run_a}", f"sources:{run_b}")
    assert r.stdout.strip() == merge_id

    # ── Same IDs, wrong slot name → miss ──────────────────────────────────
    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", "{}",
            "--parent-run-id", f"data:{run_a}", f"data:{run_b}")
    assert r.stdout.strip() == "NONE"

    # ── Same IDs, no slot (defaults to "upstream") → miss ─────────────────
    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", "{}",
            "--parent-run-id", run_a, run_b)
    assert r.stdout.strip() == "NONE"

    # ── Subset of parents → miss ──────────────────────────────────────────
    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", "{}",
            "--parent-run-id", f"sources:{run_a}")
    assert r.stdout.strip() == "NONE"

    # ── Reversed order → still a hit (set comparison, not ordered) ────────
    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", "{}",
            "--parent-run-id", f"sources:{run_b}", f"sources:{run_a}")
    assert r.stdout.strip() == merge_id


def test_params_mismatch(cli):
    """Same method+sample+parents but different params → cache miss."""

    _seed_module_method(cli)

    run_id = _register_and_complete(
        cli, "csv_merge", "csv_tools", "S1",
        params='{"column": "condition"}')

    # Exact params → hit
    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", '{"column": "condition"}')
    assert r.stdout.strip() == run_id

    # Different params → miss
    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", '{"column": "replicate"}')
    assert r.stdout.strip() == "NONE"


def test_no_parents_returns_newest(cli):
    """Two identical parentless runs → cache returns the newer one."""

    _seed_module_method(cli)

    run_old = _register_and_complete(cli, "csv_merge", "csv_tools", "S1")
    run_new = _register_and_complete(cli, "csv_merge", "csv_tools", "S1")

    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", "{}")
    assert r.stdout.strip() == run_new


def test_missing_archive_is_cache_miss(cli):
    """Completed run exists in DB but archive deleted → cache miss."""
    import shutil

    _seed_module_method(cli)

    run_id = _register_and_complete(cli, "csv_merge", "csv_tools", "S1")

    # Verify it's a hit first
    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", "{}")
    assert r.stdout.strip() == run_id

    # Delete the archive
    archive = os.path.join(".runs", f"{int(run_id):08d}")
    shutil.rmtree(archive)

    # Now it's a miss
    r = cli("check_cache", "--method", "csv_merge", "--sample", "S1",
            "--params", "{}")
    assert r.stdout.strip() == "NONE"


@workflow(
    purpose="Verify that sample-conditional QC variants produce isolated cache "
            "entries — the same method run with different params for different "
            "samples has no false cache hits and no cross-sample contamination"
)
def test_differential_qc_cache(cli):
    """Differential QC scenario: Rep2 uses threshold 2.5 (standard), Rep3 uses
    threshold 2.3 (dim_corrected). Each sample must cache independently — a
    query with the wrong params or wrong sample must always miss."""

    口 = Step(
        step_num=1,
        name="Seed QC method",
        purpose="Register a feature_qc module and method so run records can be created")
    result = cli("register-module", "--name", "data_preprocessing",
                 "--description", "Cell-level QC", "--contracts", "[]")
    assert result.returncode == 0, result.stderr
    method_dir = os.path.join("methods", "feature_qc")
    os.makedirs(method_dir, exist_ok=True)
    script_path = os.path.join(method_dir, "feature_qc.py")
    if not os.path.exists(script_path):
        with open(script_path, "w") as f:
            f.write("def main(df, params): return df\n")
    # ADR-019 Cycle H: container-only execution requires a method.yaml naming a
    # built container env; tmp_project writes the placeholder ``fixture-env``.
    yaml_path = os.path.join(method_dir, "method.yaml")
    if not os.path.exists(yaml_path):
        with open(yaml_path, "w") as f:
            f.write(
                "inputs:\n  data:\n    type: .csv\n    required: true\n"
                "outputs:\n  result:\n    type: .csv\n    required: true\n"
                "params: {}\nexecutor: python\nenv: container:fixture-env\n"
            )
    result = cli("register-method", method_dir, "--module", "data_preprocessing")
    assert result.returncode == 0, result.stderr

    params_standard     = json.dumps({"filters": [{"column": "R1_p27", "min": 2.5}]})
    params_dim_corrected = json.dumps({"filters": [{"column": "R1_p27", "min": 2.3}]})

    口 = Step(
        step_num=2,
        name="Record Rep2 standard run",
        purpose="Complete a feature_qc run for Rep2 with the standard p27 threshold",
        inputs="Rep2_siRNA sample, threshold 2.5 params",
        outputs="Completed run ID for Rep2")
    run_rep2 = _register_and_complete(
        cli, "feature_qc", "data_preprocessing", "Rep2_siRNA",
        params=params_standard)

    口 = Step(
        step_num=3,
        name="Record Rep3 dim-corrected run",
        purpose="Complete a feature_qc run for Rep3 with the lower dim-corrected threshold",
        inputs="Rep3_siRNA sample, threshold 2.3 params",
        outputs="Completed run ID for Rep3")
    run_rep3 = _register_and_complete(
        cli, "feature_qc", "data_preprocessing", "Rep3_siRNA",
        params=params_dim_corrected)

    口 = Step(
        step_num=4,
        name="Verify no cross-sample false hits",
        purpose="Confirm that querying each sample with the other sample's params "
                "returns no match — different thresholds must never share a cache entry")
    # Rep3 query with Rep2's params → miss (correct sample, wrong params)
    r = cli("check_cache", "--method", "feature_qc", "--sample", "Rep3_siRNA",
            "--params", params_standard)
    assert r.stdout.strip() == "NONE", (
        "Rep3 with standard threshold should not match the Rep2 run"
    )

    # Rep2 query with Rep3's params → miss (correct sample, wrong params)
    r = cli("check_cache", "--method", "feature_qc", "--sample", "Rep2_siRNA",
            "--params", params_dim_corrected)
    assert r.stdout.strip() == "NONE", (
        "Rep2 with dim-corrected threshold should not match the Rep3 run"
    )

    # Rep2 query with Rep2's params but Rep3 sample → miss (wrong sample)
    r = cli("check_cache", "--method", "feature_qc", "--sample", "Rep3_siRNA",
            "--params", params_dim_corrected)
    assert r.stdout.strip() == run_rep3

    口 = Step(
        step_num=5,
        name="Verify same-run cache hit",
        purpose="Confirm that querying each sample with its own params returns "
                "the correct run — no spurious misses after the cross-sample checks")
    r = cli("check_cache", "--method", "feature_qc", "--sample", "Rep2_siRNA",
            "--params", params_standard)
    assert r.stdout.strip() == run_rep2

    r = cli("check_cache", "--method", "feature_qc", "--sample", "Rep3_siRNA",
            "--params", params_dim_corrected)
    assert r.stdout.strip() == run_rep3
