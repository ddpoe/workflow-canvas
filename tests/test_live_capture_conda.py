"""Unit tests for conda live-env capture of python-free environments.

``wfc register-env conda:<name>`` must be able to mirror an env that
contains no python interpreter at all (e.g. an R-only conda-forge env):
the explicit list still captures, and the pip-freeze slot is filled with
the absent-pip sentinel instead of failing on the missing binary.

Covers the two production seams of that path:

  * :func:`wfc.environments.verbs._stage_live_env_source` conda branch — real
    ``resolve_conda_env_dir`` + ``_find_python_in_env`` against an
    on-disk env prefix; only the conda subprocess is stubbed.
  * :func:`wfc.environments.introspect.pip_freeze_best_effort` — the
    sentinel-emission branch that distinguishes "pip is absent" from
    "pip crashed".

Tier 2 / Tier 1 respectively; no external tools needed, runs in the
default suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from axiom_annotations import workflow

from tests.fixtures.fakes import fake_conda_list_explicit, fake_subprocess_run


@workflow(
    purpose="Live capture of a conda env with no python binary stages the "
            "explicit list plus the absent-pip sentinel — the env prefix is "
            "resolved on disk via the configured [conda] root, without "
            "requiring an interpreter inside the env."
)
def test_stage_live_env_source_captures_python_free_conda_env(tmp_path, monkeypatch):
    from wfc.environments.verbs import _stage_live_env_source
    from wfc.environments.introspect import PIP_MISSING_SENTINEL

    # A realistic env prefix on disk: conda-meta present, no python binary
    # anywhere (bin/python, python.exe, Scripts/python.exe all absent).
    conda_root = tmp_path / "conda_root"
    env_dir = conda_root / "envs" / "renv"
    (env_dir / "conda-meta").mkdir(parents=True)

    # Configure [conda] root so resolution stays inside the tmp tree —
    # otherwise resolve_conda_env_dir falls through to _detect_conda_root,
    # which subprocesses `conda info --base`.
    project_dir = tmp_path / "project"
    (project_dir / ".wfc").mkdir(parents=True)
    (project_dir / ".wfc" / "wf-canvas.toml").write_text(
        f'[conda]\nroot = "{conda_root.as_posix()}"\n', encoding="utf-8"
    )

    explicit_list = (
        "# This file may be used to create an environment using:\n"
        "@EXPLICIT\n"
        "https://conda.anaconda.org/conda-forge/linux-64/"
        "r-base-4.4.1-hd8f26a4_0.conda#0123456789abcdef0123456789abcdef\n"
    )
    seen: dict[str, Path] = {}

    # The conda binary subprocess is the one true external edge — stub it.
    fake_conda_list_explicit(monkeypatch, explicit_list, seen=seen)

    source = _stage_live_env_source("conda:renv", project_dir)

    assert seen["env_path"] == env_dir.resolve()
    assert source == {
        "explicit_list_content": explicit_list,
        "pip_freeze_content": PIP_MISSING_SENTINEL,
    }


def test_pip_freeze_best_effort_distinguishes_missing_pip_from_other_failures(
    monkeypatch,
):
    """`python -m pip` failing with 'No module named pip' yields the
    sentinel; any other nonzero exit still raises — swallowing it would
    hide real env problems behind a fake "pip absent" answer."""
    from wfc.environments import introspect as env_introspect

    def fake_run_missing_pip(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, returncode=1, stdout="",
            stderr="/opt/conda/bin/python: No module named pip\n",
        )

    fake_subprocess_run(monkeypatch, fake_run_missing_pip)
    assert (
        env_introspect.pip_freeze_best_effort("/opt/conda/bin/python")
        == env_introspect.PIP_MISSING_SENTINEL
    )

    def fake_run_other_failure(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, returncode=1, stdout="",
            stderr="error: unrelated pip crash\n",
        )

    fake_subprocess_run(monkeypatch, fake_run_other_failure)
    with pytest.raises(RuntimeError, match="unrelated pip crash"):
        env_introspect.pip_freeze_best_effort("/opt/conda/bin/python")
