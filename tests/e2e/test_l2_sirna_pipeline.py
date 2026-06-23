"""
E2E Workflow: L2 siRNA Fan-Out / Fan-In Pipeline

Full validation of the 24-node siRNA KD analysis pipeline:

  - 6 csv_filter root nodes       (one per experimental condition)
  - 6 feature_qc nodes            (QC filter per condition per sample)
  - 3 csv_merge fan-in nodes      (merge scr+treatment pairs)
  - 3 binary_labeling nodes       (pRb proliferative phenotype label)
  - 3 train_classifier nodes      (calibrated logistic regression, multi-output)
  - 3 plot_decision_boundary nodes (KDE + decision boundary figures)

  24 nodes × 2 samples (Rep2_siRNA, Rep3_siRNA) = 48 Runs total.

This is the validation gate for all L2-blocking gaps:

  Gap 1  — node-ID-based pipeline identity (24 nodes using 6 methods)
  Gap 2  — fan-in multiple named inputs (sources_0/sources_1 slots)
  Gap 6  — output extension config (.csv → .png at leaf nodes)
  Gap 7  — multi-output named slot routing (predictions/model/metrics)
  Gap 8  — get_lineage DAG traversal with fan-in
  Gap 12 — unified {node_id}/{sample}/{variant}/ path scheme
  Gap 13 — per-method env isolation (pixi for train_classifier + plot)
  Gap 14 — stdout JSON metrics capture
  Gap 15 — versioning and cache-key computation
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlmodel import select

from dflow.core.decorators import workflow, Step, AutoStep

from wfc.init import init_project
from wfc.register import register_module, register_method
from wfc.cli import register_sample, run_pipeline
from wfc.lineage import get_lineage

# ---------------------------------------------------------------------------
# Project root — PYTHONPATH / WFC_ROOT for generated Snakefile
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Synthetic sample CSV factory
# ---------------------------------------------------------------------------
#
# 100 cells × 6 conditions = 600 rows.
# Column values are engineered to pass all feature_qc log10 thresholds:
#   area                    raw 1200–2500  → log10 3.08–3.40  > 3.0  ✓
#   R1_p27-AF488_nuc_mean   raw  400–1200  → log10 2.60–3.08  > 2.5  ✓
#   R1_CycD1-AF555_nuc_mean raw  250–800   → log10 2.40–2.90  > 2.25 ✓
#   R1_pRb-AF647_nuc_median raw  500–2000  → log10 2.70–3.30;
#                                             ~50% above threshold 2.95
#                                             → sufficient proliferative/
#                                               quiescent cells for training
#

_CONDITIONS = [
    "scr_50nM",
    "scr_75nM",
    "scr_125nM",
    "CycD1_50nM",
    "p27_75nM",
    "cycD1_50nM+p27_75nM",
]
_N_PER_CONDITION = 100


def _make_sample_csv(seed: int) -> pd.DataFrame:
    """Return a reproducible synthetic single-cell DataFrame."""
    rng = np.random.default_rng(seed)
    n = _N_PER_CONDITION * len(_CONDITIONS)
    return pd.DataFrame({
        "condition":                 np.repeat(_CONDITIONS, _N_PER_CONDITION),
        "area":                      rng.uniform(1200, 2500, n),
        "R1_p27-AF488_nuc_mean":     rng.uniform(400,  1200, n),
        "R1_CycD1-AF555_nuc_mean":   rng.uniform(250,  800,  n),
        "R1_pRb-AF647_nuc_median":   rng.uniform(500,  2000, n),
        "R2_p21-AF555_nuc_mean":     rng.uniform(200,  1000, n),
        "R2_CycE1-AF647_nuc_mean":   rng.uniform(200,  1000, n),
        "R2_CycE2-AF488_nuc_mean":   rng.uniform(200,  1000, n),
    })


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="Replaced by focused pipeline tests in test_pipeline_wiring.py")
@pytest.mark.slow
@workflow(
    purpose=(
        "Execute the full L2 siRNA fan-out/fan-in pipeline "
        "(24 nodes × 2 samples = 48 runs) via Snakemake and verify "
        "that all runs complete and lineage traversal spans the full DAG"
    )
)
def test_l2_sirna_pipeline(wfc_project):
    tmp = wfc_project

    # ─────────────────────────────────────────────────────────────────────────
    # Step 1 — Scaffold project
    # ─────────────────────────────────────────────────────────────────────────

    口 = AutoStep(step_num=1, name="Scaffold project")
    init_project(tmp, init_git=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 2 — Register pipeline modules
    # ─────────────────────────────────────────────────────────────────────────

    口 = Step(
        step_num=2,
        name="Register pipeline modules",
        purpose=(
            "Create five module rows (csv_tools, data_preprocessing, "
            "data_labeling, binary_label_classification, data_visualization) "
            "each with typed ModuleContract rows derived from method.yaml — "
            "so the DB encodes what artifact type every method in that module "
            "must produce."
        ),
        inputs="Module names, descriptions, and typed output contracts",
        outputs="Five Module rows + ModuleContract rows written to DB")
    _modules = [
        (
            "csv_tools",
            "Built-in CSV filter and merge utilities",
            [
                {"type": "output", "name": "filtered", "value_type": ".csv", "required": False},
                {"type": "output", "name": "merged",   "value_type": ".csv", "required": False},
            ]),
        (
            "data_preprocessing",
            "Feature QC and intensity-based cell filtering",
            [{"type": "output", "name": "preprocessed", "value_type": ".csv", "required": True}]),
        (
            "data_labeling",
            "Binary phenotype labeling from marker thresholds",
            [{"type": "output", "name": "labeled", "value_type": ".csv", "required": True}]),
        (
            "binary_label_classification",
            "Logistic regression classifiers for siRNA readouts",
            [
                {"type": "output", "name": "predictions", "value_type": ".csv",   "required": True},
                {"type": "output", "name": "metrics",     "value_type": ".csv",   "required": True},
                {"type": "output", "name": "model",       "value_type": ".model", "required": True},
            ]),
        (
            "data_visualization",
            "Decision boundary and KDE contour density plots",
            [{"type": "output", "name": "figure", "value_type": ".png", "required": True}]),
    ]
    for _mod_name, _mod_desc, _mod_contracts in _modules:
        口 = AutoStep(step_num=2.1, name="Register module")
        register_module(name=_mod_name, description=_mod_desc, contracts=_mod_contracts)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 3 — Register method scripts
    # ─────────────────────────────────────────────────────────────────────────

    口 = Step(
        step_num=3,
        name="Register method scripts",
        purpose=(
            "AST-scan each method script and write Method, TrackedFunction, and "
            "ParamDef rows. Detect env strategy: csv_filter, csv_merge, "
            "feature_qc, binary_labeling → inherit; train_classifier, "
            "plot_decision_boundary → pixi (triggers pixi install)."
        ),
        inputs="Six method directories under methods/ and modules/",
        outputs=(
            "Six Method rows in DB; "
            "pixi envs installed for train_classifier and plot_decision_boundary"
        ),
        critical=(
            "train_classifier and plot_decision_boundary carry pixi.toml — "
            "register_method runs pixi install (30–60 s first run per method)"
        ))
    _method_dirs = [
        (tmp / "modules" / "_builtin" / "csv_filter",                             "csv_tools"),
        (tmp / "modules" / "_builtin" / "csv_merge",                              "csv_tools"),
        (tmp / "methods" / "feature_qc",                                          "data_preprocessing"),
        (tmp / "methods" / "binary_labeling",                                     "data_labeling"),
        (tmp / "modules" / "binary_label_classification" / "train_classifier",    "binary_label_classification"),
        (tmp / "modules" / "data_visualization" / "plot_decision_boundary",       "data_visualization"),
    ]
    for _method_dir, _module_name in _method_dirs:
        口 = AutoStep(step_num=3.1, name="Register method")
        register_method(method_dir=_method_dir, module_name=_module_name)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 4 — Create synthetic sample CSVs
    # ─────────────────────────────────────────────────────────────────────────

    口 = Step(
        step_num=4,
        name="Create synthetic sample data",
        purpose=(
            "Generate two reproducible synthetic single-cell CSVs with all "
            "required columns (condition, area, fluorescence channels). "
            "Different RNG seeds give Rep2 and Rep3 independent distributions. "
            "Values are calibrated to pass all feature_qc log10 thresholds and "
            "yield ~50% pRb+ cells for binary_labeling."
        ),
        inputs="NumPy RNG (seed=42 for Rep2, seed=99 for Rep3)",
        outputs="Rep2_siRNA.csv and Rep3_siRNA.csv at tmp/")
    csv_rep2 = tmp / "Rep2_siRNA.csv"
    csv_rep3 = tmp / "Rep3_siRNA.csv"
    _make_sample_csv(seed=42).to_csv(csv_rep2, index=False)
    _make_sample_csv(seed=99).to_csv(csv_rep3, index=False)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 5 — Register samples
    # ─────────────────────────────────────────────────────────────────────────

    口 = Step(
        step_num=5,
        name="Register experimental samples",
        purpose=(
            "Copy each replicate CSV into data/samples/{name}/ and write a "
            "Sample row capturing source file_size and file_mtime before copy "
            "(Gap 15: immutable input fingerprint for cache-key computation)"
        ),
        inputs="Rep2_siRNA.csv and Rep3_siRNA.csv",
        outputs=(
            "data/samples/Rep2_siRNA/Rep2_siRNA.csv  "
            "data/samples/Rep3_siRNA/Rep3_siRNA.csv  "
            "Two Sample rows in DB with file_size + file_mtime"
        ))
    for _name, _csv in [("Rep2_siRNA", csv_rep2), ("Rep3_siRNA", csv_rep3)]:
        口 = AutoStep(step_num=5.1, name="Register sample")
        register_sample(name=_name, source_path=_csv, project_root=tmp)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 6 — Run pipeline
    # ─────────────────────────────────────────────────────────────────────────

    口 = AutoStep(step_num=6, name="Execute L2 siRNA pipeline")
    run_pipeline(
        pipeline_path=str(tmp / "pipeline_l2_sirna.json"),
        project_root=str(tmp),
        wfc_root=str(PROJECT_ROOT),
        cores=4)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 7 — Verify final state
    # ─────────────────────────────────────────────────────────────────────────

    口 = Step(
        step_num=7,
        name="Verify pipeline completion and lineage",
        purpose=(
            "Assert all 5 modules have ModuleContract rows (typed output "
            "contracts registered in Step 2). "
            "Assert exactly 48 completed Run rows exist (24 nodes × 2 samples). "
            "Retrieve a plot_decision_boundary run and walk its lineage DAG — "
            "the chain must be ≥ 6 hops deep: "
            "csv_filter → feature_qc → csv_merge → "
            "binary_labeling → train_classifier → plot_decision_boundary."
        ),
        outputs="5 modules with contracts; 48 completed runs; lineage chain ≥ 6 nodes for leaf run")
    from wfc.database import get_session
    from wfc.models import Run, Method, ModuleContract

    # Assert all 5 modules have contracts registered
    with get_session() as session:
        all_contracts = session.exec(select(ModuleContract)).all()
    module_contract_counts = {}
    for mc in all_contracts:
        module_contract_counts[mc.module_id] = module_contract_counts.get(mc.module_id, 0) + 1
    assert len(module_contract_counts) == 5, (
        f"Expected 5 modules with contracts, got {len(module_contract_counts)}"
    )

    # Assert all 48 runs completed
    with get_session() as session:
        completed = session.exec(
            select(Run).where(Run.status == "completed")
        ).all()
    assert len(completed) == 48, (
        f"Expected 48 completed runs, got {len(completed)}"
    )

    # Spot-check lineage depth for a leaf node run
    with get_session() as session:
        plot_method = session.exec(
            select(Method).where(Method.name == "plot_decision_boundary")
        ).first()
        assert plot_method is not None, (
            "plot_decision_boundary method not found in DB after pipeline run"
        )
        leaf_run = session.exec(
            select(Run)
            .where(Run.method_id == plot_method.id)
            .where(Run.status == "completed")
        ).first()

    assert leaf_run is not None, "No completed plot_decision_boundary run found"

    lineage = get_lineage(leaf_run.id)
    assert len(lineage) >= 6, (
        f"Expected lineage depth ≥ 6 for plot_decision_boundary run {leaf_run.id}, "
        f"got {len(lineage)}: "
        f"{[n['method_name'] for n in lineage]}"
    )
