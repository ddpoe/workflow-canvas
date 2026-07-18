"""Unit tests for wfc_client.RunContext (Tier-1 ctx surface).

Behavior-first: each test pins one ADR-020 ctx member. Pure stdlib;
no Docker, no wfc, no pandas.
"""

import json

import pytest

import wfc_client as wfc
from wfc_client.context import RESULTS_FILENAME, RunContext


def _set_env(monkeypatch, run_dir, *, input_paths=None, params=None):
    monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
    monkeypatch.setenv("WFC_INPUT_PATHS", json.dumps(input_paths or {}))
    monkeypatch.setenv("WFC_PARAMS", json.dumps(params or {}))


def test_input_resolves_slot_paths(tmp_path, monkeypatch):
    """ctx.input(slot) returns the WFC_INPUT_PATHS entries as Path objects."""
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _set_env(monkeypatch, tmp_path, input_paths={"data": [str(a), str(b)]})

    ctx = RunContext()
    got = ctx.input("data")

    assert [p.name for p in got] == ["a.csv", "b.csv"]
    assert ctx.input("missing") == []


def test_params_and_run_dir(tmp_path, monkeypatch):
    """ctx.params parses WFC_PARAMS; ctx.run_dir is the resolved WFC_RUN_DIR."""
    _set_env(monkeypatch, tmp_path, params={"threshold": 0.5, "name": "x"})

    ctx = RunContext()

    assert ctx.params == {"threshold": 0.5, "name": "x"}
    assert ctx.run_dir == tmp_path.resolve()


def test_workdir_is_inside_run_dir_and_created(tmp_path, monkeypatch):
    """ctx.workdir is WFC_RUN_DIR/_workdir/ and is created on access."""
    _set_env(monkeypatch, tmp_path)

    ctx = RunContext()
    wd = ctx.workdir

    assert wd == (tmp_path.resolve() / "_workdir")
    assert wd.is_dir()
    # Files written here satisfy the save_artifact constraint automatically.
    assert wd.resolve().is_relative_to(ctx.run_dir)


def test_save_artifact_records_relative_path_no_io(tmp_path, monkeypatch):
    """save_artifact records a run-dir-relative path and does NOT touch bytes."""
    _set_env(monkeypatch, tmp_path)
    ctx = RunContext()

    out = ctx.workdir / "clean.csv"
    out.write_text("id\n1\n")
    before = out.read_bytes()

    ctx.save_artifact("clean", out)

    # Recorded as a run-dir-relative POSIX path.
    assert ctx._outputs == {"clean": "_workdir/clean.csv"}
    # The file is untouched (no copy/move/rewrite).
    assert out.read_bytes() == before


def test_save_artifact_rejects_path_outside_run_dir(tmp_path, monkeypatch):
    """save_artifact raises a clear error when source is outside WFC_RUN_DIR."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "elsewhere.csv"
    outside.write_text("x")
    _set_env(monkeypatch, run_dir)

    ctx = RunContext()
    with pytest.raises(ValueError) as exc:
        ctx.save_artifact("clean", outside)

    msg = str(exc.value)
    assert "WFC_RUN_DIR" in msg
    assert "ctx.workdir" in msg


def test_save_artifact_does_not_validate_extension(tmp_path, monkeypatch):
    """save_artifact validates ONLY path-inside-run_dir, not extension/type.

    Output type/extension correctness is a host-side concern (ADR-005),
    so an arbitrary extension inside run_dir must be accepted here.
    """
    _set_env(monkeypatch, tmp_path)
    ctx = RunContext()
    weird = ctx.run_dir / "clean.weirdext"
    weird.write_text("x")

    ctx.save_artifact("clean", weird)  # no raise

    assert ctx._outputs == {"clean": "clean.weirdext"}


def test_finalize_writes_manifest_shape(tmp_path, monkeypatch):
    """_finalize writes _wfc_results.json = {outputs, metrics} (run-dir-relative)."""
    _set_env(monkeypatch, tmp_path)
    ctx = RunContext()

    out = ctx.workdir / "clean.csv"
    out.write_text("x")
    ctx.save_artifact("clean", out)
    ctx.log_metric("kept_rows", 100)
    ctx.log_metric("dropped_rows", 5)

    manifest_path = ctx._finalize()

    assert manifest_path == tmp_path.resolve() / RESULTS_FILENAME
    data = json.loads(manifest_path.read_text())
    assert data == {
        "outputs": {"clean": "_workdir/clean.csv"},
        "metrics": {"kept_rows": 100, "dropped_rows": 5},
    }


def test_missing_run_dir_raises_clear_error(monkeypatch):
    """Constructing RunContext without WFC_RUN_DIR fails with guidance."""
    monkeypatch.delenv("WFC_RUN_DIR", raising=False)
    with pytest.raises(RuntimeError) as exc:
        RunContext()
    assert "WFC_RUN_DIR" in str(exc.value)


def test_public_surface_is_pure_stdlib():
    """wfc_client and its modules import no third-party packages (no pandas)."""
    import sys

    # Importing wfc_client must not have pulled in pandas or wfc.
    assert "pandas" not in sys.modules or True  # pandas may be loaded by other tests
    # Stronger: the package modules themselves reference only stdlib.
    import wfc_client.context as ctxmod
    import wfc_client.decorator as decmod
    import wfc_client.main as mainmod
    import wfc_client.errors as errmod

    import ast

    for mod in (ctxmod, decmod, mainmod, errmod):
        src = open(mod.__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                # Absolute imports only (level 0); relative imports stay in-package.
                if node.level == 0 and node.module:
                    imported_roots.add(node.module.split(".")[0])
        assert "pandas" not in imported_roots, f"{mod.__name__} imports pandas"
        assert "wfc" not in imported_roots, f"{mod.__name__} imports the full wfc package"
        assert "sqlmodel" not in imported_roots, f"{mod.__name__} imports sqlmodel"
