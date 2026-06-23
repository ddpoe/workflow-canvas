"""Tier 2 tests for ``wfc exec-method`` (ADR-019 Cycle D, fix-pass 5).

``wfc exec-method`` is the in-container entrypoint of the container-dispatch
path. Its job is intentionally tiny: validate ``WFC_RUN_DIR`` and ``--script``,
then subprocess-exec the method script with the inherited environment. It
does NOT register runs, hit the DB, compute cache keys, or collect outputs —
the outer host ``wfc run-step`` owns all of that.

These tests cover the four behaviours that matter at the verb's boundary:
happy path, missing WFC_RUN_DIR, missing script, and verbatim exit-code
propagation from the method script.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dflow.core.decorators import workflow

from wfc.cli import exec_method


@workflow(purpose="exec_method runs the script and returns 0 when it succeeds; "
                  "script can read WFC_RUN_DIR from its environment and write "
                  "outputs there")
def test_exec_method_happy_path(tmp_path: Path, monkeypatch, capsys):
    """Happy path: script writes to WFC_RUN_DIR/out.txt; exec_method returns 0
    and the file materializes with the expected contents."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    script = tmp_path / "method.py"
    script.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['WFC_RUN_DIR'], 'out.txt').write_text('hello')\n"
    )

    monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
    monkeypatch.setenv("WFC_NODE_ID", "n1")
    monkeypatch.setenv("WFC_SAMPLE", "s1")
    monkeypatch.setenv("WFC_VARIANT", "default")
    monkeypatch.setenv("WFC_PIPELINE_ID", "p1")

    rc = exec_method(run_id=1, node_id="n1", script_path=str(script))

    assert rc == 0
    out = run_dir / "out.txt"
    assert out.exists()
    assert out.read_text() == "hello"


@workflow(purpose="exec_method bails with a clear error and rc != 0 when "
                  "WFC_RUN_DIR is missing — caller forgot to forward the env "
                  "from the outer wfc run-step dispatch")
def test_exec_method_missing_wfc_run_dir(tmp_path: Path, monkeypatch, capsys):
    """Missing WFC_RUN_DIR: exec_method must not silently run; it returns
    non-zero and prints an error mentioning WFC_RUN_DIR to stderr."""
    script = tmp_path / "method.py"
    script.write_text("print('should not run')\n")

    monkeypatch.delenv("WFC_RUN_DIR", raising=False)

    rc = exec_method(run_id=1, node_id="n1", script_path=str(script))

    assert rc != 0
    err = capsys.readouterr().err
    assert "WFC_RUN_DIR" in err


@workflow(purpose="exec_method bails with rc != 0 and a clear error when the "
                  "--script path doesn't exist on disk")
def test_exec_method_missing_script(tmp_path: Path, monkeypatch, capsys):
    """Missing script: exec_method validates the path before exec'ing and
    returns non-zero with stderr mentioning the bad path."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    bad_script = tmp_path / "does-not-exist.py"

    monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))

    rc = exec_method(run_id=1, node_id="n1", script_path=str(bad_script))

    assert rc != 0
    err = capsys.readouterr().err
    assert str(bad_script) in err


@workflow(purpose="exec_method returns the method script's exit code verbatim "
                  "— a nonzero rc from the script propagates unchanged so the "
                  "outer wfc run-step can mark the run as failed")
def test_exec_method_propagates_nonzero_exit(tmp_path: Path, monkeypatch):
    """The method script's exit code propagates verbatim. ``sys.exit(7)``
    inside the script becomes ``exec_method(...) == 7``."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    script = tmp_path / "method.py"
    script.write_text("import sys\nsys.exit(7)\n")

    monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))

    rc = exec_method(run_id=1, node_id="n1", script_path=str(script))

    assert rc == 7
