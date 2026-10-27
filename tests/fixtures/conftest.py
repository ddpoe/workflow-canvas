"""
Shared fixture method infrastructure for pipeline tests.

Provides reusable fixtures for registering lightweight test methods
and building pipeline JSON files from topology descriptions, plus the
consolidated fakes the fixtures install.

Fixtures:
  - register_fixture_methods: Registers lightweight fixture methods
                   (transform, merge, faulty) in a test project.
  - pipeline_factory: Builds pipeline JSON files from topology descriptions.
                   Supports both method nodes and system nodes
                   (input_selector, run_reference).
  - register_imaging_methods / imaging_pipeline_factory: the same pair for
                   the imaging fixture methods.
  - processes:     Starts real sleeping child processes (a live pipeline
                   owner or engine) and kills any still running at teardown.

Helpers:
  - fixture_env_blob: The content-blob hash of the fixture env a project
                   registers (``tmp_project`` does).
  - ended_process: The identity of a process that ran and was killed (a
                   gone owner or engine).
  - wait_until_gone: Wait until psutil no longer lists a killed process
                   (Windows keeps it listed briefly after ``wait()``).

Fakes:
  - stub_readiness_probes: The one readiness-probe fake, taking a status
                   per probe (``None`` leaves a probe real).
  - mocked_snakemake: The one stand-in for a Snakemake invocation (the
                   process spawn and the Snakefile emitter).

The production-path builders (``init_test_project``, ``register_test_method``,
``register_sample_row``, ``create_sample_csv``, ``write_env_record``,
``run_cli`` and the two out-of-tree path helpers) live in
the ``tests/fixtures/routes/`` package and are re-exported from here so every existing
import path keeps working.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wfc import layout

# Re-export the routes so ``from tests.fixtures.conftest import ...`` keeps
# working at every existing call site.
from tests.fixtures.routes import (  # noqa: F401
    create_sample_csv,
    init_test_project,
    project_archive_dir,
    register_sample_row,
    register_test_method,
    run_cli,
    sample_source_dir,
    write_env_record,
)
# Two registry fakes, re-exported so ``from tests.fixtures.conftest import
# stub_readiness_probes`` works.
from tests.fixtures.fakes import mocked_snakemake, stub_readiness_probes  # noqa: F401


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
# method.yaml files and the pipeline nodes emitted by pipeline_factory both
# declare ``env: fixture-env``, resolving to the one .wfc/envs.json record
# written by register_fixture_methods.
FIXTURE_ENV_NAME = "fixture-env"


def fixture_env_blob(project: Path) -> str:
    """Return the content-blob hash of the fixture env registered in *project*.

    ``tmp_project`` registers the fixture env with the project, so its
    content blob is a blob row like any other env's: pending until a push
    worker tick pushes it. A test that counts or pushes blob rows meets it.

    Args:
        project: A project whose env manifest holds the fixture env.

    Returns:
        The env's ``env_fingerprint``, the md5 of its content blob.
    """
    from wfc.environments import get

    return get(FIXTURE_ENV_NAME, project).env_fingerprint


@pytest.fixture
def processes():
    """Start sleeping child processes; every one still running is killed at teardown.

    A real process stands for a live pipeline owner or engine: the pipeline
    record names processes by pid and creation time, and the cancel kills an
    engine's process tree only when both match.

    Yields:
        ``start()`` -> a live ``Popen``.
    """
    started: list[subprocess.Popen] = []

    def start() -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        started.append(proc)
        return proc

    yield start
    for proc in started:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def ended_process(start):
    """The identity of a process that ran and has been killed (a gone owner or engine).

    Args:
        start: The ``processes`` fixture's ``start``.

    Returns:
        The ``ProcessIdentity`` the process had while it ran.
    """
    from wfc.execution.pipeline_record import process_identity

    proc = start()
    identity = process_identity(proc.pid)
    proc.kill()
    proc.wait()
    wait_until_gone(proc.pid)
    return identity


def wait_until_gone(pid: int, timeout: float = 5.0) -> None:
    """Wait until psutil no longer sees a killed and waited-on process.

    Windows keeps a pid listed for a few milliseconds after ``wait()``
    returns, so a probe made at once can still read the process as alive.

    Args:
        pid: The process id.
        timeout: Seconds to wait before failing the test.
    """
    from wfc.execution.pipeline_record import process_identity

    deadline = time.monotonic() + timeout
    while process_identity(pid).created is not None:
        assert time.monotonic() < deadline, (
            f"process {pid} is still listed {timeout} s after it was killed and waited on")
        time.sleep(0.01)


@pytest.fixture
def register_fixture_methods(git_project, fixture_container_image, monkeypatch):
    """Register lightweight fixture methods bound to a built container env.

    execution is container-only. The fixture methods
    (transform, merge, faulty) declare ``env: fixture-env`` and run
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

    from wfc.persistence import reset_engine

    # Initialise the project and write the container-env manifest BEFORE the
    # first register_test_method call: register_method -> check_method_env reads
    # the manifest to validate the method's env.
    init_test_project(tmp_path)
    # The fixture image is a locally built docker image attached by digest —
    # a byo registration in production terms (raw docker build, no pixi/conda
    # source). Its python:3.11-slim base has `python` on PATH, which is the
    # byo interpreter default.
    write_env_record(tmp_path, FIXTURE_ENV_NAME, digest=fixture_container_image)
    reset_engine()

    # Registration copies each method from its source (the checked-in
    # fixture directory, outside the project) into the module-qualified
    # snapshot a run executes.
    for method_name, module_name in FIXTURE_METHODS:
        register_test_method(
            project_dir=tmp_path,
            module_name=module_name,
            method_dir=FIXTURE_METHODS_DIR / method_name,
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
        project_dir: Project root whose registered snapshots
            (``.wfc/registry/methods/<module>/<method>/``) hold the contracts.
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
            # Real Canvas exports (compile.ts) omit method/module on system
            # nodes entirely — JSON.stringify drops the undefined fields. The
            # run-step crash shape is the key ABSENT, not an empty string, so
            # the fixture must leave them out to match production output.
            enriched = {
                "id": node["id"],
                "type": node_type,
                "params": node.get("params", {}),
            }
            for key in ("samples", "run_id", "fan_mode"):
                if key in node:
                    enriched[key] = node[key]
            enriched_nodes.append(enriched)
            continue

        method_name = node["method"]
        module_name = node.get("module", default_module)
        # A run executes its method version's registered copy; the document
        # names no script (the validator refuses a node `script`).
        snapshot = layout.method_dir(Path(project_dir), module_name, method_name)

        slot_outputs = {}
        slot_types = {}
        method_yaml_dir = snapshot
        if method_yaml_dir.exists():
            from wfc.contracts import parse_method_yaml
            contract = parse_method_yaml(method_yaml_dir)
            if contract:
                for slot_name, slot_spec in contract.get("outputs", {}).items():
                    slot_type = (
                        slot_spec.get("type", "csv")
                        if isinstance(slot_spec, dict) else "csv"
                    )
                    # A node may name the published filename explicitly;
                    # the declared filename (not the slot name) is what
                    # the collect phase scans for.
                    slot_outputs[slot_name] = node.get("slot_outputs", {}).get(
                        slot_name, f"{slot_name}.csv"
                    )
                    slot_types[slot_name] = slot_type

        enriched = {
            "id": node["id"],
            "method": method_name,
            "module": module_name,
            "params": node.get("params", {}),
            "slot_outputs": slot_outputs,
            "slot_types": slot_types,
            # bind to the built container env so run-step's
            # _envs_get lookup finds the digest-pinned image record.
            "env": node.get("env", FIXTURE_ENV_NAME),
        }
        # Canvas label passthrough — run-step stamps Run.nid from it and the
        # cancelled-rows walk keys rows by it.
        if "label" in node:
            enriched["label"] = node["label"]
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
    method.yaml files declare ``env: fixture-env`` so registration
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

    from wfc.persistence import reset_engine

    init_test_project(tmp_path)
    # The fixture image is a locally built docker image attached by digest —
    # a byo registration in production terms (raw docker build, no pixi/conda
    # source). Its python:3.11-slim base has `python` on PATH, which is the
    # byo interpreter default.
    write_env_record(tmp_path, FIXTURE_ENV_NAME, digest=fixture_container_image)
    reset_engine()

    for method_name, module_name in IMAGING_METHODS:
        register_test_method(
            project_dir=tmp_path,
            module_name=module_name,
            method_dir=IMAGING_METHODS_DIR / method_name,
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
