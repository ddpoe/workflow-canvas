"""
Demo: generate a Snakefile for the 3-step pipeline.

Pipeline: preprocess -> filter_cells -> label
Samples:  Pa16c (single sample)

Two modes demonstrated:
  1. Cartesian — all variant combinations (default)
  2. Selective — hand-picked combinations

Usage:
    cd <your-project>
    python -m wfc.demo_generate
    # or: pixi run generate
"""

from pathlib import Path

from .snakemake_gen import StepDef, PipelineDef, generate_snakefile


# =============================================================================
# Pipeline definitions
# =============================================================================

STEPS = [
    StepDef(
        method_name="preprocess",
        module_name="demo_pipeline",
        script_path="methods/preprocess/preprocess.py",
        params={"normalize": True, "scale_factor": 1.0},
        depends_on=[],
    ),
    StepDef(
        method_name="filter_cells",
        module_name="demo_pipeline",
        script_path="methods/filter_cells/filter_cells.py",
        params={"min_quality": 0.5, "remove_outliers": True},
        depends_on=["preprocess"],
    ),
    StepDef(
        method_name="label",
        module_name="demo_pipeline",
        script_path="methods/label/label.py",
        params={"threshold": 0.5, "label_column": "label"},
        depends_on=["filter_cells"],
    ),
]

PARAM_SETS = {
    "preprocess": {
        "default": {"normalize": True, "scale_factor": 1.0},
    },
    "filter_cells": {
        "strict":   {"min_quality": 0.7, "remove_outliers": True},
        "relaxed":  {"min_quality": 0.3, "remove_outliers": False},
    },
    "label": {
        "high_thresh": {"threshold": 0.5, "label_column": "label"},
        "low_thresh":  {"threshold": 0.0, "label_column": "label"},
    },
}


def build_demo_pipeline() -> PipelineDef:
    """Cartesian mode: 1 sample × (1 × 2 × 2) variants = 4 leaf runs."""
    return PipelineDef(
        steps=STEPS,
        samples=["Pa16c"],
        param_sets=PARAM_SETS,
    )


def build_demo_pipeline_selective() -> PipelineDef:
    """Selective mode: only 2 specific combos out of 4 possible."""
    return PipelineDef(
        steps=STEPS,
        samples=["Pa16c"],
        param_sets=PARAM_SETS,
        explicit_combos=[
            {"sample": "Pa16c", "preprocess": "default", "filter_cells": "strict",  "label": "high_thresh"},
            {"sample": "Pa16c", "preprocess": "default", "filter_cells": "relaxed", "label": "low_thresh"},
        ],
    )


def main():
    wfc_module_path = str(Path(__file__).resolve().parent.parent)

    # --- Cartesian ---
    pipeline = build_demo_pipeline()
    content = generate_snakefile(pipeline, wfc_module_path)
    out = Path("Snakefile")
    out.write_text(content)
    print(f"Generated: {out.resolve()}")
    print(f"  Mode: cartesian (all combinations)")
    print(f"  {len(pipeline.steps)} steps × {len(pipeline.samples)} sample × variants = leaf runs")
    print(f"\n  To run:  pixi run snakemake --cores 1 --snakefile Snakefile")

    # --- Selective ---
    pipeline_sel = build_demo_pipeline_selective()
    content_sel = generate_snakefile(pipeline_sel, wfc_module_path)
    out_sel = Path("Snakefile_selective")
    out_sel.write_text(content_sel)
    print(f"\nGenerated: {out_sel.resolve()}")
    print(f"  Mode: selective ({len(pipeline_sel.explicit_combos)} specific combos)")


if __name__ == "__main__":
    main()
