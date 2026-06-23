"""v1 regression guard: `wfc init` does not prompt for a registry URL.

Per ADR-019 v1 carve-out (amended 2026-05-17), registry support is deferred
to v1.x. v1's `wfc init` must:
  - Complete without invoking `input()` (no interactive prompt).
  - Write no `[registry]` block to `.wfc/wf-canvas.toml`.

Forward-compat parsing of a hand-authored `[registry]` block by `read_config`
is covered by `test_envs.py::test_read_config_registry_block`.
"""

from __future__ import annotations

import subprocess
import tomllib


def test_wfc_init_does_not_prompt_for_registry_in_v1(tmp_path, monkeypatch):
    from wfc.init import init_project

    # Repo exists (covers the case where Cycle B used to derive a default
    # from `git remote get-url origin`) — v1 must still not prompt.
    subprocess.run(["git", "init", str(tmp_path)], check=True,
                   capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin",
         "git@github.com:dante/myproject.git"],
        check=True, capture_output=True,
    )

    input_calls: list[str] = []

    def _trap_input(prompt=""):
        input_calls.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", _trap_input)

    init_project(tmp_path)

    assert input_calls == [], (
        f"v1 `wfc init` must not call input() — got {len(input_calls)} "
        f"prompt(s): {input_calls!r}"
    )

    parsed = tomllib.loads(
        (tmp_path / ".wfc" / "wf-canvas.toml").read_text(encoding="utf-8")
    )
    assert "registry" not in parsed, (
        f"v1 `wfc init` must not write a [registry] block; got "
        f"{parsed.get('registry')!r}"
    )
