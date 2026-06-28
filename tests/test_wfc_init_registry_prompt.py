"""Regression guard: `wfc init` writes no `[registry]` block.

The registry prompt was backed out (pev-instance-2026-05-17) and stays gone.
This cycle (D-2) makes `wfc init` interactive-by-default for the *archive
location only*, with `--yes` for fully non-interactive runs — so the old
"init never calls input()" assertion is retired. What remains binding:

  - `wfc init --yes` runs fully non-interactively (no `input()` at all).
  - No `[registry]` block is ever written, prompt or no prompt.

Forward-compat parsing of a hand-authored `[registry]` block by `read_config`
is covered by `test_envs.py::test_read_config_registry_block`.
"""

from __future__ import annotations

import subprocess
import tomllib


def test_wfc_init_yes_is_non_interactive_and_writes_no_registry(tmp_path, monkeypatch):
    from wfc.init import init_project

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    project = tmp_path / "proj"

    # Repo exists (covers the case where Cycle B used to derive a registry
    # default from `git remote get-url origin`) — must still not prompt.
    project.mkdir()
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(project), "remote", "add", "origin",
         "git@github.com:dante/myproject.git"],
        check=True, capture_output=True,
    )

    input_calls: list[str] = []

    def _trap_input(prompt=""):
        input_calls.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", _trap_input)

    # --yes path must never prompt.
    init_project(project, assume_yes=True)

    assert input_calls == [], (
        f"`wfc init --yes` must be fully non-interactive — got "
        f"{len(input_calls)} prompt(s): {input_calls!r}"
    )

    parsed = tomllib.loads(
        (project / ".wfc" / "wf-canvas.toml").read_text(encoding="utf-8")
    )
    assert "registry" not in parsed, (
        f"`wfc init` must not write a [registry] block; got "
        f"{parsed.get('registry')!r}"
    )
