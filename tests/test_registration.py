"""
Subsystem Test: Method Registration

Validates that pm_mvp can register method scripts via the production
register_module() + register_method() code path.  Covers:

  - Flat method dirs (methods/emit/, methods/transform/, etc.)
  - AST scanner extraction of @wfc_method-decorated functions
  - Module → method linkage in the database
  - TrackedFunction + ParamDef rows populated from AST scan
  - Re-registration (idempotent upsert) without duplicating rows

Uses lightweight fixture methods from tests/fixtures/methods/ (transform,
merge, faulty).

These are Tier 2 tests: @workflow(purpose=...), no Step markers.
They test a meaningful subsystem (registration) but aren't product stories.
"""

import shutil
import sys
# unittest.mock no longer needed — shared env system uses config validation
# instead of subprocess mocking

import pytest
from pathlib import Path

from sqlmodel import select

from axiom_annotations import workflow, Step

from wfc.init import init_project
from wfc.register import register_module, register_method
from wfc.database import get_session
from wfc.models import Module, Method, TrackedFunction, ParamDef, ModuleContract


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Module definitions for fixture methods ────────────────────────────────

L2_MODULES = {
    "data_generation": {
        "description": "Methods that generate or emit data",
        "contracts": [
            {"type": "output", "name": "output", "value_type": "csv", "required": True},
        ],
    },
    "data_transform": {
        "description": "Methods that transform input data",
        "contracts": [
            {"type": "output", "name": "output", "value_type": "csv", "required": True},
        ],
    },
    "data_merge": {
        "description": "Methods that merge multiple inputs",
        "contracts": [],
    },
}

# ── Method → module mapping & script locations ────────────────────────────

# Each entry: (module_name, method_dir_relative_to_project_root, expected_function_name)
L2_METHODS = [
    # Flat fixture methods (methods/<name>/<name>.py)
    ("data_transform", "methods/transform", "main"),
    ("data_merge", "methods/merge", "main"),
]


# ============================================================================
# Test: Register all L2 modules
# ============================================================================

@workflow(
    purpose="Register all five L2 modules with contracts and verify DB state")
def test_register_l2_modules(tmp_project):
    """Register each L2 module with its output and metric contracts,
    then verify Module and ModuleContract rows exist in the DB."""
    init_project(tmp_project)

    module_ids = {}
    for mod_name, mod_def in L2_MODULES.items():
        mid = register_module(
            name=mod_name,
            contracts=mod_def["contracts"],
            description=mod_def["description"])
        module_ids[mod_name] = mid
        assert mid is not None

    # Verify all modules exist
    with get_session() as session:
        modules = session.exec(select(Module)).all()
        module_names = {m.name for m in modules}
        assert module_names == set(L2_MODULES.keys())

        # Verify contracts for a module with contracts
        gen_module = session.exec(
            select(Module).where(Module.name == "data_generation")
        ).first()
        contracts = session.exec(
            select(ModuleContract).where(
                ModuleContract.module_id == gen_module.id
            )
        ).all()
        assert len(contracts) == 1
        assert contracts[0].name == "output"

        # data_merge has no contracts
        merge_module = session.exec(
            select(Module).where(Module.name == "data_merge")
        ).first()
        merge_contracts = session.exec(
            select(ModuleContract).where(
                ModuleContract.module_id == merge_module.id
            )
        ).all()
        assert len(merge_contracts) == 0


# ============================================================================
# Test: Register all L2 method scripts (flat + nested)
# ============================================================================

@workflow(
    purpose="Register all fixture method scripts via AST scan and verify "
            "Method, TrackedFunction, and ParamDef rows are created")
def test_register_l2_methods(tmp_project):
    """Full registration cycle: modules → methods → verify DB."""
    init_project(tmp_project)

    # Register modules first (methods need a parent module)
    for mod_name, mod_def in L2_MODULES.items():
        register_module(
            name=mod_name,
            contracts=mod_def["contracts"],
            description=mod_def["description"])

    # Register all method scripts
    # With the shared env system, methods default to 'inherit' unless
    # method.yaml declares an env key -- no _install_env needed.
    registered = {}
    for module_name, method_dir_rel, expected_func in L2_METHODS:
        method_dir = tmp_project / method_dir_rel
        assert method_dir.exists(), f"Method dir not found: {method_dir}"
        expected_script = f"{method_dir.name}.py"
        assert (method_dir / expected_script).exists(), (
            f"{expected_script} not found in {method_dir}"
        )

        method_id = register_method(
            method_dir=method_dir,
            module_name=module_name)
        registered[method_dir.name] = (method_id, expected_func)
        assert method_id is not None

    # Verify all methods in DB
    with get_session() as session:
        methods = session.exec(select(Method)).all()
        assert len(methods) == len(L2_METHODS)

        for method in methods:
            # Each method has at least one tracked function
            tfs = session.exec(
                select(TrackedFunction).where(
                    TrackedFunction.method_id == method.id
                )
            ).all()
            assert len(tfs) >= 1, f"Method '{method.name}' has no tracked functions"

            # The main tracked function matches expected name
            method_id, expected_func = registered[method.name]
            main_func = tfs[0]  # ordinal=1 is the @wfc_method function
            assert main_func.function_name == expected_func, (
                f"Method '{method.name}': expected function '{expected_func}', "
                f"got '{main_func.function_name}'"
            )


# ============================================================================
# Test: AST scanner extracts @wfc_method functions with correct params
# ============================================================================

@workflow(
    purpose="Verify AST scanner handles @wfc_method parameter stripping correctly")
def test_ast_scanner_extracts_params(tmp_project):
    """The AST scanner strips 'inputs' and 'params' from @wfc_method functions.
    filter_data(inputs, params) → 0 tracked params.
    merge_data(inputs, params) → 0 tracked params."""
    init_project(tmp_project)
    register_module(name="test_tools", contracts=[], description="Test")

    # Create inline @wfc_method scripts for testing AST scanning
    filter_dir = tmp_project / "methods" / "filter_data"
    filter_dir.mkdir(parents=True, exist_ok=True)
    (filter_dir / "method.yaml").write_text(
        "env: container:docker://local/x@sha256:" + "a" * 64 + "\n"
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  result:\n    type: .csv\n"
    )
    (filter_dir / "filter_data.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        "def filter_data(ctx):\n"
        "    out = ctx.run_dir / 'result.csv'\n"
        "    out.write_text('x')\n"
        "    ctx.save_artifact('result', out)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )

    merge_dir = tmp_project / "methods" / "merge_data"
    merge_dir.mkdir(parents=True, exist_ok=True)
    (merge_dir / "method.yaml").write_text(
        "env: container:docker://local/x@sha256:" + "a" * 64 + "\n"
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  result:\n    type: .csv\n"
    )
    (merge_dir / "merge_data.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        "def merge_data(ctx):\n"
        "    out = ctx.run_dir / 'result.csv'\n"
        "    out.write_text('x')\n"
        "    ctx.save_artifact('result', out)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )

    # filter_data(inputs, params) — both stripped by @wfc_method rule
    register_method(
        method_dir=filter_dir,
        module_name="test_tools")
    # merge_data(inputs, params) — both stripped by @wfc_method rule
    register_method(
        method_dir=merge_dir,
        module_name="test_tools")

    with get_session() as session:
        # filter_data: (df, params) both stripped → 0 ParamDefs
        filter_method = session.exec(
            select(Method).where(Method.name == "filter_data")
        ).first()
        filter_tf = session.exec(
            select(TrackedFunction).where(
                TrackedFunction.method_id == filter_method.id
            )
        ).first()
        filter_params = session.exec(
            select(ParamDef).where(
                ParamDef.tracked_function_id == filter_tf.id
            )
        ).all()
        assert len(filter_params) == 0, (
            f"filter_data should have 0 tracked params (df/params stripped), "
            f"got {[p.param_name for p in filter_params]}"
        )

        # merge_data: (inputs, params) — both stripped by @wfc_method rule
        merge_method = session.exec(
            select(Method).where(Method.name == "merge_data")
        ).first()
        merge_tf = session.exec(
            select(TrackedFunction).where(
                TrackedFunction.method_id == merge_method.id
            )
        ).first()
        merge_params = session.exec(
            select(ParamDef).where(
                ParamDef.tracked_function_id == merge_tf.id
            )
        ).all()
        assert len(merge_params) == 0, (
            f"merge_data should have 0 tracked params (inputs/params stripped), "
            f"got {[p.param_name for p in merge_params]}"
        )


# ============================================================================
# Test: Re-registration is idempotent
# ============================================================================

@workflow(
    purpose="Re-registering a method updates rows without creating duplicates")
def test_reregistration_idempotent(tmp_project):
    """Register the same method twice — second call should update,
    not duplicate rows."""
    init_project(tmp_project)

    register_module(name="data_transform", contracts=[], description="Test")
    method_dir = tmp_project / "methods" / "transform"

    # First registration
    id1 = register_method(method_dir=method_dir, module_name="data_transform")

    # Second registration (idempotent upsert)
    id2 = register_method(method_dir=method_dir, module_name="data_transform")

    assert id1 == id2

    # Only one Method row exists
    with get_session() as session:
        methods = session.exec(
            select(Method).where(Method.name == "transform")
        ).all()
        assert len(methods) == 1

        # Only one set of TrackedFunction rows (not doubled)
        tfs = session.exec(
            select(TrackedFunction).where(
                TrackedFunction.method_id == methods[0].id
            )
        ).all()
        func_names = [tf.function_name for tf in tfs]
        # No duplicates
        assert len(func_names) == len(set(func_names))


# ============================================================================
# Test: Nested method dir registration resolves correct script_path
# ============================================================================

@workflow(
    purpose="Nested method directories produce correct script_path in DB")
def test_nested_method_script_path(tmp_project):
    """Methods under modules/<module>/<method>/ should store a
    script_path that points to the actual {method_name}.py."""
    init_project(tmp_project)

    register_module(
        name="nested_module",
        contracts=[],
        description="Test nested")

    # Create a nested method directory inline
    nested_dir = tmp_project / "modules" / "nested_module" / "nested_method"
    nested_dir.mkdir(parents=True, exist_ok=True)
    (nested_dir / "method.yaml").write_text(
        "env: container:docker://local/x@sha256:" + "a" * 64 + "\n"
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  result:\n    type: .csv\n"
    )
    (nested_dir / "nested_method.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        "def nested_method(ctx):\n"
        "    out = ctx.run_dir / 'result.csv'\n"
        "    out.write_text('x')\n"
        "    ctx.save_artifact('result', out)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )

    mid = register_method(
        method_dir=nested_dir,
        module_name="nested_module")

    with get_session() as session:
        method = session.exec(
            select(Method).where(Method.name == "nested_method")
        ).first()
        assert method is not None

        # script_path should be a relative path to {method_name}.py
        # (relative to cwd, which is tmp_project)
        script_rel = method.script_path
        assert "nested_method" in script_rel
        assert script_rel.endswith("nested_method.py")

        # The path should actually resolve to a file
        full_path = tmp_project / script_rel
        assert full_path.exists(), (
            f"script_path '{script_rel}' does not resolve from project root"
        )


# ============================================================================
# Test: Module contracts round-trip
# ============================================================================

@workflow(
    purpose="Module contracts are stored and queryable for validation")
def test_module_contracts_roundtrip(tmp_project):
    """Register a module with contracts, query them back, verify
    they match the original definitions."""
    init_project(tmp_project)

    contracts_in = [
        {"type": "output", "name": "model", "value_type": "model", "required": True},
        {"type": "output", "name": "predictions", "value_type": "csv", "required": True},
        {"type": "metric", "name": "mcc", "value_type": "float", "required": True},
    ]

    mid = register_module(
        name="test_module",
        description="Round-trip test",
        contracts=contracts_in)

    with get_session() as session:
        contracts_out = session.exec(
            select(ModuleContract).where(ModuleContract.module_id == mid)
        ).all()

        assert len(contracts_out) == 3

        by_name = {c.name: c for c in contracts_out}
        assert by_name["model"].contract_type == "output"
        assert by_name["model"].value_type == "model"
        assert by_name["model"].required is True
        assert by_name["mcc"].contract_type == "metric"
        assert by_name["mcc"].value_type == "float"


# ============================================================================
# Test: contracts argument is required (cannot be omitted)
# ============================================================================

@workflow(
    purpose="register_module raises TypeError when contracts is not supplied, "
            "and accepts an empty list as a valid explicit choice")
def test_register_module_contract_required(tmp_project):
    """contracts is a required argument — omitting it is a hard error.
    Passing [] is valid: it explicitly declares the module has no contracts."""
    init_project(tmp_project)

    口 = Step(step_num=1, name="Verify omitting contracts raises",
             purpose="Confirm that calling register_module without contracts is a TypeError")
    with pytest.raises(TypeError, match="contracts"):
        register_module(name="no_contract_module", description="missing contracts arg")  # type: ignore[call-arg]

    口 = Step(step_num=2, name="Verify empty list is accepted",
             purpose="Confirm that contracts=[] is valid — zero contracts is a deliberate choice")
    mid = register_module(name="empty_contract_module", contracts=[], description="no contracts")
    assert mid is not None

    with get_session() as session:
        contracts = session.exec(
            select(ModuleContract).where(ModuleContract.module_id == mid)
        ).all()
        assert len(contracts) == 0, (
            f"Expected 0 contracts for empty-list registration, got {len(contracts)}"
        )


# ============================================================================
# Parametrized: Each L2 script is individually registrable
# ============================================================================

@pytest.mark.parametrize(
    "module_name,method_dir_rel,expected_func",
    L2_METHODS,
    ids=[m[2] for m in L2_METHODS],  # test IDs from function names
)
@workflow(
    purpose="Individual L2 method script registers successfully via AST scan")
def test_individual_method_registration(
    tmp_project, module_name, method_dir_rel, expected_func):
    """Register a single L2 method and verify the tracked function name."""
    init_project(tmp_project)
    register_module(name=module_name, contracts=[], description="test")

    method_dir = tmp_project / method_dir_rel
    method_id = register_method(
        method_dir=method_dir,
        module_name=module_name)
    assert method_id is not None

    with get_session() as session:
        tfs = session.exec(
            select(TrackedFunction).where(
                TrackedFunction.method_id == method_id
            )
        ).all()
        func_names = [tf.function_name for tf in tfs]
        assert expected_func in func_names, (
            f"Expected '{expected_func}' in tracked functions, "
            f"got {func_names}"
        )


# ============================================================================
# Test: env persisted during registration (shared env system)
# ============================================================================

@workflow(
    purpose="ADR-019 Cycle H: registering a method stores its declared built "
            "container env in the database")
def test_register_method_stores_inherit_for_plain_dir(tmp_project):
    """The fixture transform method declares env: container:fixture-env ->
    method.env stores that container env in the DB (no 'inherit' default)."""
    init_project(tmp_project)

    _ = Step(step_num=1, name="Register module and method",
             purpose="Register the fixture transform method (env: container:fixture-env)")
    register_module(name="data_transform", contracts=[], description="Test")
    register_method(
        method_dir=tmp_project / "methods" / "transform",
        module_name="data_transform")

    _ = Step(step_num=2, name="Verify stored environment",
             purpose="Confirm the container env is recorded in the method's database entry")
    with get_session() as session:
        method = session.exec(
            select(Method).where(Method.name == "transform")
        ).first()
        assert method is not None
        # tmp_project's conftest writes a fixture-env record; the fixture
        # method.yaml declares env: container:fixture-env.
        assert method.env == "container:fixture-env"


@workflow(
    purpose="Registering a method with env: image-io in method.yaml stores the "
            "named env in the database when pixi env directory exists")
def test_register_method_stores_named_env(tmp_project):
    """method.yaml with env: image-io -> method.env == 'image-io' in DB."""
    _ = Step(step_num=1, name="Set up pixi env config and fake env directory",
             purpose="Create project config with pixi root and a matching env with python")
    import sys as _sys
    wfc_dir = tmp_project / ".wfc"
    wfc_dir.mkdir(exist_ok=True)
    pixi_root = tmp_project / ".pixi"
    env_dir = pixi_root / "image-io-abc123" / "envs" / "default"
    if _sys.platform == "win32":
        python = env_dir / "Scripts" / "python.exe"
    else:
        python = env_dir / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("fake python")
    toml_path = str(wfc_dir / "wfc.db").replace("\\", "/")
    (wfc_dir / "wf-canvas.toml").write_text(
        f'[database]\nurl = "sqlite:///{toml_path}"\n\n'
        f'[project]\nname = "test"\n\n'
        '[pixi]\nroot = ".pixi"\n'
    )

    _ = Step(step_num=2, name="Create method with named env",
             purpose="Write a method.yaml that declares env: pixi:image-io")
    method_dir = tmp_project / "methods" / "env_method"
    method_dir.mkdir(parents=True)
    (method_dir / "method.yaml").write_text(
        "env: container:docker://local/image-io@sha256:" + "a" * 64 + "\n"
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  result:\n    type: .csv\n"
    )
    (method_dir / "env_method.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        "def env_method(ctx):\n"
        "    out = ctx.run_dir / 'result.csv'\n"
        "    out.write_text('x')\n"
        "    ctx.save_artifact('result', out)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )

    _ = Step(step_num=3, name="Register module and method",
             purpose="Register the method with the named env")
    register_module(name="env_module", contracts=[], description="Test env")
    register_method(
        method_dir=method_dir,
        module_name="env_module")

    _ = Step(step_num=4, name="Verify stored environment",
             purpose="Confirm the named env is recorded in the method's database entry")
    with get_session() as session:
        method = session.exec(
            select(Method).where(Method.name == "env_method")
        ).first()
        assert method is not None
        assert method.env == "container:docker://local/image-io@sha256:" + "a" * 64


# =============================================================================
# Container env (ADR-019): register_method end-to-end (US-4)
# =============================================================================

# A valid 64-hex sha256 digest used to compose well-formed container refs.
_CONTAINER_VALID_DIGEST = "a" * 64
_CONTAINER_VALID_REF = (
    f"docker://ghcr.io/dante/image-io@sha256:{_CONTAINER_VALID_DIGEST}"
)


def _write_envs_manifest(project_dir: Path, envs: dict) -> None:
    """Write a minimal .wfc/envs.json manifest under *project_dir*."""
    import json
    (project_dir / ".wfc").mkdir(parents=True, exist_ok=True)
    (project_dir / ".wfc" / "envs.json").write_text(
        json.dumps({"schema_version": 1, "envs": envs}, indent=2)
    )


def _container_env_record_dict(container: str = _CONTAINER_VALID_REF) -> dict:
    """Return a record dict shaped like a valid .wfc/envs.json envs[name] VALUE."""
    return {
        "backend": "pixi",
        "source": "pixi.toml",
        "container": container,
        "env_fingerprint": "deadbeef" * 8,
        "built_from_lock": "pixi.lock",
        "built_at": "2026-05-16T00:00:00Z",
    }


def _make_container_method_dir(tmp_project: Path, env_value: str) -> Path:
    """Create a method directory whose method.yaml declares ``env: <env_value>``.

    Uses the same inline @wfc_method script shape as the neighboring named-env
    tests, so the AST scanner is exercised on a realistic script.
    """
    method_dir = tmp_project / "methods" / "container_method"
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "method.yaml").write_text(
        f"env: {env_value}\n"
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  result:\n    type: .csv\n"
    )
    (method_dir / "container_method.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        "def container_method(ctx):\n"
        "    out = ctx.run_dir / 'result.csv'\n"
        "    out.write_text('x')\n"
        "    ctx.save_artifact('result', out)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )
    return method_dir


@workflow(
    purpose="register_method accepts env: container:<envname> when the env "
            "is present in .wfc/envs.json with a digest-pinned container ref, "
            "and persists 'container:<envname>' as Method.env (US-4 happy path "
            "through the full register_method integration path)"
)
def test_register_method_resolves_container_env(tmp_project):
    """End-to-end: register_method must resolve env: container:image-io
    via the .wfc/envs.json manifest. The image is NOT pulled at registration
    time (ADR-019 #8) — only the manifest record's shape is validated."""
    _ = Step(step_num=1, name="Init project and write envs manifest",
             purpose="Create .wfc/envs.json with one digest-pinned 'image-io' record")
    init_project(tmp_project)
    _write_envs_manifest(tmp_project, envs={"image-io": _container_env_record_dict()})

    _ = Step(step_num=2, name="Create method that declares container env",
             purpose="method.yaml has env: container:image-io")
    method_dir = _make_container_method_dir(tmp_project, "container:image-io")

    _ = Step(step_num=3, name="Register module and method",
             purpose="register_method must succeed; Method.env == 'container:image-io'")
    register_module(name="container_module", contracts=[], description="Test")
    method_id = register_method(
        method_dir=method_dir,
        module_name="container_module",
    )
    assert method_id is not None

    with get_session() as session:
        method = session.exec(
            select(Method).where(Method.name == "container_method")
        ).first()
        assert method is not None
        assert method.env == "container:image-io"


@workflow(
    purpose="register_method FAILS with a clear error when env: container:<name> "
            "references an env that is absent from .wfc/envs.json (US-4 absent-env)"
)
def test_register_method_container_env_missing_from_manifest(tmp_project):
    _ = Step(step_num=1, name="Init project WITHOUT envs manifest",
             purpose="No .wfc/envs.json on disk — every container:<name> lookup misses")
    init_project(tmp_project)

    _ = Step(step_num=2, name="Create method referencing absent container env",
             purpose="method.yaml declares env: container:image-io but manifest is empty")
    method_dir = _make_container_method_dir(tmp_project, "container:image-io")

    _ = Step(step_num=3, name="Attempt registration",
             purpose="Must raise ValueError pointing at .wfc/envs.json")
    register_module(name="container_module", contracts=[], description="Test")
    with pytest.raises(ValueError, match=r"not found in \.wfc/envs\.json"):
        register_method(
            method_dir=method_dir,
            module_name="container_module",
        )


@workflow(
    purpose="register_method FAILS when env: container:<name> references an "
            "env whose 'container' field is a floating tag — the digest-pin "
            "rule must be enforced through the register_method path, not "
            "just on direct refs (US-4 floating-tag rejection)"
)
def test_register_method_container_env_floating_tag_rejected(tmp_project):
    _ = Step(step_num=1, name="Init project and write envs manifest with floating tag",
             purpose="image-io.container ends in :latest, NOT @sha256:...")
    init_project(tmp_project)
    _write_envs_manifest(tmp_project, envs={
        "image-io": _container_env_record_dict(
            container="docker://ghcr.io/dante/image-io:latest",
        ),
    })

    _ = Step(step_num=2, name="Create method referencing the floating-tag env",
             purpose="method.yaml declares env: container:image-io")
    method_dir = _make_container_method_dir(tmp_project, "container:image-io")

    _ = Step(step_num=3, name="Attempt registration",
             purpose="Must raise ValueError mentioning digest-pinned")
    register_module(name="container_module", contracts=[], description="Test")
    with pytest.raises(ValueError, match="digest-pinned"):
        register_method(
            method_dir=method_dir,
            module_name="container_module",
        )


@workflow(
    purpose="register_method accepts env: container:docker://...@sha256:... "
            "as a direct ref WITHOUT requiring an entry in .wfc/envs.json "
            "(US-4 direct-ref escape hatch, ADR-019 decision #12)"
)
def test_register_method_container_direct_ref_bypasses_manifest(tmp_project):
    _ = Step(step_num=1, name="Init project, no envs manifest written",
             purpose="The direct-ref path must work even when .wfc/envs.json is absent")
    init_project(tmp_project)

    _ = Step(step_num=2, name="Create method with direct digest-pinned ref",
             purpose="method.yaml declares env: container:docker://...@sha256:...")
    env_value = f"container:{_CONTAINER_VALID_REF}"
    method_dir = _make_container_method_dir(tmp_project, env_value)

    _ = Step(step_num=3, name="Register module and method",
             purpose="register_method must succeed; Method.env equals the full direct ref")
    register_module(name="container_module", contracts=[], description="Test")
    method_id = register_method(
        method_dir=method_dir,
        module_name="container_module",
    )
    assert method_id is not None

    with get_session() as session:
        method = session.exec(
            select(Method).where(Method.name == "container_method")
        ).first()
        assert method is not None
        assert method.env == env_value


@workflow(
    purpose="Registering a method that references a nonexistent env raises ValueError")
def test_register_method_missing_env_raises(tmp_project):
    """method.yaml with env: nonexistent -> raises ValueError at registration."""
    _ = Step(step_num=1, name="Set up config without matching pixi env",
             purpose="Create a project config with pixi root but no matching env directory")
    wfc_dir = tmp_project / ".wfc"
    wfc_dir.mkdir(exist_ok=True)
    pixi_root = tmp_project / ".pixi"
    pixi_root.mkdir()
    toml_path = str(wfc_dir / "wfc.db").replace("\\", "/")
    (wfc_dir / "wf-canvas.toml").write_text(
        f'[database]\nurl = "sqlite:///{toml_path}"\n\n'
        f'[project]\nname = "test"\n\n'
        '[pixi]\nroot = ".pixi"\n'
    )

    _ = Step(step_num=2, name="Create method referencing missing env",
             purpose="Write a method.yaml that declares an env not found in pixi root")
    method_dir = tmp_project / "methods" / "missing_env_method"
    method_dir.mkdir(parents=True)
    (method_dir / "method.yaml").write_text(
        "env: container:nonexistent\n"
        "inputs:\n  data:\n    type: .csv\n"
    )
    (method_dir / "missing_env_method.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        "def missing_env_method(ctx):\n"
        "    pass\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )

    _ = Step(step_num=3, name="Attempt registration",
             purpose="Verify that registration fails with a clear error")
    register_module(name="missing_env_module", contracts=[], description="Test")
    with pytest.raises(ValueError, match="not found in .wfc/envs.json|wfc register-env"):
        register_method(
            method_dir=method_dir,
            module_name="missing_env_module")


@workflow(
    purpose="register_method rejects a method.yaml with no input slots"
)
def test_register_method_no_inputs_raises(tmp_project):
    """method.yaml with inputs: {} -> raises ValueError at registration."""
    method_dir = tmp_project / "methods" / "no_inputs"
    method_dir.mkdir(parents=True)
    (method_dir / "method.yaml").write_text(
        "env: container:docker://local/x@sha256:" + "a" * 64 + "\n"
        "inputs: {}\n"
        "outputs:\n  result:\n    type: .csv\n"
    )
    (method_dir / "no_inputs.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        "def no_inputs(ctx):\n"
        "    out = ctx.run_dir / 'result.csv'\n"
        "    out.write_text('x')\n"
        "    ctx.save_artifact('result', out)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )

    register_module(name="no_inputs_module", contracts=[], description="Test")
    with pytest.raises(ValueError, match="no input slots"):
        register_method(
            method_dir=method_dir,
            module_name="no_inputs_module")


# =============================================================================
# Git integration
# =============================================================================

@workflow(
    purpose="register_method creates a git commit so the method code version is captured in the cache key"
)
def test_register_method_auto_commits(tmp_project):
    口 = Step(
        step_num=1,
        name="Scaffold project with git",
        purpose="init_project with init_git=True creates a git repo and initial commit")
    from wfc.init import init_project
    init_project(tmp_project, init_git=True)

    口 = Step(
        step_num=2,
        name="Register module and method",
        purpose="register_method must stage the method dir and commit to the project repo")
    method_dir = tmp_project / "methods" / "transform"
    register_module(name="data_transform", contracts=[], description="test")
    register_method(method_dir=method_dir, module_name="data_transform")

    口 = Step(
        step_num=3,
        name="Assert commit exists",
        purpose="git log must contain a commit message referencing the registered method")
    import subprocess
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(tmp_project),
        capture_output=True,
        text=True)
    assert "data_transform/transform" in log.stdout, (
        f"Expected a git commit for 'data_transform/transform' but got:\n{log.stdout}"
    )


@workflow(
    purpose="register_method raises RuntimeError when the project has no git repository"
)
def test_register_method_raises_if_no_git_repo(tmp_project):
    口 = Step(
        step_num=1,
        name="Scaffold project without git",
        purpose="init_project without init_git=True leaves no .git directory")
    from wfc.init import init_project
    import shutil
    import os
    import stat

    def _force_remove(func, path, exc):
        """On Windows, git objects are read-only — make them writable first."""
        os.chmod(path, stat.S_IWRITE)
        func(path)

    # tmp_project sits inside a git_project (already a git repo). Scaffold,
    # then remove the .git directory so register_method exercises its
    # "not a git repository" error path.
    init_project(tmp_project, init_git=False)
    shutil.rmtree(tmp_project / ".git", onexc=_force_remove)

    口 = Step(
        step_num=2,
        name="Register module then attempt register_method",
        purpose="register_method must raise RuntimeError, not silently succeed with broken caching")
    method_dir = tmp_project / "methods" / "transform"
    register_module(name="data_transform", contracts=[], description="test")
    with pytest.raises(RuntimeError, match="not a git repository"):
        register_method(method_dir=method_dir, module_name="data_transform")


# =============================================================================
# Module YAML: parse_module_yaml + register_module from file
# =============================================================================

@workflow(
    purpose="parse_module_yaml reads contracts and description from module.yaml")
def test_parse_module_yaml(tmp_project):
    """module.yaml with contracts and description is parsed correctly."""
    from wfc.contracts import parse_module_yaml

    _ = Step(step_num=1, name="Create module.yaml",
             purpose="Write a module.yaml with contracts and description")
    mod_dir = tmp_project / "modules" / "test_mod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "module.yaml").write_text(
        "description: Test module for validation\n"
        "contracts:\n"
        "  - type: output\n"
        "    name: predictions\n"
        "    value_type: csv\n"
        "    required: true\n"
        "  - type: metric\n"
        "    name: accuracy\n"
        "    value_type: float\n"
    )

    _ = Step(step_num=2, name="Parse module.yaml",
             purpose="Verify parsed output matches expected structure")
    result = parse_module_yaml(mod_dir)
    assert result is not None
    assert result["description"] == "Test module for validation"
    assert len(result["contracts"]) == 2
    assert result["contracts"][0]["type"] == "output"
    assert result["contracts"][0]["name"] == "predictions"
    assert result["contracts"][0]["value_type"] == "csv"
    assert result["contracts"][0]["required"] is True
    assert result["contracts"][1]["type"] == "metric"
    assert result["contracts"][1]["name"] == "accuracy"
    assert result["contracts"][1]["required"] is True  # default


@workflow(
    purpose="parse_module_yaml returns None when no module.yaml exists")
def test_parse_module_yaml_missing(tmp_project):
    """Missing module.yaml returns None, not an error."""
    from wfc.contracts import parse_module_yaml

    mod_dir = tmp_project / "modules" / "no_yaml_mod"
    mod_dir.mkdir(parents=True)

    result = parse_module_yaml(mod_dir)
    assert result is None


@workflow(
    purpose="register_module loads contracts from module.yaml when --contracts is omitted")
def test_register_module_from_yaml(tmp_project):
    """register_module with module_dir reads module.yaml for contracts."""
    init_project(tmp_project)

    _ = Step(step_num=1, name="Create module with module.yaml",
             purpose="Set up a module directory with a module.yaml file")
    mod_dir = tmp_project / "modules" / "yaml_mod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "module.yaml").write_text(
        "description: Module from YAML\n"
        "contracts:\n"
        "  - type: output\n"
        "    name: result\n"
        "    value_type: csv\n"
        "    required: true\n"
    )

    _ = Step(step_num=2, name="Register module from YAML",
             purpose="register_module reads contracts from module.yaml")
    mid = register_module(name="yaml_mod", module_dir=mod_dir)
    assert mid is not None

    _ = Step(step_num=3, name="Verify contracts in DB",
             purpose="Confirm module contracts were loaded from YAML")
    with get_session() as session:
        module = session.exec(
            select(Module).where(Module.name == "yaml_mod")
        ).first()
        assert module is not None
        assert module.description == "Module from YAML"

        contracts = session.exec(
            select(ModuleContract).where(ModuleContract.module_id == mid)
        ).all()
        assert len(contracts) == 1
        assert contracts[0].contract_type == "output"
        assert contracts[0].name == "result"


@workflow(
    purpose="register_module raises TypeError when neither contracts nor module.yaml is provided")
def test_register_module_requires_contracts(tmp_project):
    """Omitting both contracts and module_dir raises TypeError."""
    init_project(tmp_project)

    with pytest.raises(TypeError, match="contracts"):
        register_module(name="no_contracts_mod")


@workflow(
    purpose="register_method validates method outputs against module's required output contracts")
def test_register_method_validates_module_contract(tmp_project):
    """Method missing a required module output raises ValueError at registration."""
    init_project(tmp_project)

    _ = Step(step_num=1, name="Register module with required output contract",
             purpose="Create a module that requires a 'predictions' output")
    register_module(
        name="strict_mod",
        contracts=[
            {"type": "output", "name": "predictions", "value_type": "csv", "required": True},
            {"type": "metric", "name": "accuracy", "value_type": "float", "required": True},
        ],
        description="Module with required outputs")

    _ = Step(step_num=2, name="Create method missing required output",
             purpose="Write a method.yaml that does not declare the required 'predictions' output")
    method_dir = tmp_project / "methods" / "bad_method"
    method_dir.mkdir(parents=True)
    (method_dir / "method.yaml").write_text(
        "env: container:docker://local/x@sha256:" + "a" * 64 + "\n"
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  wrong_output:\n    type: .csv\n"
    )
    (method_dir / "bad_method.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        "def bad_method(ctx):\n"
        "    out = ctx.run_dir / 'wrong_output.csv'\n"
        "    out.write_text('x')\n"
        "    ctx.save_artifact('wrong_output', out)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )

    _ = Step(step_num=3, name="Attempt registration",
             purpose="Verify registration fails with missing output error")
    with pytest.raises(ValueError, match="missing required module output"):
        register_method(method_dir=method_dir, module_name="strict_mod")
