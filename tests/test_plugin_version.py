"""The wfc plugin names the wfc version it is written for, and it is the version that ships.

An agent compares ``wfc --version`` with the version using-wfc's "Check the versions"
names, and tells the user to update the plugin when wfc is newer. That only works when
the skill, the Claude Code and Codex manifests and the package all name one version.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from packaging.version import Version

_REPO = Path(__file__).resolve().parents[1]
_PLUGIN = _REPO / "plugins" / "wfc"


def test_the_skills_and_manifests_name_the_version_that_ships():
    """using-wfc's written-for version and both manifests equal pyproject's version."""
    with open(_REPO / "pyproject.toml", "rb") as f:
        package = Version(tomllib.load(f)["tool"]["poetry"]["version"])

    skill = (_PLUGIN / "skills" / "using-wfc" / "SKILL.md").read_text(encoding="utf-8")
    written_for = re.search(r"These skills are written for \*\*wfc ([^*]+)\*\*", skill)
    assert written_for, "using-wfc's Check the versions names no wfc version"
    manifests = {
        name: json.loads((_PLUGIN / name / "plugin.json").read_text(encoding="utf-8"))["version"]
        for name in (".claude-plugin", ".codex-plugin")
    }

    assert Version(written_for.group(1)) == package
    # The manifests write a pre-release as semver (0.8.0-beta.4 for 0.8.0b4).
    assert {name: Version(v) for name, v in manifests.items()} == {
        name: package for name in manifests}
