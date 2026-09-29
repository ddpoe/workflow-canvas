"""Workflow Canvas — track pipeline runs with full lineage.

This is the host engine package: it owns the DB, the DVC cache, contracts,
and the ``wfc run-step`` orchestration boundary. It deliberately keeps its
top-level namespace empty of heavy imports so that lightweight callers can
``import wfc`` without pulling in the orchestration layer.

The method-authoring helpers live in the separate distribution ``wfc-client``
(``import wfc_client as wfc``) — one small dependency, ``axiom-annotations``
— which runs inside the user container and never imports this package. Callers that need the host
orchestration layer import the submodules directly::

    from wfc.orchestration import generate_snakefile
    from wfc.execution import run_pipeline, load_pipeline_from_path
    from wfc.registration import register_sample
    from wfc.init import init_project
"""

__all__: list[str] = []
