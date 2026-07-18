"""Unit tests for wfc_client.run() registry resolution + end-to-end finalize.

The decorator registry is module-level; each test clears it so the
zero/one/many resolution is deterministic. Pure stdlib; no Docker.
"""

import json

import pytest

import wfc_client as wfc
from wfc_client import decorator
from wfc_client.context import RESULTS_FILENAME


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset the module-level @method registry around each test."""
    decorator._registry.clear()
    yield
    decorator._registry.clear()


def _set_env(monkeypatch, run_dir, *, input_paths=None, params=None):
    monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
    monkeypatch.setenv("WFC_INPUT_PATHS", json.dumps(input_paths or {}))
    monkeypatch.setenv("WFC_PARAMS", json.dumps(params or {}))


def test_run_zero_methods_raises_clear_error(tmp_path, monkeypatch):
    """run() with no @method registered raises a clear error, not IndexError."""
    _set_env(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError) as exc:
        wfc.run()
    assert "no @wfc.method" in str(exc.value)


def test_run_multiple_methods_raises_clear_error(tmp_path, monkeypatch):
    """run() with >1 @method registered names them and explains the rule."""
    _set_env(monkeypatch, tmp_path)

    @wfc.method
    def first(ctx):
        pass

    @wfc.method
    def second(ctx):
        pass

    with pytest.raises(RuntimeError) as exc:
        wfc.run()
    msg = str(exc.value)
    assert "exactly one" in msg
    assert "first" in msg and "second" in msg


def test_run_one_method_end_to_end(tmp_path, monkeypatch):
    """run() builds ctx, calls the single @method, and finalizes the manifest."""
    _set_env(monkeypatch, tmp_path, input_paths={"data": [str(tmp_path / "in.csv")]},
             params={"k": 3})
    (tmp_path / "in.csv").write_text("v\n1\n")

    @wfc.method
    def qc(ctx):
        assert ctx.params == {"k": 3}
        assert ctx.input("data")[0].name == "in.csv"
        out = ctx.workdir / "clean.csv"
        out.write_text("v\n1\n")
        ctx.save_artifact("clean", out)
        ctx.log_metric("kept", 1)
        # Return value is ignored — no return-value parsing.
        return "ignored"

    wfc.run()

    manifest = json.loads((tmp_path / RESULTS_FILENAME).read_text())
    assert manifest == {
        "outputs": {"clean": "_workdir/clean.csv"},
        "metrics": {"kept": 1},
    }


def test_method_decorator_sets_marker_and_registers():
    """@method flags the function and appends it to the registry."""
    @wfc.method
    def m(ctx):
        pass

    assert m._wfc_method is True
    assert decorator._registry == [m]
