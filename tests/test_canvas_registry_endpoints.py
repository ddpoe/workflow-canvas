"""Registry-tab HTTP endpoints on wfc.canvas.server.

Design handoff: design_handoff_onboarding/ENDPOINTS.md (scoped subset).

Covers:
  - GET  /api/registry/modules     (response shape)
  - GET  /api/registry/methods     (validated: null default)
  - POST /api/registry/methods     (dryRun cache hit + fingerprint invalidation)
  - POST /api/registry/samples     (DvcNotConfiguredError -> 409)
  - POST /api/registry/modules     (?dryRun=true does not persist)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from wfc.canvas import server as canvas_server
from wfc.canvas.server import app
from wfc.models import Method, MethodContract, Module, ModuleContract


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        mod = Module(name="preprocessing", description="Tile export + normalization.")
        session.add(mod)
        session.flush()

        session.add_all([
            ModuleContract(
                module_id=mod.id,
                contract_type="output",
                name="expression_matrix",
                value_type=".h5ad",
                required=True,
            ),
            ModuleContract(
                module_id=mod.id,
                contract_type="metric",
                name="batch_effect_score",
                value_type="float",
                required=True,
            ),
        ])

        m1 = Method(name="tile_export", module_id=mod.id,
                    script_path="methods/tile_export/tile_export.py",
                    env="inherit")
        m2 = Method(name="normalize", module_id=mod.id,
                    script_path="methods/normalize/normalize.py",
                    env="inherit")
        session.add_all([m1, m2])
        session.flush()

        session.add_all([
            MethodContract(method_id=m1.id, input_slots={}, output_slots={}, params_schema={}),
            MethodContract(method_id=m2.id, input_slots={}, output_slots={}, params_schema={}),
        ])
        session.commit()

    yield engine


@pytest.fixture
def client(db_engine):
    return TestClient(app, raise_server_exceptions=False)


# =============================================================================
# T1: GET /api/registry/modules — response shape matches ENDPOINTS.md
# =============================================================================

def test_get_registry_modules_shape(client):
    """Response matches the handoff contract: name, description, contracts, methods count, source."""
    resp = client.get("/api/registry/modules")
    assert resp.status_code == 200

    body = resp.json()
    assert "modules" in body
    assert len(body["modules"]) == 1

    mod = body["modules"][0]
    assert mod["name"] == "preprocessing"
    assert mod["description"] == "Tile export + normalization."
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

def test_get_registry_methods_includes_validated_null(client):
    """Freshly-registered methods (no dryRun run yet) report validated: null.

    Replaces the ENDPOINTS.md `status: "ok"|"stale"|"broken"` enum with a
    `validated: bool | null` field sourced from the dryRun cache.
    """
    resp = client.get("/api/registry/methods")
    assert resp.status_code == 200

    body = resp.json()
    assert "methods" in body
    assert len(body["methods"]) == 2

    by_name = {m["name"]: m for m in body["methods"]}
    tile = by_name["tile_export"]

    assert tile["module"] == "preprocessing"
    assert tile["env"] == "inherit"
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
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))

    call_count = {"n": 0}

    def fake_import_check(python_bin, module_name):
        call_count["n"] += 1
        return (0, "", "")

    monkeypatch.setattr(canvas_server, "_run_import_check_fn", fake_import_check)
    canvas_server._method_validate_cache.clear()

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

    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))

    call_count = {"n": 0}

    def fake_import_check(python_bin, script_path):
        call_count["n"] += 1
        return (0, "", "")

    monkeypatch.setattr(canvas_server, "_run_import_check_fn", fake_import_check)
    canvas_server._method_validate_cache.clear()

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
    """`DvcNotConfiguredError` from wfc.cli.register_sample -> HTTP 409."""
    from wfc.provenance import DvcNotConfiguredError

    def fake_register_sample(*args, **kwargs):
        raise DvcNotConfiguredError(
            "Project has no [dvc] section in wf-canvas.toml"
        )

    monkeypatch.setattr(canvas_server, "_register_sample_fn", fake_register_sample)

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

    monkeypatch.setattr(canvas_server, "_register_module_fn", fake_register_module)

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
# Regression: validate must NOT execute the `if __name__ == "__main__":` block
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

    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))
    canvas_server._method_validate_cache.clear()

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

    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))

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
# T8: detail endpoint rejects method_dir that escapes the project root
# =============================================================================

# =============================================================================
# T9: GET /api/fs/browse — project-root-scoped dir listing
# =============================================================================

def test_fs_browse_lists_project_root_contents(client, tmp_path, monkeypatch):
    """Returns dirs and files at the given project-relative path."""
    (tmp_path / "methods").mkdir()
    (tmp_path / "methods" / "transform").mkdir()
    (tmp_path / "README.md").write_text("hi\n")

    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))

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
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))

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

    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))

    resp = client.get("/api/registry/methods/preprocessing/tile_export/detail")
    assert resp.status_code == 400
    assert "outside" in resp.json()["detail"].lower() or "traversal" in resp.json()["detail"].lower()


# ============================================================================
# Envs registry endpoints
# ============================================================================

def test_list_and_fingerprints_aggregate_from_runs(client, db_engine):
    """List + fingerprints endpoints aggregate Run.env_fingerprint correctly."""
    from datetime import datetime
    from wfc.models import Run
    with Session(db_engine) as session:
        m1 = session.exec(select(Method).where(Method.name == "tile_export")).first()
        m2 = session.exec(select(Method).where(Method.name == "normalize")).first()
        fp_a, fp_b = "a" * 32, "b" * 32
        session.add_all([
            Run(method_id=m1.id, status="completed",
                started_at=datetime(2026, 4, 18, 10, 0, 0), env_fingerprint=fp_a),
            Run(method_id=m1.id, status="completed",
                started_at=datetime(2026, 4, 18, 12, 0, 0), env_fingerprint=fp_a),
            Run(method_id=m2.id, status="completed",
                started_at=datetime(2026, 4, 18, 14, 0, 0), env_fingerprint=fp_b),
        ])
        session.commit()

    envs = client.get("/api/registry/envs").json()["envs"]
    assert len(envs) == 1
    row = envs[0]
    assert row["spec"] == "inherit"
    assert set(row["methods"]) == {"preprocessing.tile_export", "preprocessing.normalize"}
    assert row["fingerprint_count"] == 2
    assert row["run_count"] == 3
    assert row["last_run_at"] is not None

    fps = client.get("/api/registry/envs/inherit/fingerprints").json()["fingerprints"]
    by_md5 = {e["md5"]: e for e in fps}
    assert by_md5[fp_a]["run_count"] == 2
    assert by_md5[fp_b]["run_count"] == 1


def test_snapshot_computes_is_new_and_surfaces_errors(client, db_engine, monkeypatch):
    """Snapshot returns is_new based on existing Runs and 400s on capture failure."""
    from datetime import datetime
    from wfc.models import Run
    known_md5 = "a" * 32
    with Session(db_engine) as session:
        m1 = session.exec(select(Method).where(Method.name == "tile_export")).first()
        session.add(Run(method_id=m1.id, status="completed",
                        started_at=datetime(2026, 4, 18, 10, 0, 0),
                        env_fingerprint=known_md5))
        session.commit()

    # is_new=False: md5 matches an existing Run.
    monkeypatch.setattr("wfc.version.capture_env_content", lambda s, p: "content")
    monkeypatch.setattr("wfc.version.store_env_content", lambda c, p: known_md5)
    assert client.post("/api/registry/envs/inherit/snapshot").json()["is_new"] is False

    # is_new=True: md5 is novel.
    monkeypatch.setattr("wfc.version.store_env_content", lambda c, p: "c" * 32)
    assert client.post("/api/registry/envs/inherit/snapshot").json()["is_new"] is True

    # capture failure surfaces as 400 with the message (frontend shows in tooltip).
    def boom(spec, project_dir):
        raise ValueError("pixi.lock not found")
    monkeypatch.setattr("wfc.version.capture_env_content", boom)
    resp = client.post("/api/registry/envs/inherit/snapshot")
    assert resp.status_code == 400 and "pixi.lock" in resp.json()["detail"]


def test_env_blob_serves_content_and_guards_md5(client, tmp_path, monkeypatch):
    """Blob endpoint reads the DVC cache and rejects malformed md5."""
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))
    md5 = "d" * 32
    cache_dir = tmp_path / ".dvc" / "cache" / "files" / "md5" / md5[:2]
    cache_dir.mkdir(parents=True)
    (cache_dir / md5[2:]).write_text("env blob contents\npackage==1.0\n", encoding="utf-8")

    assert "env blob contents" in client.get(f"/api/registry/envs/blob/{md5}").text
    assert client.get("/api/registry/envs/blob/not-a-valid-md5").status_code == 400
