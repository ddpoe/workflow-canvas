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
IMAGING_METHODS_DIR = Path(__file__).resolve().parent / "methods_imaging"

# Fixture method definitions: (method_name, module_name)
FIXTURE_METHODS = [
    ("transform", "test_pipeline"),
    ("merge", "test_pipeline"),
    ("faulty", "test_pipeline"),
]

# Imaging marquee fixture methods (7-node skip-link DAG). Module "imaging".
# build_config is the root (reads the seeded manifest via input_selector);
# stitch/quantify/export_final are the skip-link fan-in nodes whose completed
# scripts tag each row by source_slot.
IMAGING_METHODS = [
    ("build_config", "imaging"),
    ("tile_export", "imaging"),
    ("illum_correct", "imaging"),
    ("stitch", "imaging"),
    ("segment", "imaging"),
    ("quantify", "imaging"),
    ("export_final", "imaging"),
]

# System node types that do not require a "method" key or script
SYSTEM_NODE_TYPES = {"input_selector", "run_reference"}


# Bare name of the container env the fixture methods bind to. The fixture
# method.yaml files declare ``env: container:fixture-env``; the pipeline nodes
# emitted by pipeline_factory declare ``env: fixture-env`` (bare). Both resolve
# to the same .wfc/envs.json record written by register_fixture_methods.
FIXTURE_ENV_NAME = "fixture-env"

# Tag of the locally-built fixture image (matches tests/conftest.py).
FIXTURE_IMAGE_REPO = "local/wfc-test-minimal"


def _write_fixture_env_manifest(project_dir: Path, image_digest: str) -> None:
    """Write the ``fixture-env`` container record into ``.wfc/envs.json``.

    The record's digest-pinned ``container`` ref lets run-step dispatch each
    fixture method into the session-scoped image, and lets register_method's
    ``_resolve_env`` validation pass.

    Args:
        project_dir: Project root (already initialised, has ``.wfc/``).
        image_digest: Bare sha256 hex of the locally-built fixture image.
    """
    wfc_dir = project_dir / ".wfc"
    wfc_dir.mkdir(parents=True, exist_ok=True)
    container_ref = f"docker://{FIXTURE_IMAGE_REPO}@sha256:{image_digest}"
    (wfc_dir / "envs.json").write_text(json.dumps({
        "schema_version": 1,
        "envs": {
            FIXTURE_ENV_NAME: {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": container_ref,
                "env_fingerprint": image_digest,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-06-23T00:00:00Z",
                # The fixture image is plain python:3.11-slim (python on
                # PATH); record the interpreter explicitly so dispatch does
                # not fall back to the pixi-backend default path, which does
                # not exist in that image.
                "python": "python",
            }
        },
    }))


@pytest.fixture
def register_fixture_methods(git_project, fixture_container_image, monkeypatch):
    """Register lightweight fixture methods bound to a built container env.

    ADR-019 Cycle H: execution is container-only. The fixture methods
    (transform, merge, faulty) declare ``env: container:fixture-env`` and run
    inside a session-scoped image built from ``tests/fixtures/Dockerfile.minimal``
    (the ``fixture_container_image`` fixture). This fixture writes the
    ``fixture-env`` record into ``.wfc/envs.json``, then registers each method
    through the production path (:func:`register_test_method`).

    Because it depends on ``fixture_container_image`` (a Docker build), every
    test that uses this fixture must be marked ``integration`` +
    ``requires_docker`` — the default ``pytest`` run deselects ``integration``,
    so no image build is triggered there.

    Returns:
        The git_project path with all fixture methods registered.
    """
    tmp_path = git_project
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))

    db_path = tmp_path / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from wfc.database import reset_engine
    from wfc.init import init_project

    # Initialise the project and write the container-env manifest BEFORE the
    # first register_test_method call: register_method -> _resolve_env reads
    # the manifest to validate the method's env.
    init_project(tmp_path)
    _write_fixture_env_manifest(tmp_path, fixture_container_image)
    reset_engine()

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


def build_pipeline_json(
    project_dir: Path,
    name: str,
    nodes: list[dict],
    links: list[dict],
    samples: list[str],
    param_sets: dict | None = None,
    default_module: str = "test_pipeline",
) -> Path:
    """Create an enriched pipeline JSON file from a topology description.

    Shared by both ``pipeline_factory`` (fixture methods) and
    ``imaging_pipeline_factory`` (imaging methods). Method nodes are enriched
    with script paths and slot_outputs read from the registered method.yaml
    contracts on disk; system nodes (input_selector, run_reference) pass through
    their type-specific fields and need no script.

    Args:
        project_dir: Project root holding ``methods/{name}/`` dirs.
        name: Pipeline name (used for filename).
        nodes: Node dicts. Method nodes require id, method, module.
        links: Link dicts (source, target, optional target_slot/source_slot).
        samples: Sample name list.
        param_sets: Optional param_sets dict.
        default_module: Module assumed when a method node omits ``module``.

    Returns:
        Path to the created pipeline JSON file.
    """
    enriched_nodes = []
    for node in nodes:
        node_type = node.get("type", "method")

        if node_type in SYSTEM_NODE_TYPES:
            enriched = {
                "id": node["id"],
                "type": node_type,
                "method": "",
                "module": "",
                "params": node.get("params", {}),
            }
            for key in ("samples", "run_id", "output_slot", "output_path", "fan_mode"):
                if key in node:
                    enriched[key] = node[key]
            enriched_nodes.append(enriched)
            continue

        method_name = node["method"]
        script_path = f"methods/{method_name}/{method_name}.py"

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
            "module": node.get("module", default_module),
            "script": script_path,
            "params": node.get("params", {}),
            "slot_outputs": slot_outputs,
            "slot_types": slot_types,
            # ADR-019 Cycle H: bind to the built container env so run-step's
            # _envs_get lookup finds the digest-pinned image record.
            "env": node.get("env", FIXTURE_ENV_NAME),
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
        return build_pipeline_json(
            project_dir, name, nodes, links, samples, param_sets,
            default_module="test_pipeline",
        )

    return _create_pipeline


@pytest.fixture
def register_imaging_methods(git_project, fixture_container_image, monkeypatch):
    """Register the 7 imaging marquee methods bound to the built container env.

    Mirrors :func:`register_fixture_methods` but installs the imaging skip-link
    DAG (build_config -> tile_export -> illum_correct -> stitch -> segment ->
    quantify -> export_final) from ``tests/fixtures/methods_imaging/``. The
    method.yaml files declare ``env: container:fixture-env`` so registration
    validates against the manifest written here; pipeline nodes use the bare
    ``fixture-env`` name.

    Like ``register_fixture_methods`` it depends on ``fixture_container_image``
    (a Docker build), so every test using it must be marked ``integration`` +
    ``requires_docker``.

    Returns:
        The git_project path with all imaging methods registered.
    """
    tmp_path = git_project
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))
    db_path = tmp_path / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from wfc.database import reset_engine
    from wfc.init import init_project

    init_project(tmp_path)
    _write_fixture_env_manifest(tmp_path, fixture_container_image)
    reset_engine()

    for method_name, module_name in IMAGING_METHODS:
        src_dir = IMAGING_METHODS_DIR / method_name
        dest_dir = tmp_path / "methods" / method_name
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.copytree(
            src_dir, dest_dir,
            ignore=shutil.ignore_patterns("__pycache__", "__init__.py"),
        )
        register_test_method(
            project_dir=tmp_path,
            module_name=module_name,
            method_dir=dest_dir,
            method_name=method_name,
        )

    yield tmp_path

    reset_engine()


@pytest.fixture
def imaging_pipeline_factory(register_imaging_methods):
    """Factory that builds pipeline JSON for the imaging marquee DAG.

    Identical surface to :func:`pipeline_factory` but bound to
    ``register_imaging_methods`` (module ``imaging``).
    """
    project_dir = register_imaging_methods

    def _create_pipeline(
        name: str,
        nodes: list[dict],
        links: list[dict],
        samples: list[str],
        param_sets: dict | None = None,
    ) -> Path:
        return build_pipeline_json(
            project_dir, name, nodes, links, samples, param_sets,
            default_module="imaging",
        )

    return _create_pipeline
