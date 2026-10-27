"""
Subsystem Test: Method Registration

Validates that wfc registers method scripts via the production
register_module() + register_method(assume_yes=True) code path.  Covers:

  - Flat method dirs (methods/transform/, methods/merge/)
  - AST scanner extraction of @wfc_method-decorated functions
  - Module → method linkage in the database
  - TrackedFunction + ParamDef rows populated from AST scan
  - Re-registration (idempotent upsert) without duplicating rows

Uses lightweight fixture methods from tests/fixtures/methods/ (transform,
merge).

Tier 2 tests (@workflow(purpose=...)); the multi-phase flows are Tier 3 and
add Step markers.
"""

import shutil
import sys

import pytest
from pathlib import Path

from sqlmodel import select

from axiom_annotations import workflow, Step

from wfc.init import init_project
from wfc.registration import register_module, register_method
from wfc.persistence import get_session, Module, Method, TrackedFunction, ParamDef, ModuleContract

from tests.fixtures.conftest import write_env_record


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
    purpose="Register the L2 modules with contracts and verify DB state")
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
# Test: the contract map
# ============================================================================

@workflow(
    purpose="The contract map carries one entry per registered method, keyed "
            "<module>.<method>, holding one contract per version keyed by "
            "its content identity; the newest version is named and its "
            "slots and env are what an unpinned node reads")
def test_load_contract_map_covers_every_registered_method(tmp_project):
    """The one read Contracts' enrichment and Graph's load take as a value.

    A second registration that changes the outputs makes v2; the map names
    v2 as newest and still holds v1's contract under v1's identity.
    """
    from wfc.registration import load_contract_map, read_version_list

    init_project(tmp_project)
    src_dir = tmp_project / "workspace" / "cm_method"
    src_dir.mkdir(parents=True)
    (src_dir / "cm_method.py").write_text("def main():\n    return 1\n")
    spec = ("inputs:\n  table:\n    type: .csv\n"
            "outputs:\n  summary:\n    type: .csv\n"
            "params: {}\nexecutor: python\n"
            "env: fixture-env\n")
    (src_dir / "method.yaml").write_text(spec)
    register_module(name="cm_contracted", contracts=[], description="contracted")
    register_method(method_dir=src_dir, module_name="cm_contracted", assume_yes=True)
    (src_dir / "method.yaml").write_text(spec.replace("summary:", "report:"))
    register_method(method_dir=src_dir, module_name="cm_contracted", assume_yes=True)

    with get_session() as session:
        contract_map = load_contract_map(session)
        registered = session.exec(
            select(Method).where(Method.name == "cm_method")
        ).one()
        env = registered.env

    assert set(contract_map) == {"cm_contracted.cm_method"}
    entry = contract_map["cm_contracted.cm_method"]
    assert set(entry["input_slots"]) == {"table"}
    assert set(entry["output_slots"]) == {"report"}
    assert entry["env"] == env == "fixture-env"
    v1, v2 = read_version_list(tmp_project, "cm_contracted", "cm_method")
    assert (entry["version"], entry["label"]) == (v2.identity, 2)
    assert set(entry["versions"]) == {v1.identity, v2.identity}
    assert set(entry["versions"][v1.identity]["output_slots"]) == {"summary"}
    assert entry["versions"][v1.identity]["label"] == 1
    assert set(entry["versions"][v2.identity]["output_slots"]) == {"report"}


def test_an_env_is_used_by_the_methods_whose_newest_version_records_it(tmp_project):
    """The answer comes from each method's newest list entry; a method
    with no list is skipped rather than guessed from its row."""
    from wfc import layout
    from wfc.registration import methods_whose_newest_version_uses_env

    init_project(tmp_project)
    register_module(name="env_mod", contracts=[])
    for name in ("first", "second"):
        src = tmp_project / "workspace" / name
        src.mkdir(parents=True)
        (src / f"{name}.py").write_text("def main():\n    return 1\n")
        (src / "method.yaml").write_text(
            "inputs:\n  table:\n    type: .csv\nenv: fixture-env\n")
        register_method(method_dir=src, module_name="env_mod", assume_yes=True)

    assert methods_whose_newest_version_uses_env("fixture-env") == [
        "env_mod/first", "env_mod/second"]
    assert methods_whose_newest_version_uses_env("other-env") == []
    layout.method_versions_path(tmp_project, "env_mod", "second").unlink()
    assert methods_whose_newest_version_uses_env("fixture-env") == ["env_mod/first"]


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
            module_name=module_name, assume_yes=True).method_id
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
        "env: fixture-env\n"
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
        "env: fixture-env\n"
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
        module_name="test_tools", assume_yes=True)
    # merge_data(inputs, params) — both stripped by @wfc_method rule
    register_method(
        method_dir=merge_dir,
        module_name="test_tools", assume_yes=True)

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
    id1 = register_method(method_dir=method_dir, module_name="data_transform", assume_yes=True).method_id

    # Second registration (idempotent upsert)
    id2 = register_method(method_dir=method_dir, module_name="data_transform", assume_yes=True).method_id

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
# Snapshot, commit and version: registration is the ingest boundary
# ============================================================================

def _outside_source(tmp_project: Path, method: str, tag: str) -> Path:
    """Copy a fixture method's source to a directory outside the project."""
    source = tmp_project.parent / f"{tmp_project.name}-{tag}" / method
    shutil.copytree(tmp_project / "methods" / method, source)
    return source


def _git(tmp_project: Path, *args: str) -> str:
    import subprocess
    return subprocess.run(["git", *args], cwd=tmp_project, check=True,
                          capture_output=True, text=True).stdout.strip()


@workflow(
    purpose="Two modules register a same-named method from different sources "
            "into two snapshots with two fingerprints, and neither "
            "registration overwrites the other's snapshot")
def test_same_method_name_in_two_modules_registers_two_snapshots(tmp_project):
    """Module-qualified snapshots: a method is named within its module."""
    from wfc import layout
    from wfc.persistence import MethodVersion

    register_module(name="first_mod", contracts=[], description="Test")
    register_module(name="second_mod", contracts=[], description="Test")
    first_source = _outside_source(tmp_project, "transform", "first")
    second_source = _outside_source(tmp_project, "transform", "second")
    second_script = second_source / "transform.py"
    second_script.write_text(second_script.read_text() + "\n# second module's variant\n")

    first_id = register_method(method_dir=first_source, module_name="first_mod", assume_yes=True).method_id
    second_id = register_method(method_dir=second_source, module_name="second_mod", assume_yes=True).method_id
    assert first_id != second_id

    first_snapshot = layout.method_dir(tmp_project, "first_mod", "transform")
    second_snapshot = layout.method_dir(tmp_project, "second_mod", "transform")
    assert ((first_snapshot / "transform.py").read_bytes()
            == (first_source / "transform.py").read_bytes())
    assert (second_snapshot / "transform.py").read_bytes() == second_script.read_bytes()

    with get_session() as session:
        versions = session.exec(select(MethodVersion)).all()
    fingerprints = {v.method_id: v.code_fingerprint for v in versions}
    assert set(fingerprints) == {first_id, second_id}
    assert fingerprints[first_id] != fingerprints[second_id]

    # Re-registering the first module's method leaves the second's alone.
    before = {p.relative_to(second_snapshot).as_posix(): p.read_bytes()
              for p in second_snapshot.rglob("*") if p.is_file()}
    register_method(method_dir=first_source, module_name="first_mod", assume_yes=True)
    after = {p.relative_to(second_snapshot).as_posix(): p.read_bytes()
             for p in second_snapshot.rglob("*") if p.is_file()}
    assert after == before


@workflow(
    purpose="Registration mints the version carrying its own commit, which "
            "names only the snapshot and the method's version list; "
            "re-registering identical code makes no version and no commit")
def test_registration_mints_the_version_with_its_commit(tmp_project):
    from wfc import layout
    from wfc.persistence import MethodVersion

    register_module(name="data_transform", contracts=[], description="Test")
    source = _outside_source(tmp_project, "transform", "src")

    method_id = register_method(method_dir=source, module_name="data_transform", assume_yes=True).method_id
    registration_commit = _git(tmp_project, "rev-parse", "HEAD")
    committed = _git(tmp_project, "show", "--name-only", "--format=", "HEAD").splitlines()
    snapshot_rel = layout.method_dir(Path("."), "data_transform", "transform").as_posix()
    list_rel = layout.method_versions_path(
        Path("."), "data_transform", "transform").as_posix()
    assert list_rel in committed
    assert all(name.startswith(snapshot_rel + "/") or name == list_rel
               for name in committed)

    with get_session() as session:
        versions = session.exec(
            select(MethodVersion).where(MethodVersion.method_id == method_id)
        ).all()
    assert [v.git_commit for v in versions] == [registration_commit]

    register_method(method_dir=source, module_name="data_transform", assume_yes=True)
    assert _git(tmp_project, "rev-parse", "HEAD") == registration_commit
    with get_session() as session:
        again = session.exec(
            select(MethodVersion).where(MethodVersion.method_id == method_id)
        ).all()
    assert [(v.id, v.git_commit) for v in again] == [(versions[0].id, registration_commit)]


def test_registration_names_the_files_it_did_not_copy(tmp_project, capsys):
    """Only scripts, method.yaml and declared helpers enter the snapshot."""
    from wfc import layout

    register_module(name="data_transform", contracts=[], description="Test")
    source = _outside_source(tmp_project, "transform", "src")
    (source / "genes.csv").write_text("gene\nA\n")
    (source / "notes.md").write_text("notes\n")
    (source / "__pycache__").mkdir(exist_ok=True)
    (source / "__pycache__" / "stale.cpython-312.pyc").write_bytes(b"0")

    capsys.readouterr()
    registration = register_method(method_dir=source, module_name="data_transform", assume_yes=True)
    out = capsys.readouterr().out

    assert registration.not_copied == ("genes.csv", "notes.md")
    advice = next(line for line in out.splitlines() if "Not registered" in line)
    assert advice.strip() == registration.advice
    assert ("Not registered (only scripts, `method.yaml` and declared helpers "
            "are copied):") in advice
    assert "`genes.csv`" in advice and "`notes.md`" in advice
    assert "stale.cpython-312.pyc" not in advice and "__pycache__" not in advice
    assert "register it as a sample and wire it as an input" in advice
    assert "please open an issue: https://" in advice

    snapshot = layout.method_dir(tmp_project, "data_transform", "transform")
    assert not (snapshot / "genes.csv").exists()
    assert not (snapshot / "notes.md").exists()


def test_a_snapshot_path_git_ignores_is_refused(tmp_project):
    """No version row for a snapshot the registration commit cannot hold."""
    from wfc import layout
    from wfc.persistence import MethodVersion

    gitignore = tmp_project / ".gitignore"
    gitignore.write_text(gitignore.read_text() + "\n.wfc/registry/\n")
    register_module(name="data_transform", contracts=[], description="Test")
    source = _outside_source(tmp_project, "transform", "src")

    with pytest.raises(ValueError, match="git would not commit") as exc:
        register_method(method_dir=source, module_name="data_transform", assume_yes=True)
    assert "transform" in str(exc.value)

    with get_session() as session:
        assert session.exec(select(MethodVersion)).all() == []
        assert session.exec(select(Method)).all() == []
    assert not layout.method_dir(tmp_project, "data_transform", "transform").exists()


# ============================================================================
# Test: Nested method dir registration resolves correct script_path
# ============================================================================

@workflow(
    purpose="Nested method directories produce correct script_path in DB")
def test_nested_method_script_path(tmp_project):
    """Methods under modules/<module>/<method>/ should store a
    script_path that points to the actual {method_name}.py."""
    from wfc import layout
    init_project(tmp_project)

    register_module(
        name="nested_module",
        contracts=[],
        description="Test nested")

    # Create a nested method directory inline
    nested_dir = tmp_project / "modules" / "nested_module" / "nested_method"
    nested_dir.mkdir(parents=True, exist_ok=True)
    (nested_dir / "method.yaml").write_text(
        "env: fixture-env\n"
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
        module_name="nested_module", assume_yes=True).method_id

    with get_session() as session:
        method = session.exec(
            select(Method).where(Method.name == "nested_method")
        ).first()
        assert method is not None

        # script_path is relative to the method's snapshot and names the
        # copied {method_name}.py there.
        script_rel = method.script_path
        assert script_rel == "nested_method.py"
        snapshot = layout.method_dir(tmp_project, "nested_module", "nested_method")
        assert (snapshot / script_rel).is_file()


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
        module_name=module_name, assume_yes=True).method_id
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
    purpose="registering a method stores its declared built "
            "container env in the database")
def test_register_method_stores_inherit_for_plain_dir(tmp_project):
    """The fixture transform method declares env: fixture-env ->
    method.env stores that container env in the DB."""
    init_project(tmp_project)

    _ = Step(step_num=1, name="Register module and method",
             purpose="Register the fixture transform method (env: fixture-env)")
    register_module(name="data_transform", contracts=[], description="Test")
    register_method(
        method_dir=tmp_project / "methods" / "transform",
        module_name="data_transform", assume_yes=True)

    _ = Step(step_num=2, name="Verify stored environment",
             purpose="Confirm the container env is recorded in the method's database entry")
    with get_session() as session:
        method = session.exec(
            select(Method).where(Method.name == "transform")
        ).first()
        assert method is not None
        # tmp_project's conftest writes a fixture-env record; the fixture
        # method.yaml declares env: fixture-env.
        assert method.env == "fixture-env"


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

    write_env_record(tmp_project, "image-io")

    _ = Step(step_num=2, name="Create method with named env",
             purpose="Write a method.yaml that declares env: image-io")
    method_dir = tmp_project / "methods" / "env_method"
    method_dir.mkdir(parents=True)
    (method_dir / "method.yaml").write_text(
        "env: image-io\n"
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
        module_name="env_module", assume_yes=True)

    _ = Step(step_num=4, name="Verify stored environment",
             purpose="Confirm the named env is recorded in the method's database entry")
    with get_session() as session:
        method = session.exec(
            select(Method).where(Method.name == "env_method")
        ).first()
        assert method is not None
        assert method.env == "image-io"


# =============================================================================
# Container env: register_method end-to-end
# =============================================================================

# A valid 64-hex sha256 digest used to compose well-formed container refs.
_CONTAINER_VALID_DIGEST = "a" * 64


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
    purpose="register_method accepts env: <envname> when the env "
            "is present in .wfc/envs.json with a digest-pinned container ref, "
            "and persists '<envname>' as Method.env (happy path "
            "through the full register_method integration path)"
)
def test_register_method_resolves_container_env(tmp_project):
    """End-to-end: register_method must resolve env: image-io
    via the .wfc/envs.json manifest. The image is NOT pulled at registration
    time — only the manifest record's shape is validated."""
    _ = Step(step_num=1, name="Init project and write envs manifest",
             purpose="Create .wfc/envs.json with one digest-pinned 'image-io' record")
    init_project(tmp_project)
    write_env_record(tmp_project, "image-io", backend="pixi",
                     digest=_CONTAINER_VALID_DIGEST)

    _ = Step(step_num=2, name="Create method that declares container env",
             purpose="method.yaml has env: image-io")
    method_dir = _make_container_method_dir(tmp_project, "image-io")

    _ = Step(step_num=3, name="Register module and method",
             purpose="register_method must succeed; Method.env == 'image-io'")
    register_module(name="container_module", contracts=[], description="Test")
    method_id = register_method(
        method_dir=method_dir,
        module_name="container_module",
        assume_yes=True,
    ).method_id
    assert method_id is not None

    with get_session() as session:
        method = session.exec(
            select(Method).where(Method.name == "container_method")
        ).first()
        assert method is not None
        assert method.env == "image-io"


@workflow(
    purpose="register_method FAILS with a clear error when env: <name> "
            "references an env that is absent from .wfc/envs.json (absent-env)"
)
def test_register_method_container_env_missing_from_manifest(tmp_project):
    _ = Step(step_num=1, name="Init project WITHOUT envs manifest",
             purpose="No .wfc/envs.json on disk — every env-name lookup misses")
    init_project(tmp_project)

    _ = Step(step_num=2, name="Create method referencing absent container env",
             purpose="method.yaml declares env: image-io but manifest is empty")
    method_dir = _make_container_method_dir(tmp_project, "image-io")

    _ = Step(step_num=3, name="Attempt registration",
             purpose="Must raise ValueError pointing at .wfc/envs.json")
    register_module(name="container_module", contracts=[], description="Test")
    with pytest.raises(ValueError, match=r"not found in \.wfc/envs\.json"):
        register_method(
            method_dir=method_dir,
            module_name="container_module",
            assume_yes=True,
        )


@workflow(
    purpose="register_method FAILS when env: <name> references an "
            "env whose 'container' field is a floating tag — the digest-pin "
            "rule must be enforced through the register_method path "
            "(floating-tag rejection)"
)
def test_register_method_container_env_floating_tag_rejected(tmp_project):
    _ = Step(step_num=1, name="Init project and write envs manifest with floating tag",
             purpose="image-io.container ends in :latest, NOT @sha256:...")
    init_project(tmp_project)
    # Deliberately-INVALID record, hand-written on purpose: the container ref
    # carries a floating tag, a shape production register() can never write
    # (validate_container_ref rejects it before the manifest write). The raw
    # dict stages the corrupt state this test exists to reject downstream.
    import json
    (tmp_project / ".wfc" / "envs.json").write_text(json.dumps({
        "schema_version": 3,
        "envs": {
            "image-io": {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": "docker://ghcr.io/dante/image-io:latest",
                "env_fingerprint": "deadbeef" * 4,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-05-16T00:00:00Z",
            }
        },
    }, indent=2))

    _ = Step(step_num=2, name="Create method referencing the floating-tag env",
             purpose="method.yaml declares env: image-io")
    method_dir = _make_container_method_dir(tmp_project, "image-io")

    _ = Step(step_num=3, name="Attempt registration",
             purpose="Must raise ValueError mentioning digest-pinned")
    register_module(name="container_module", contracts=[], description="Test")
    with pytest.raises(ValueError, match="digest-pinned"):
        register_method(
            method_dir=method_dir,
            module_name="container_module",
            assume_yes=True,
        )


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
        "env: nonexistent\n"
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
            module_name="missing_env_module", assume_yes=True)


@workflow(
    purpose="register_method rejects a method.yaml with no input slots"
)
def test_register_method_no_inputs_raises(tmp_project):
    """method.yaml with inputs: {} -> raises ValueError at registration."""
    method_dir = tmp_project / "methods" / "no_inputs"
    method_dir.mkdir(parents=True)
    (method_dir / "method.yaml").write_text(
        "env: fixture-env\n"
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
            module_name="no_inputs_module", assume_yes=True)


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
    register_method(method_dir=method_dir, module_name="data_transform", assume_yes=True)

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
        register_method(method_dir=method_dir, module_name="data_transform", assume_yes=True)


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
        "env: fixture-env\n"
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
        register_method(method_dir=method_dir, module_name="strict_mod", assume_yes=True)
