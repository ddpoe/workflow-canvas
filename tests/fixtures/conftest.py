"""
Shared fixture method infrastructure for pipeline tests.

Provides reusable fixtures for registering lightweight test methods
and building pipeline JSON files from topology descriptions.

Fixtures:
  - register_fixture_methods: Registers lightweight fixture methods
                   (transform, merge, faulty) in a test project.
  - pipeline_factory: Builds pipeline JSON files from topology descriptions.
                   Supports both method nodes and system nodes
                   (input_selector, run_reference).

Helpers:
  - register_test_method: Plain function (no pytest dependency) that exercises
                   the production registration code path (init_project +
                   reset_engine + register_module + register_method) for a
                   single method directory. Reusable from both the fixture
                   above and Tier 3 integration tests.
"""

import csv
import json
import os
import shutil
from pathlib import Path

import pytest


def register_test_method(
    project_dir: Path,
    *,
    module_name: str,
    method_dir: Path,
    method_name: str | None = None,
    module_contracts: list[dict] | None = None,
) -> None:
    """Register a method into a tmp wfc project using production registration APIs.

    Runs the same code path a real ``wfc register`` CLI invocation does:
    ``wfc.init.init_project`` (idempotent), ``wfc.database.reset_engine``,
    ``wfc.register.register_module``, ``wfc.register.register_method``. No DB
    hand-crafting, no stub registration.

    The helper temporarily ``chdir``\\ s into ``project_dir`` for the
    registration call because ``register_method`` and ``_git_commit_registration``
    use ``Path.cwd()`` to resolve relative script paths and the git repo root.
    Original cwd is restored on return.

    Caller responsibility: ``WFC_PROJECT_ROOT`` and ``DATABASE_URL`` must be set
    in ``os.environ`` BEFORE invoking (typically via ``monkeypatch.setenv`` in
    the calling test). The project directory must be a git repo (run ``git init``
    plus user.email/user.name config before calling).

    Args:
        project_dir: Project root directory. Will be initialized (idempotent)
            if not already.
        module_name: Module name to register (or upsert) into the database.
        method_dir: Directory containing the method's ``{method_name}.py`` and
            ``method.yaml``. Must already exist with the method source files.
        method_name: Method name (defaults to ``method_dir.name``).
        module_contracts: Optional list of module contract dicts (passed to
            :func:`wfc.register.register_module`). Defaults to empty list.
    """
    from wfc.init import init_project
    from wfc.register import register_module, register_method
    from wfc.database import reset_engine

    project_dir = Path(project_dir).resolve()
    method_dir = Path(method_dir).resolve()

    # init_project is idempotent on a project_dir that already has .wfc/ —
    # the existing scaffold is left in place; only missing pieces are filled.
    init_project(project_dir)
    reset_engine()

    register_module(
        name=module_name,
        contracts=module_contracts if module_contracts is not None else [],
    )

    # register_method uses Path.cwd() to compute the relative script_path and
    # to resolve the git repo root for the commit step. chdir for the duration
    # of the call so the caller doesn't have to manage it.
    prev_cwd = os.getcwd()
    try:
        os.chdir(project_dir)
        register_method(
            method_dir=method_dir,
            module_name=module_name,
            method_name=method_name,
        )
    finally:
        os.chdir(prev_cwd)


def create_sample_csv(project_dir: Path, sample_name: str, num_rows: int = 3) -> Path:
    """Write a sample CSV to data/samples/{sample_name}/data.csv.

    Args:
        project_dir: Project root directory.
        sample_name: Sample identifier.
        num_rows: Number of data rows to generate.

    Returns:
        Path to the created CSV file.
    """
    sample_dir = project_dir / "data" / "samples" / sample_name
    sample_dir.mkdir(parents=True, exist_ok=True)
    csv_path = sample_dir / "data.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "value"])
        for i in range(num_rows):
            writer.writerow([i, i * 10])
    return csv_path

FIXTURE_METHODS_DIR = Path(__file__).resolve().parent / "methods"

# Fixture method definitions: (method_name, module_name)
FIXTURE_METHODS = [
    ("transform", "test_pipeline"),
    ("merge", "test_pipeline"),
    ("faulty", "test_pipeline"),
]

# System node types that do not require a "method" key or script
SYSTEM_NODE_TYPES = {"input_selector", "run_reference"}


@pytest.fixture
def register_fixture_methods(git_project, monkeypatch):
    """Register lightweight fixture methods in a fresh test project.

    Sets up: init_project, register test_pipeline module, register each
    fixture method (transform, merge, faulty). All methods use
    env: inherit and have no external dependencies.

    Delegates per-method registration to :func:`register_test_method` so
    the same code path is exercised from Tier 3 integration tests.

    Returns:
        The git_project path with all fixture methods registered.
    """
    tmp_path = git_project
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))

    db_path = tmp_path / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from wfc.database import reset_engine

    for method_name, module_name in FIXTURE_METHODS:
        src_dir = FIXTURE_METHODS_DIR / method_name
        dest_dir = tmp_path / "methods" / method_name
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.copytree(src_dir, dest_dir)

        register_test_method(
            project_dir=tmp_path,
            module_name=module_name,
            method_dir=dest_dir,
            method_name=method_name,
        )

    yield tmp_path

    reset_engine()


@pytest.fixture
def pipeline_factory(register_fixture_methods):
    """Factory that builds pipeline JSON files from topology descriptions.

    Returns a callable that creates enriched pipeline JSON files ready
    for run_pipeline(). Nodes are enriched with script paths and
    slot_outputs from the registered fixture method contracts.

    Supports both method nodes and system nodes (input_selector,
    run_reference).  System nodes do not require a "method" key or
    method script on disk.

    Usage::

        path = pipeline_factory(
            name="linear",
            nodes=[
                {"id": "sel1", "type": "input_selector",
                 "samples": ["s1"]},
                {"id": "t1", "method": "transform", "module": "test_pipeline"},
            ],
            links=[{"source": "sel1", "target": "t1"}],
            samples=[],
        )
    """
    project_dir = register_fixture_methods

    def _create_pipeline(
        name: str,
        nodes: list[dict],
        links: list[dict],
        samples: list[str],
        param_sets: dict | None = None,
    ) -> Path:
        """Create an enriched pipeline JSON file.

        Supports both method nodes and system nodes. System nodes
        (input_selector, run_reference) do not require a "method"
        key and have no method script on disk.

        Args:
            name: Pipeline name (used for filename).
            nodes: Node dicts. Method nodes require id, method, module.
                System nodes require id, type (input_selector or
                run_reference), and type-specific fields.
            links: Link dicts with source, target, optional target_slot/source_slot.
            samples: Sample name list.
            param_sets: Optional param_sets dict.

        Returns:
            Path to the created pipeline JSON file.
        """
        enriched_nodes = []
        for node in nodes:
            node_type = node.get("type", "method")

            if node_type in SYSTEM_NODE_TYPES:
                # System nodes have no method script or contracts
                enriched = {
                    "id": node["id"],
                    "type": node_type,
                    "method": "",
                    "module": "",
                    "params": node.get("params", {}),
                }
                # Pass through system-node-specific fields
                for key in ("samples", "run_id", "output_slot", "output_path", "fan_mode"):
                    if key in node:
                        enriched[key] = node[key]
                enriched_nodes.append(enriched)
                continue

            method_name = node["method"]
            script_path = f"methods/{method_name}/{method_name}.py"

            # Read method.yaml for slot_outputs
            slot_outputs = {}
            slot_types = {}
            method_yaml_dir = project_dir / "methods" / method_name
            if method_yaml_dir.exists():
                from wfc.contracts import parse_method_yaml
                contract = parse_method_yaml(method_yaml_dir)
                if contract:
                    for slot_name, slot_spec in contract.get("outputs", {}).items():
                        slot_type = (
                            slot_spec.get("type", "csv")
                            if isinstance(slot_spec, dict) else "csv"
                        )
                        slot_outputs[slot_name] = f"{slot_name}.csv"
                        slot_types[slot_name] = slot_type

            enriched = {
                "id": node["id"],
                "method": method_name,
                "module": node.get("module", "test_pipeline"),
                "script": script_path,
                "params": node.get("params", {}),
                "slot_outputs": slot_outputs,
                "slot_types": slot_types,
                "env": node.get("env", "inherit"),
            }

            enriched_nodes.append(enriched)

        pipeline = {
            "nodes": enriched_nodes,
            "links": links,
            "samples": samples,
        }
        if param_sets:
            pipeline["param_sets"] = param_sets

        pipeline_path = project_dir / f"pipeline_{name}.json"
        pipeline_path.write_text(json.dumps(pipeline, indent=2))
        return pipeline_path

    return _create_pipeline
