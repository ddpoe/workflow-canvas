"""Workflow Canvas — track pipeline runs with full lineage.

This is the host engine package: it owns the DB, the DVC cache, contracts,
and the ``wfc run-step`` orchestration boundary. It deliberately keeps its
top-level namespace empty of heavy imports so that lightweight callers can
``import wfc`` without pulling in dflow or the rest of the orchestration
layer.

Tier-1 method authoring lives in the separate pure-stdlib distribution
``wfc-client`` (``import wfc_client as wfc``), which runs inside the user
container and never imports this package. Callers that need the host
orchestration layer import the submodules directly::

    from wfc.snakemake_gen import load_pipeline, generate_snakefile
    from wfc.cli import register_sample, run_pipeline
    from wfc.init import init_project
"""

__all__: list[str] = []
