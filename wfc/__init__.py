"""Process Manager MVP — track pipeline runs with full lineage."""

# NOTE: Only wfc.method is imported here.
#
# snakemake_gen, cli, and init all depend on dflow (or pull in modules that
# do), which is only installed in the outer dev environment — NOT in the
# isolated pixi envs used by train_classifier / plot_decision_boundary.
#
# Method scripts do:
#   from wfc.method import wfc_method, wfc_method_main
# That import resolves to this __init__ first. Keeping it dflow-free means
# the pixi-isolated methods can import wfc without blowing up.
#
# Callers that need the orchestration layer should import directly:
#   from wfc.snakemake_gen import load_pipeline, generate_snakefile
#   from wfc.cli import register_sample, run_pipeline
#   from wfc.init import init_project

from .method import wfc_method, wfc_method_main, ContractViolation

__all__ = [
    "wfc_method",
    "wfc_method_main",
    "ContractViolation",
]
