"""The docs site serves the wfc plugin as one zip a chat's "upload a plugin" page accepts.

claude.ai and ChatGPT take a plugin archive whose manifest sits at the zip's root, so the
docs build zips ``plugins/wfc`` with ``.claude-plugin/plugin.json`` and
``.codex-plugin/plugin.json`` at the top and every skill under ``skills/``.
"""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "userdocs" / "_sphinx" / "plugin_zip.py"


def _plugin_zip():
    spec = importlib.util.spec_from_file_location("plugin_zip", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_plugin_zip_holds_the_plugin_with_its_manifests_at_the_root(tmp_path):
    out = tmp_path / "wfc-plugin.zip"

    _plugin_zip().build_plugin_zip(_REPO / "plugins" / "wfc", out)

    names = zipfile.ZipFile(out).namelist()
    assert ".claude-plugin/plugin.json" in names
    assert ".codex-plugin/plugin.json" in names
    plugin_files = sorted(
        p.relative_to(_REPO / "plugins" / "wfc").as_posix()
        for p in (_REPO / "plugins" / "wfc").rglob("*") if p.is_file()
    )
    assert sorted(names) == plugin_files


def test_two_builds_of_the_same_plugin_are_the_same_bytes(tmp_path):
    builder = _plugin_zip()
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"

    builder.build_plugin_zip(_REPO / "plugins" / "wfc", first)
    builder.build_plugin_zip(_REPO / "plugins" / "wfc", second)

    assert first.read_bytes() == second.read_bytes()
