"""Registry-tab HTTP endpoints in wfc.canvas.routes.registry.

Covers:
  - GET  /api/registry/modules     (response shape)
  - GET  /api/registry/methods     (the method rows and their versions)
  - POST /api/registry/samples     (DvcNotConfiguredError -> 409)
  - POST /api/registry/modules     (?dryRun=true does not persist)
  - POST /api/registry/methods     (?dryRun=true states the plan, writes
    nothing; the Register POST applies it)
  - method detail (files, the script first, and contract; path traversal
    refused) and the version export zip
  - filesystem browse (project-root listing; traversal refused)
  - environments list and packages endpoints
"""

from __future__ import annotations

from tests.conftest import pin_project_root
from tests.fixtures.fakes import stub_registry_seam

from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc import layout
from sqlmodel import Session, create_engine, select

from wfc.persistence import Method, Module
from wfc.registration import (
    MalformedMethodRecordError,
    resolve_method_code,
)
from tests.fixtures.routes import (
    build_project_snapshot,
    canvas_client,
    restore_project_snapshot,
)
from tests.harness import ModuleSpec, Scenario, node

REGISTRY_MODULE = "preprocessing"
REGISTRY_DESCRIPTION = "Tile export + normalization."
# The env every method binds to. The env tests write their own manifest
# naming it, so the list row's ``spec`` is this bare name as registration
# stored it -- ``Method.env`` is the method.yaml value verbatim, and the
# ``container:`` prefix is only the legacy read side of the env grammar.
REGISTRY_ENV = "demo"


@pytest.fixture(scope="module")
def registry_project(tmp_path_factory):
    """The registry tab's project -- one module, two methods -- built once by registration.

    The module's description and its two module-level contracts are
    declared on the scenario and reach the rows through production's
    ``register_module``; the methods' contracts are what registration
    derived from the generated method directories. Both methods declare
    the module's required output, because registration refuses a method
    under a module whose required outputs it does not produce.

    Yields:
        The ``ProjectSnapshot``: the built project, its ``DATABASE_URL``,
        the live database file and the pristine copy.
    """
    from wfc.persistence import reset_engine

    root = tmp_path_factory.mktemp("canvas_registry_project")
    scenario = Scenario(
        nodes=[node("tile_export", module=REGISTRY_MODULE,
                    outputs={"expression_matrix": ".h5ad"}),
               node("normalize", module=REGISTRY_MODULE,
                    outputs={"expression_matrix": ".h5ad"})],
        env_name=REGISTRY_ENV,
        modules={REGISTRY_MODULE: ModuleSpec(
            description=REGISTRY_DESCRIPTION,
            contracts=(
                {"type": "output", "name": "expression_matrix",
                 "value_type": ".h5ad", "required": True},
                {"type": "metric", "name": "batch_effect_score",
                 "value_type": "float", "required": True},
            ),
        )},
    )
    snapshot = build_project_snapshot(scenario, root)
    yield snapshot
    reset_engine()


@pytest.fixture
def db_engine(registry_project, monkeypatch):
    """A pristine copy of the harness-built database, pinned for one test."""
    from wfc.persistence import reset_engine

    restore_project_snapshot(registry_project, monkeypatch)

    engine = create_engine(registry_project.database_url)
    yield engine
    engine.dispose()
    reset_engine()


@pytest.fixture
def client(db_engine, registry_project, monkeypatch):
    """FastAPI test client over the harness-built project ``db_engine`` pinned."""
    return canvas_client(registry_project.project.root, monkeypatch)


# =============================================================================
# T1: GET /api/registry/modules — response shape
# =============================================================================

def test_get_registry_modules_shape(client):
    """Response carries name, description, contracts, methods count, source."""
    resp = client.get("/api/registry/modules")
    assert resp.status_code == 200

    body = resp.json()
    assert "modules" in body
    assert len(body["modules"]) == 1

    mod = body["modules"][0]
    assert mod["name"] == REGISTRY_MODULE
    assert mod["description"] == REGISTRY_DESCRIPTION
    assert mod["methods"] == 2
    assert mod["source"] == "modules/preprocessing/module.yaml"

    # Contracts: ModuleContract.contract_type -> "type" in response.
    contracts = mod["contracts"]
    assert len(contracts) == 2
    by_name = {c["name"]: c for c in contracts}
    assert by_name["expression_matrix"] == {
        "type": "output",
        "name": "expression_matrix",
        "value_type": ".h5ad",
        "required": True,
    }
    assert by_name["batch_effect_score"]["type"] == "metric"
    assert by_name["batch_effect_score"]["value_type"] == "float"


# =============================================================================
# T2: GET /api/registry/methods — one row per method
# =============================================================================

def test_get_registry_methods_lists_each_method(client, registry_project):
    """Each registered method is a row: its module, env, runs and registered copy."""
    resp = client.get("/api/registry/methods")
    assert resp.status_code == 200

    body = resp.json()
    assert "methods" in body
    assert len(body["methods"]) == 2

    by_name = {m["name"]: m for m in body["methods"]}
    tile = by_name["tile_export"]

    assert tile["module"] == REGISTRY_MODULE
    # The env registration copied from the method's own method.yaml: the
    # bare env name the scenario declared, which is the only form the
    # contract parser accepts.
    assert tile["env"] == registry_project.project.env_name
    assert tile["runCount"] == 0
    assert tile["source"] == (
        f".wfc/registry/methods/{REGISTRY_MODULE}/tile_export/method.yaml")


# =============================================================================
# T5: POST /api/registry/samples — DvcNotConfiguredError maps to 409
# =============================================================================

def test_post_registry_samples_dvc_not_configured_returns_409(
    client, tmp_path, monkeypatch
):
    """`DvcNotConfiguredError` from wfc.registration.register_sample -> HTTP 409."""
    from wfc.storage import DvcNotConfiguredError

    def fake_register_sample(*args, **kwargs):
        raise DvcNotConfiguredError(
            "Project has no [dvc] section in wf-canvas.toml"
        )

    stub_registry_seam(monkeypatch, "_register_sample_fn", fake_register_sample)

    resp = client.post(
        "/api/registry/samples",
        json={"name": "CFPAC_ERKi", "source": str(tmp_path / "missing.csv")},
    )
    assert resp.status_code == 409
    assert "dvc" in resp.json()["detail"].lower()


# =============================================================================
# T6: POST /api/registry/modules?dryRun=true — pre-checks only, no persist
# =============================================================================

def test_post_registry_modules_dryrun_does_not_persist(
    client, db_engine, monkeypatch
):
    """?dryRun=true returns preChecks without calling register_module()."""
    called = {"n": 0}

    def fake_register_module(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("register_module must NOT be called in dryRun mode")

    stub_registry_seam(monkeypatch, "_register_module_fn", fake_register_module)

    resp = client.post(
        "/api/registry/modules?dryRun=true",
        json={
            "name": "new_module_that_does_not_exist",
            "description": "proposed",
            "contracts": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "preChecks" in body
    assert called["n"] == 0

    # Also verify: the module was NOT inserted into the DB.
    with Session(db_engine) as session:
        found = session.exec(
            select(Module).where(Module.name == "new_module_that_does_not_exist")
        ).first()
        assert found is None


# =============================================================================
# T7: GET /api/registry/methods/{module}/{method}/detail — files + contract
# =============================================================================

def test_method_detail_returns_files_and_contract(client, registry_project):
    """Detail lists the registered copy's files and the parsed contract."""
    code = resolve_method_code(REGISTRY_MODULE, "tile_export",
                               registry_project.project.root)

    resp = client.get("/api/registry/methods/preprocessing/tile_export/detail")
    assert resp.status_code == 200

    body = resp.json()
    by_name = {f["name"]: f for f in body["files"]}
    assert set(by_name) == {p.name for p in code.code_dir.iterdir()
                            if p.is_file()}
    # The main script opens first, though method.yaml sorts before it.
    assert body["files"][0]["name"] == code.script.name
    assert sorted(by_name) == ["method.yaml", code.script.name]
    assert by_name["method.yaml"]["language"] == "yaml"
    assert by_name[code.script.name]["language"] == "python"
    assert by_name[code.script.name]["content"] == code.script.read_text(
        encoding="utf-8")

    contract = body["contract"]
    assert set(contract["output_slots"]) == {"expression_matrix"}
    assert "input_slots" in contract
    assert "params_schema" in contract


# =============================================================================
# Versions: the listing, the detail and the export name a version
# =============================================================================

VERSIONED = "versioned"


def _row_commits() -> dict[int, str | None]:
    """The registration commit each ``MethodVersion`` row records, by label."""
    from wfc.persistence import MethodVersion, get_session
    with get_session() as s:
        return {v.label: v.git_commit for v in s.exec(select(MethodVersion)).all()}


def _register_second_version(root, monkeypatch):
    """A method that ran once at v1, then registered again as v2.

    v2 changes the script, declares a param v1 lacks and runs in another
    env, all through production: the run by the harness's driven route,
    the second registration by ``register_method``.

    Returns:
        ``(module, v1 entry, v2 entry, v1 script text, v1 env name)``.
    """
    import yaml

    from tests.fixtures.routes import completed_run, write_env_record
    from tests.harness.project import method_source_dir
    from wfc.registration import register_method
    from wfc.registration.method_version import read_version_list

    driven = completed_run(root, monkeypatch=monkeypatch, method=VERSIONED)
    spec = next(s for s in driven.project.scenario.method_nodes()
                if s.method_name == VERSIONED)
    write_env_record(root, "other", digest="b" * 64)
    method_dir = method_source_dir(root, spec)
    script = method_dir / f"{VERSIONED}.py"
    v1_script = script.read_text(encoding="utf-8")
    script.write_text(v1_script + "\n# the second version\n", encoding="utf-8")
    contract = yaml.safe_load((method_dir / "method.yaml").read_text(encoding="utf-8"))
    v1_env = contract["env"]
    contract["params"] = {"threshold": {"type": "float"}}
    contract["env"] = "other"
    (method_dir / "method.yaml").write_text(
        yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    register_method(method_dir=method_dir, module_name=spec.module, assume_yes=True)
    v1, v2 = read_version_list(root, spec.module, VERSIONED)
    return spec.module, v1, v2, v1_script, v1_env


@workflow(purpose="The Registry looks back at any version of a method: the "
                  "listing names every version (label, identity, date, env, "
                  "its own run count, its commit, env fingerprint, image and "
                  "code tree) with the newest marked, and the detail given a "
                  "version's identity returns that version's files, contract, "
                  "env and run count; no identity is the newest and an "
                  "unknown one is a 404 naming it")
def test_registry_detail_shows_any_version_and_the_listing_marks_the_newest(
        tmp_project, monkeypatch):
    module, v1, v2, v1_script, v1_env = _register_second_version(tmp_project, monkeypatch)
    client = canvas_client(tmp_project, monkeypatch)

    listing = client.get("/api/registry/methods")
    older = client.get(f"/api/registry/methods/{module}/{VERSIONED}/detail",
                       params={"version": v1.identity})
    newest = client.get(f"/api/registry/methods/{module}/{VERSIONED}/detail")
    unknown = client.get(f"/api/registry/methods/{module}/{VERSIONED}/detail",
                         params={"version": "f" * 64})

    commits = _row_commits()
    assert commits[1] and commits[2] and commits[1] != commits[2]
    row = next(m for m in listing.json()["methods"] if m["name"] == VERSIONED)
    assert row["env"] == "other"
    assert row["versions"] == [
        {"label": 1, "identity": v1.identity, "registered_at": v1.registered_at,
         "env": v1_env, "runCount": 1, "newest": False, "commit": commits[1],
         "envFingerprint": v1.env_fingerprint, "image": v1.image_ref,
         "codeTree": v1.tree_hash},
        {"label": 2, "identity": v2.identity, "registered_at": v2.registered_at,
         "env": "other", "runCount": 0, "newest": True, "commit": commits[2],
         "envFingerprint": v2.env_fingerprint, "image": v2.image_ref,
         "codeTree": v2.tree_hash},
    ]

    assert older.status_code == 200, older.text
    body = older.json()
    assert {f["name"]: f["content"] for f in body["files"]}[f"{VERSIONED}.py"] == v1_script
    assert "threshold" not in body["contract"]["params_schema"]
    assert body["env"] == v1_env
    assert body["version"] == row["versions"][0]

    assert newest.status_code == 200, newest.text
    body = newest.json()
    assert "# the second version" in {
        f["name"]: f["content"] for f in body["files"]}[f"{VERSIONED}.py"]
    assert set(body["contract"]["params_schema"]) == {"threshold"}
    assert body["env"] == "other"
    assert body["version"]["identity"] == v2.identity
    assert body["version"]["newest"] is True

    assert unknown.status_code == 404, unknown.text
    assert "f" * 64 in unknown.json()["detail"]


@workflow(purpose="A version this database did not see registered (a fresh "
                  "clone's database, its rows rebuilt from the version list) "
                  "names no commit: the listing and the detail say null for "
                  "it, while a version registered afterwards records its own")
def test_registry_version_commit_is_null_when_not_recorded(
        tmp_project, tmp_path_factory, monkeypatch):
    import shutil

    from wfc.persistence import reset_engine
    from wfc.registration import register_method, register_module

    module, method = "mod", "transform"
    source = tmp_path_factory.mktemp("elsewhere") / method
    shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "methods" / method,
                    source)
    register_module(name=module, contracts=[])
    register_method(method_dir=source, module_name=module, assume_yes=True)
    # A fresh clone: no database; registering the same content rebuilds the rows.
    reset_engine()
    (tmp_project / ".wfc" / "wfc.db").unlink()
    reset_engine()
    register_module(name=module, contracts=[])
    register_method(method_dir=source, module_name=module, assume_yes=True)
    script = source / f"{method}.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# v2\n", encoding="utf-8")
    register_method(method_dir=source, module_name=module, assume_yes=True)
    client = canvas_client(tmp_project, monkeypatch)

    row = next(m for m in client.get("/api/registry/methods").json()["methods"]
               if m["name"] == method)
    v1, v2 = row["versions"]
    detail = client.get(f"/api/registry/methods/{module}/{method}/detail",
                        params={"version": v1["identity"]}).json()

    assert v1["commit"] is None
    assert detail["version"]["commit"] is None
    assert v2["commit"] == _row_commits()[2] and v2["commit"]


@workflow(purpose="Export script and contract: the zip <method>_v<N>.zip holds "
                  "every file of that version's registered copy, subfolders "
                  "included, and not the copy's stamp; an older version exports "
                  "its own files; an unknown identity is a 404 naming it")
def test_registry_export_zips_a_versions_copy(tmp_project, tmp_path_factory, monkeypatch):
    import io
    import shutil
    import zipfile

    from wfc.registration import register_method, register_module
    from wfc.registration.method_version import read_version_list

    module, method = "mod", "transform"
    source = tmp_path_factory.mktemp("elsewhere") / method
    shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "methods" / method,
                    source)
    yaml_path = source / "method.yaml"
    yaml_path.write_text(yaml_path.read_text(encoding="utf-8") + f"script: {method}.py\n",
                         encoding="utf-8")
    (source / "lib").mkdir()
    (source / "lib" / "util.py").write_bytes(b"X = 1\n")
    register_module(name=module, contracts=[])
    register_method(method_dir=source, module_name=module, assume_yes=True)
    (source / "lib" / "util.py").write_bytes(b"X = 2\n")
    register_method(method_dir=source, module_name=module, assume_yes=True)
    v1, v2 = read_version_list(tmp_project, module, method)
    client = canvas_client(tmp_project, monkeypatch)

    def export(version=None):
        params = {"version": version} if version else {}
        return client.get(f"/api/registry/methods/{module}/{method}/export", params=params)

    def files(resp) -> dict[str, bytes]:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            return {name: zf.read(name) for name in zf.namelist()}

    def copy_files(entry) -> dict[str, bytes]:
        copy = layout.code_copy_dir(tmp_project, entry.tree_hash)
        return {p.relative_to(copy).as_posix(): p.read_bytes()
                for p in copy.rglob("*") if p.is_file()}

    older, newest, unknown = export(v1.identity), export(), export("f" * 64)

    assert older.status_code == 200, older.text
    assert older.headers["content-disposition"] == f'attachment; filename="{method}_v1.zip"'
    assert files(older) == copy_files(v1)
    assert set(files(older)) == {"method.yaml", f"{method}.py", "lib/util.py"}
    assert files(older)["lib/util.py"] == b"X = 1\n"
    assert not any(name.endswith(".verified") for name in files(older))
    assert layout.code_copy_stamp(tmp_project, v1.tree_hash).is_file()

    assert newest.headers["content-disposition"] == f'attachment; filename="{method}_v2.zip"'
    assert files(newest) == copy_files(v2)

    assert unknown.status_code == 404, unknown.text
    assert "f" * 64 in unknown.json()["detail"]


# =============================================================================
# The dry run states the plan; the Register POST applies it
# =============================================================================

def _registration_state(root: Path, module: str, method: str) -> tuple:
    """Everything a method registration writes: HEAD, the list, the snapshot, the rows."""
    import subprocess

    from wfc.identity import fingerprinted_file_digests
    from wfc.persistence import MethodVersion, get_session

    list_path = layout.method_versions_path(root, module, method)
    snapshot = layout.method_dir(root, module, method)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()
    with get_session() as s:
        rows = sorted(m.name for m in s.exec(select(Method)).all())
        versions = sorted(v.label for v in s.exec(select(MethodVersion)).all())
    return (head, list_path.read_bytes() if list_path.is_file() else None,
            fingerprinted_file_digests(snapshot), rows, versions)


@workflow(purpose="For a method whose script changed, the canvas dry run "
                  "returns the same outcome message wfc register-method prints "
                  "('already registered (newest v1); this adds v2') and writes "
                  "nothing; the Register POST then adds v2 and returns the "
                  "message of the outcome it applied")
def test_method_dry_run_states_the_plan_and_register_applies_it(
        tmp_project, tmp_path_factory, monkeypatch, capsys):
    import shutil

    from wfc.cli import cli_main
    from wfc.registration import register_module, register_method

    module, method = "mod", "transform"
    source = tmp_path_factory.mktemp("elsewhere") / method
    shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "methods" / method,
                    source)
    register_module(name=module, contracts=[])
    register_method(method_dir=source, module_name=module, assume_yes=True)
    script = source / f"{method}.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# v2\n", encoding="utf-8")
    client = canvas_client(tmp_project, monkeypatch)
    before = _registration_state(tmp_project, module, method)

    # What the CLI prints for this change: with no terminal and no --yes it
    # states the plan and refuses, having written nothing.
    capsys.readouterr()
    assert cli_main(["register-method", str(source), "--module", module]) == 1
    cli_out = capsys.readouterr().out
    assert _registration_state(tmp_project, module, method) == before

    body = {"directory": str(source), "module": module, "method_name": None}
    dry = client.post("/api/registry/methods?dryRun=true", json=body)
    assert dry.status_code == 200, dry.text
    plan = dry.json()["plan"]
    assert plan["outcome"] == "changed"
    assert plan["message"].startswith(
        f"{module}/{method} is already registered (newest v1); this adds v2.")
    assert f"Code files changed since v1: {method}.py" in plan["message"]
    assert plan["message"] in cli_out
    # The changes the message names, as data, and what v2 holds.
    assert (plan["label"], plan["newestLabel"]) == (2, 1)
    assert plan["changes"]["code"] == {"changed": [f"{method}.py"], "added": [],
                                       "removed": []}
    assert plan["changes"]["slots"] == [] and plan["changes"]["params"] == []
    assert plan["holds"] == {"script": f"{method}.py", "helpers": [], "ignored": []}
    assert _registration_state(tmp_project, module, method) == before

    applied = client.post("/api/registry/methods",
                          json={**body, "plan_token": plan["token"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["message"] == plan["message"]
    assert _registration_state(tmp_project, module, method)[4] == [1, 2]


@workflow(purpose="The Register POST applies only the plan its dry run "
                  "stated -- a script edited between the dry run and the click "
                  "is refused with 409 listing what may have changed (the "
                  "source, the env or the version list) and leaves HEAD, "
                  "the version list, the snapshot and the rows as they were; "
                  "after a fresh dry run the POST registers the new version")
def test_method_register_refuses_a_plan_that_changed_since_its_dry_run(
        tmp_project, tmp_path_factory, monkeypatch):
    import shutil

    from wfc.registration import register_module, register_method

    module, method = "mod", "transform"
    source = tmp_path_factory.mktemp("elsewhere") / method
    shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "methods" / method,
                    source)
    register_module(name=module, contracts=[])
    register_method(method_dir=source, module_name=module, assume_yes=True)
    script = source / f"{method}.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# v2\n", encoding="utf-8")
    client = canvas_client(tmp_project, monkeypatch)
    body = {"directory": str(source), "module": module, "method_name": None}

    dry = client.post("/api/registry/methods?dryRun=true", json=body)
    assert dry.status_code == 200, dry.text
    stale_token = dry.json()["plan"]["token"]
    # The script changes again after the user read the plan.
    script.write_text(script.read_text(encoding="utf-8") + "\n# v3\n", encoding="utf-8")
    before = _registration_state(tmp_project, module, method)

    refused = client.post("/api/registry/methods",
                          json={**body, "plan_token": stale_token})
    assert refused.status_code == 409, refused.text
    assert "changed after registration said what it would do" in refused.json()["detail"]
    assert "Nothing was registered" in refused.json()["detail"]
    assert _registration_state(tmp_project, module, method) == before
    assert before[4] == [1]

    untokened = client.post("/api/registry/methods", json=body)
    assert untokened.status_code == 400, untokened.text
    assert _registration_state(tmp_project, module, method) == before

    fresh = client.post("/api/registry/methods?dryRun=true", json=body).json()["plan"]
    assert fresh["token"] != stale_token
    applied = client.post("/api/registry/methods",
                          json={**body, "plan_token": fresh["token"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["message"] == fresh["message"]
    assert _registration_state(tmp_project, module, method)[4] == [1, 2]


# =============================================================================
# T9: GET /api/fs/browse — project-root-scoped dir listing
# =============================================================================

def test_fs_browse_lists_project_root_contents(client, tmp_path, monkeypatch):
    """Returns dirs and files at the given project-relative path."""
    (tmp_path / "methods").mkdir()
    (tmp_path / "methods" / "transform").mkdir()
    (tmp_path / "README.md").write_text("hi\n")

    pin_project_root(monkeypatch, tmp_path)

    resp = client.get("/api/fs/browse")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == ""
    names = {(e["name"], e["kind"]) for e in body["entries"]}
    assert ("methods", "dir") in names
    assert ("README.md", "file") in names

    resp2 = client.get("/api/fs/browse?path=methods")
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["path"] == "methods"
    assert {"name": "transform", "kind": "dir"} in [
        {"name": e["name"], "kind": e["kind"]} for e in body2["entries"]
    ]


def test_fs_browse_rejects_traversal(client, tmp_path, monkeypatch):
    """`..` escapes the project root -> 400."""
    pin_project_root(monkeypatch, tmp_path)

    resp = client.get("/api/fs/browse?path=../../../etc")
    assert resp.status_code == 400
    assert "outside" in resp.json()["detail"].lower() or "traversal" in resp.json()["detail"].lower()


def test_method_detail_refuses_a_record_that_leaves_its_registered_copy(
    client, db_engine, registry_project
):
    """A method whose record names no version that can run (its version list
    is gone) is refused with the resolver's reason, naming the repair."""
    root = registry_project.project.root
    version_list = layout.method_versions_path(root, REGISTRY_MODULE, "tile_export")
    original = version_list.read_bytes()
    try:
        version_list.unlink()
        resp = client.get("/api/registry/methods/preprocessing/tile_export/detail")
        with pytest.raises(MalformedMethodRecordError) as refusal:
            resolve_method_code(REGISTRY_MODULE, "tile_export", root)
    finally:
        version_list.write_bytes(original)
    assert resp.status_code == 400
    assert resp.json()["detail"] == str(refusal.value)
    assert "wfc register-method" in resp.json()["detail"]


# ============================================================================
# Envs registry endpoints
# ============================================================================

_DEMO_PIXI_LOCK = """\
version: 5
packages:
- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.11.0-h.conda
  name: python
  version: 3.11.0
- pypi: https://files.pythonhosted.org/packages/numpy-1.24.0-cp311.whl
  name: numpy
  version: 1.24.0
"""


def _seed_env_manifest_and_blob(tmp_path):
    """Write a .wfc/envs.json with a captured pixi env ('demo') + an
    uncaptured byo env ('byo_box') through the production serialization
    helper. The demo env's package list lands in its captures folder
    through the production writer, so the /packages and /envs endpoints
    read back exactly what registration would have committed.

    Returns the demo env's source_fingerprint md5.
    """
    from tests.fixtures.conftest import write_env_record

    demo = write_env_record(
        tmp_path, "demo", backend="pixi", digest="a" * 64,
        lock_content=_DEMO_PIXI_LOCK,
    )
    write_env_record(
        tmp_path, "byo_box", backend="byo", image="reg/img", digest="b" * 64,
    )
    return demo["source_fingerprint"]


def test_list_envs_reshaped_row_carries_backend_run_stats_and_has_packages(
    client, db_engine, tmp_path, monkeypatch
):
    """List rows report backend + has_packages + run stats, sourced from the
    env manifest and Run rows (the reshaped row, no fingerprint history)."""
    from datetime import datetime
    from wfc.persistence import Run

    _seed_env_manifest_and_blob(tmp_path)
    pin_project_root(monkeypatch, tmp_path)

    with Session(db_engine) as session:
        m1 = session.exec(select(Method).where(Method.name == "tile_export")).first()
        m2 = session.exec(select(Method).where(Method.name == "normalize")).first()
        session.add_all([
            Run(method_id=m1.id, status="completed",
                started_at=datetime(2026, 4, 18, 10, 0, 0), env_fingerprint="a" * 32),
            Run(method_id=m1.id, status="completed",
                started_at=datetime(2026, 4, 18, 12, 0, 0), env_fingerprint="a" * 32),
            Run(method_id=m2.id, status="completed",
                started_at=datetime(2026, 4, 18, 14, 0, 0), env_fingerprint="b" * 32),
        ])
        session.commit()

    envs = client.get("/api/registry/envs").json()["envs"]
    assert len(envs) == 1
    row = envs[0]
    assert row["spec"] == REGISTRY_ENV
    assert set(row["methods"]) == {"preprocessing.tile_export", "preprocessing.normalize"}
    assert row["backend"] == "pixi"
    assert row["has_packages"] is True
    assert row["run_count"] == 3
    assert row["last_run_at"] is not None


def test_env_packages_endpoint_captured_vs_byo_and_agrees_with_list(
    client, db_engine, tmp_path, monkeypatch
):
    """/packages returns a parsed, source-tagged list for a captured pixi env
    and an honest empty state for a byo env; has_packages on the list row
    agrees with /packages captured for the same spec."""
    _seed_env_manifest_and_blob(tmp_path)
    pin_project_root(monkeypatch, tmp_path)

    captured = client.get("/api/registry/envs/container:demo/packages").json()
    assert captured["captured"] is True
    assert captured["backend"] == "pixi"
    names = {p["name"]: p for p in captured["packages"]}
    assert set(names) == {"numpy", "python"}
    assert names["numpy"] == {"name": "numpy", "version": "1.24.0", "source": "pixi"}

    byo = client.get("/api/registry/envs/container:byo_box/packages").json()
    assert byo["captured"] is False
    assert byo["packages"] == []
    assert byo["backend"] == "byo"

    # Agreement: the list row's has_packages matches /packages captured.
    row = client.get("/api/registry/envs").json()["envs"][0]
    assert row["spec"] == REGISTRY_ENV
    assert row["has_packages"] == captured["captured"]


_TWO_ENV_PIXI_LOCK = """\
version: 6
environments:
  default:
    packages:
      linux-64:
      - pypi: https://files.pythonhosted.org/packages/napari-0.6.6-py3-none-any.whl
  demo:
    packages:
      linux-64:
      - conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.9.23-hc30ae73_0_cpython.conda
      - pypi: https://files.pythonhosted.org/packages/cellpose-2.3.2-py3-none-any.whl
packages:
- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.9.23-hc30ae73_0_cpython.conda
  sha256: aaaa
- pypi: https://files.pythonhosted.org/packages/cellpose-2.3.2-py3-none-any.whl
  name: cellpose
  version: 2.3.2
- pypi: https://files.pythonhosted.org/packages/napari-0.6.6-py3-none-any.whl
  name: napari
  version: 0.6.6
"""


def test_env_packages_endpoint_lists_the_envs_own_pixi_environment(
    client, tmp_path, monkeypatch
):
    """A pixi env built from a lock with several environments lists the
    packages of the environment its image installed (the env's name), conda
    ones included, and none of the other environments'."""
    from tests.fixtures.conftest import write_env_record

    write_env_record(tmp_path, "demo", backend="pixi", digest="a" * 64,
                     lock_content=_TWO_ENV_PIXI_LOCK)
    pin_project_root(monkeypatch, tmp_path)

    captured = client.get("/api/registry/envs/container:demo/packages").json()
    assert captured["packages"] == [
        {"name": "cellpose", "version": "2.3.2", "source": "pixi"},
        {"name": "python", "version": "3.9.23", "source": "pixi"},
    ]


def _demo_package_list(project_dir):
    """The demo env's committed ``packages.lock``, located through its record."""
    from wfc.environments import get

    record = get(REGISTRY_ENV, project_dir)
    return (layout.env_capture_dir(project_dir, REGISTRY_ENV, record.env_fingerprint)
            / layout.CAPTURE_PACKAGES_FILENAME)


@workflow(purpose="The Registry's package view reads the env's committed "
                  "captures folder, so it works with an empty DVC cache, and a "
                  "package list whose bytes no longer match the record is refused "
                  "naming the plain remedy: register under a new env name.")
def test_env_packages_read_the_capture_without_dvc_and_refuse_a_changed_byte(
    client, db_engine, tmp_path, monkeypatch
):
    import os
    import shutil
    import stat

    _seed_env_manifest_and_blob(tmp_path)
    pin_project_root(monkeypatch, tmp_path)

    def _writable_retry(func, path, _exc):
        # Cache entries are stored read-only; Windows refuses to unlink them.
        os.chmod(path, stat.S_IWRITE)
        func(path)

    # A fresh clone: the DVC cache holds nothing.
    shutil.rmtree(layout.dvc_cache_files_dir(tmp_path), onexc=_writable_retry)
    assert not layout.dvc_cache_files_dir(tmp_path).exists()

    captured = client.get(f"/api/registry/envs/{REGISTRY_ENV}/packages")
    assert captured.status_code == 200
    assert {p["name"] for p in captured.json()["packages"]} == {"numpy", "python"}

    # One byte of the committed package list changes.
    lock = _demo_package_list(tmp_path)
    data = bytearray(lock.read_bytes())
    data[-2] = ord("X") if data[-2] != ord("X") else ord("Y")
    lock.write_bytes(bytes(data))

    refused = client.get(f"/api/registry/envs/{REGISTRY_ENV}/packages")
    assert refused.status_code == 409
    assert "wfc register-env <new-name>" in refused.json()["detail"]


@pytest.mark.parametrize("damage", [
    "image-missing", "image-unreadable", "lock-missing", "lock-edited"])
def test_env_packages_refuse_a_changed_capture_with_the_plain_remedy(
    client, db_engine, tmp_path, monkeypatch, damage
):
    """With ``image.json`` or ``packages.lock`` changed outside wfc, the
    package view is a 409 naming the changed file once and the one remedy
    register-env's refusal names (register under a new env name): no git
    command, and no ``register-env <env> --force``, which register refuses."""
    from wfc.environments import capture_remedy, get

    _seed_env_manifest_and_blob(tmp_path)
    pin_project_root(monkeypatch, tmp_path)
    lock = _demo_package_list(tmp_path)
    image_json = lock.parent / layout.CAPTURE_IMAGE_FILENAME
    target = image_json if damage.startswith("image") else lock
    if damage.endswith("missing"):
        target.unlink()
    elif damage == "image-unreadable":
        target.write_text("{not json", encoding="utf-8")
    else:
        target.write_bytes(target.read_bytes() + b"numpy==9.9.9\n")

    refused = client.get(f"/api/registry/envs/{REGISTRY_ENV}/packages")

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail.count(str(target)) == 1
    fingerprint = get(REGISTRY_ENV, tmp_path).env_fingerprint
    assert detail.endswith(capture_remedy(REGISTRY_ENV, fingerprint))
    assert "changed outside wfc" in detail
    assert "git" not in detail.replace(str(tmp_path), "<project>")
    assert "--force" not in detail


def test_list_envs_has_packages_follows_the_captures_package_list(
    client, db_engine, tmp_path, monkeypatch
):
    """has_packages is whether the env's captures folder holds a package list:
    with ``packages.lock`` removed the row says there is none to show."""
    _seed_env_manifest_and_blob(tmp_path)
    pin_project_root(monkeypatch, tmp_path)

    assert client.get("/api/registry/envs").json()["envs"][0]["has_packages"] is True

    _demo_package_list(tmp_path).unlink()

    assert client.get("/api/registry/envs").json()["envs"][0]["has_packages"] is False
