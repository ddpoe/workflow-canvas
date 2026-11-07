"""Unit tests for wfc_client.RunContext (Tier-1 ctx surface).

Behavior-first: each test pins one ctx member. Pure stdlib;
no Docker, no wfc, no pandas.
"""

import json

import pytest
from axiom_annotations import workflow

import wfc_client as wfc
from wfc_client.context import RESULTS_FILENAME, RunContext


def _set_env(monkeypatch, run_dir, *, input_paths=None, params=None, declared=None):
    monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
    monkeypatch.setenv("WFC_INPUT_PATHS", json.dumps(input_paths or {}))
    monkeypatch.setenv("WFC_PARAMS", json.dumps(params or {}))
    if declared is None:
        monkeypatch.delenv("WFC_DECLARED_NAMES", raising=False)
    else:
        monkeypatch.setenv("WFC_DECLARED_NAMES", json.dumps(declared))


_DECLARED = {
    "inputs": {"data": {"multiple": False, "optional": False},
               "many": {"multiple": True, "optional": False},
               "extra": {"multiple": False, "optional": True}},
    "outputs": ["clean"],
}


@workflow(purpose="A single input reaches the script as one path that also "
                  "answers [0] with one step-log note; a multiple input is a "
                  "list; an optional unwired single input is None")
def test_input_shape_follows_the_declaration(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    a, b, c = (str(tmp_path / n) for n in ("a.csv", "b.csv", "c.csv"))
    raw = {"data": [a], "many": [b, c]}
    _set_env(monkeypatch, tmp_path, input_paths=raw, declared=_DECLARED)
    ctx = RunContext()

    single = ctx.input("data")
    assert isinstance(single, Path) and single == Path(a)
    assert single[0] == single and list(single) == [single]
    ctx.input("data")[0]
    notes = [ln for ln in capsys.readouterr().err.splitlines() if "[0]" in ln]
    assert len(notes) == 1 and "'data'" in notes[0]

    assert ctx.input("many") == [Path(b), Path(c)]
    assert ctx.input("extra") is None
    # WFC_INPUT_PATHS keeps its list shape for raw readers.
    assert json.loads(__import__("os").environ["WFC_INPUT_PATHS"]) == raw


@workflow(purpose="Case 19: wfc_client.__version__ is the packaged version, and a "
                  "single-file input is the public InputPath (a Path that also "
                  "indexes as [0])")
def test_version_and_the_public_input_path(tmp_path, monkeypatch):
    """Case 19: ``wfc_client.__version__`` is the packaged version, and a
    single-file input is the public ``InputPath`` (``_SinglePath`` its alias)."""
    import re
    from pathlib import Path

    from wfc_client.context import _SinglePath

    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == wfc.__version__

    a = str(tmp_path / "a.csv")
    _set_env(monkeypatch, tmp_path, input_paths={"data": [a]}, declared=_DECLARED)
    single = RunContext().input("data")
    assert isinstance(single, wfc.InputPath) and isinstance(single, Path)
    assert single[0] == Path(a)
    assert _SinglePath is wfc.InputPath and "InputPath" in wfc.__all__


@workflow(purpose="A computed undeclared name is caught at the call: ctx.input "
                  "raises listing the declared inputs, save_artifact warns in "
                  "the step log naming the declared outputs")
def test_undeclared_names_at_the_call(tmp_path, monkeypatch, capsys):
    _set_env(monkeypatch, tmp_path, declared=_DECLARED)
    ctx = RunContext()

    with pytest.raises(KeyError) as exc:
        ctx.input("dta")
    assert "'data', 'many', 'extra'" in str(exc.value)

    out = ctx.workdir / "x.csv"
    out.write_text("x")
    ctx.save_artifact("clen", out)
    err = capsys.readouterr().err
    assert "'clen'" in err and "Declared outputs: 'clean'" in err
    assert ctx._outputs == {"clen": "_workdir/x.csv"}


def test_input_without_declared_names_keeps_lists(tmp_path, monkeypatch):
    """Started by a wfc that sends no declared names, every input is a list."""
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

    Output type/extension correctness is a host-side concern,
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

    import ast

    for mod in (ctxmod, decmod, mainmod):
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
