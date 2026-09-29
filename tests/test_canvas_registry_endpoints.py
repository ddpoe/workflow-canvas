"""Registry-tab HTTP endpoints in wfc.canvas.routes.registry.

Covers:
  - GET  /api/registry/modules     (response shape)
  - GET  /api/registry/methods     (validated: null default)
  - POST /api/registry/methods/validate (cache hit, fingerprint invalidation,
    the ``__main__`` block not executed)
  - POST /api/registry/samples     (DvcNotConfiguredError -> 409)
  - POST /api/registry/modules     (?dryRun=true does not persist)
  - method detail (files and contract; path traversal refused)
  - filesystem browse (project-root listing; traversal refused)
  - environments list, packages and blob endpoints
"""

from __future__ import annotations

from tests.conftest import pin_project_root
from tests.fixtures.fakes import stub_registry_seam

import pytest
from sqlmodel import Session, create_engine, select

from wfc.canvas.routes import registry as registry_routes
from wfc.persistence import Method, Module
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
# T2: GET /api/registry/methods — validated: null on uncached methods
# =============================================================================

def test_get_registry_methods_includes_validated_null(client, registry_project):
    """Freshly-registered methods (no dryRun run yet) report validated: null.

    `validated: bool | null` is sourced from the dryRun cache.
    """
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
    assert tile["validated"] is None
    assert tile["runCount"] == 0
    assert tile["source"] == "methods/tile_export/method.yaml"


# =============================================================================
# T3: Method validate — cache hit when fingerprint unchanged
# =============================================================================

def test_method_validate_cache_hit_on_unchanged_fingerprint(
    client, tmp_path, monkeypatch
):
    """Second validate call with identical script contents skips subprocess."""
    script = tmp_path / "methods" / "tile_export" / "tile_export.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# tile_export\n")

    # Point the server at this tmp project so fingerprint lookup resolves.
    pin_project_root(monkeypatch, tmp_path)

    call_count = {"n": 0}

    def fake_import_check(python_bin, module_name):
        call_count["n"] += 1
        return (0, "", "")

    stub_registry_seam(monkeypatch, "_run_import_check_fn", fake_import_check)
    registry_routes._method_validate_cache.clear()

    first = client.post("/api/registry/methods/validate",
                        json={"module": "preprocessing", "method": "tile_export"})
    assert first.status_code == 200
    assert first.json()["validated"] is True
    assert call_count["n"] == 1

    second = client.post("/api/registry/methods/validate",
                         json={"module": "preprocessing", "method": "tile_export"})
    assert second.status_code == 200
    assert second.json()["validated"] is True
    # Fingerprint unchanged -> cache hit -> subprocess NOT called a second time.
    assert call_count["n"] == 1


# =============================================================================
# T4: Method validate — cache invalidated when script fingerprint changes
# =============================================================================

def test_method_validate_invalidates_on_fingerprint_change(
    client, tmp_path, monkeypatch
):
    """Editing the script file changes its sha256; the next validate re-runs."""
    script = tmp_path / "methods" / "tile_export" / "tile_export.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# v1\n")

    pin_project_root(monkeypatch, tmp_path)

    call_count = {"n": 0}

    def fake_import_check(python_bin, script_path):
        call_count["n"] += 1
        return (0, "", "")

    stub_registry_seam(monkeypatch, "_run_import_check_fn", fake_import_check)
    registry_routes._method_validate_cache.clear()

    r1 = client.post("/api/registry/methods/validate",
                     json={"module": "preprocessing", "method": "tile_export"})
    assert r1.status_code == 200
    assert call_count["n"] == 1

    # Edit the script -> fingerprint changes -> cache key misses.
    script.write_text("# v2 — different contents\n")

    r2 = client.post("/api/registry/methods/validate",
                     json={"module": "preprocessing", "method": "tile_export"})
    assert r2.status_code == 200
    assert call_count["n"] == 2


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
# validate must NOT execute the `if __name__ == "__main__":` block
# =============================================================================

def test_method_validate_does_not_run_main_block(client, tmp_path, monkeypatch):
    """Scripts gated by `if __name__ == "__main__":` must import cleanly.

    Fixture method scripts wrap their work in `main()` called from a
    `__main__` guard. Running the validator with run_name='__main__' would
    invoke main() and crash with a KeyError on missing env vars. The import
    check must load the script as a non-__main__ module so the guard protects
    the validator.
    """
    script = tmp_path / "methods" / "tile_export" / "tile_export.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "def main():\n"
        "    raise RuntimeError('must not run during validate')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )

    pin_project_root(monkeypatch, tmp_path)
    registry_routes._method_validate_cache.clear()

    resp = client.post(
        "/api/registry/methods/validate",
        json={"module": "preprocessing", "method": "tile_export"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["validated"] is True, f"Unexpected: {body}"


# =============================================================================
# T7: GET /api/registry/methods/{module}/{method}/detail — files + contract
# =============================================================================

def test_method_detail_returns_files_and_contract(client, tmp_path, monkeypatch):
    """Detail endpoint returns every file in method dir + parsed contract."""
    method_dir = tmp_path / "methods" / "tile_export"
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "tile_export.py").write_text("def main():\n    pass\n")
    (method_dir / "method.yaml").write_text("name: tile_export\n")

    pin_project_root(monkeypatch, tmp_path)

    resp = client.get("/api/registry/methods/preprocessing/tile_export/detail")
    assert resp.status_code == 200

    body = resp.json()
    assert "files" in body
    assert "contract" in body

    by_name = {f["name"]: f for f in body["files"]}
    assert "tile_export.py" in by_name
    assert "method.yaml" in by_name
    assert by_name["tile_export.py"]["language"] == "python"
    assert by_name["method.yaml"]["language"] == "yaml"
    assert "def main()" in by_name["tile_export.py"]["content"]

    contract = body["contract"]
    assert "input_slots" in contract
    assert "output_slots" in contract
    assert "params_schema" in contract


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


def test_method_detail_rejects_path_traversal(client, db_engine, tmp_path, monkeypatch):
    """A DB-poisoned script_path with ../ must not expose files outside project root."""
    # Poison the DB: overwrite tile_export.script_path to an escape sequence.
    with Session(db_engine) as session:
        meth = session.exec(
            select(Method).where(Method.name == "tile_export")
        ).first()
        meth.script_path = "../../../etc/tile_export.py"
        session.add(meth)
        session.commit()

    pin_project_root(monkeypatch, tmp_path)

    resp = client.get("/api/registry/methods/preprocessing/tile_export/detail")
    assert resp.status_code == 400
    assert "outside" in resp.json()["detail"].lower() or "traversal" in resp.json()["detail"].lower()


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
    helper. The demo env's pixi source blob lands in the DVC cache via the
    production source_fingerprint path, so the /packages and /envs
    endpoints read back exactly what registration would have staged.

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


def test_env_blob_serves_content_and_guards_md5(client, tmp_path, monkeypatch):
    """Blob endpoint reads the DVC cache; a malformed md5 is a 400 and an
    absent blob a 404, each with its detail string."""
    pin_project_root(monkeypatch, tmp_path)
    md5 = "d" * 32
    cache_dir = tmp_path / ".dvc" / "cache" / "files" / "md5" / md5[:2]
    cache_dir.mkdir(parents=True)
    (cache_dir / md5[2:]).write_text("env blob contents\npackage==1.0\n", encoding="utf-8")

    assert "env blob contents" in client.get(f"/api/registry/envs/blob/{md5}").text
    malformed = client.get("/api/registry/envs/blob/not-a-valid-md5")
    assert malformed.status_code == 400
    assert malformed.json()["detail"] == "malformed md5 (expect 32 lowercase hex)"
    absent = "e" * 32
    missing = client.get(f"/api/registry/envs/blob/{absent}")
    assert missing.status_code == 404
    assert missing.json()["detail"] == f"blob not found: {absent}"
