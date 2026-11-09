"""wfc's own changelog, as the canvas's What's new reads it (request case 8).

Covers:
  - Tier 2: a changelog with the real heading shape parses into its
    versions; the installed version's sections list Upgrading first and
    Known issues last, items kept as markdown.
  - Tier 2: a range lists every version since the last one seen, newest
    first; [Unreleased] is never a version.
  - Tier 2: a version with no section, or no packaged changelog (a source
    checkout), answers the Changelog link instead.
  - Tier 2: the canvas build's copy step carries the changelog into dist/
    and fails when it is missing.
  - Tier 1: the docs link follows the installed version's channel.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.release import (
    changelog_url,
    changelog_versions,
    docs_url,
    installed_changelog,
    parse_changelog,
)

ROOT = Path(__file__).resolve().parent.parent
COPY_SCRIPT = ROOT / "wfc" / "canvas" / "static" / "scripts" / "copy-changelog.mjs"

CHANGELOG = """\
# Changelog

Notable changes to Workflow Canvas.

## [Unreleased]

### Added
- Something not released yet.

## [0.8.0b3] — 2026-11-07

### Added
- `wfc prune-envs` stops what an interrupted run left behind.
- The guide has a **Known Issues** page.

### Known issues
Each is on the guide's Known Issues page with its way around.
- A reference to a cached run is refused.
- `wfc check-pipeline` refuses a node with two inputs.

### Upgrading
- Keep your project on a local disk ([why](https://example.org/why)).

### Fixed
- Ctrl-C stops the run's container.

## [0.8.0b2] — 2026-11-06

### Upgrading
- `wfc run-pipeline` takes `--pipeline` only.

## [0.7.0] — 2026-10-27

### Changed
- Older change.
"""


@workflow(purpose="The installed version's changelog entry lists Upgrading "
                  "first and Known issues last, the rest in the file's order, "
                  "each item kept as markdown")
def test_the_installed_version_reads_upgrading_first_and_known_issues_last(tmp_path):
    packaged = tmp_path / "CHANGELOG.md"
    packaged.write_text(CHANGELOG, encoding="utf-8")

    answer = installed_changelog("0.8.0b3", path=packaged)

    assert answer["link"] is None
    (entry,) = answer["versions"]
    assert (entry["version"], entry["date"]) == ("0.8.0b3", "2026-11-07")
    assert [s["heading"] for s in entry["sections"]] == [
        "Upgrading", "Added", "Fixed", "Known issues"]
    upgrading, added, _, known = entry["sections"]
    assert upgrading["items"] == [
        "Keep your project on a local disk ([why](https://example.org/why))."]
    assert added["items"][0] == "`wfc prune-envs` stops what an interrupted run left behind."
    assert known["intro"] == "Each is on the guide's Known Issues page with its way around."
    assert len(known["items"]) == 2


@workflow(purpose="A reader who last saw an older version is shown every "
                  "version since it, newest first, never [Unreleased]")
def test_a_range_lists_every_version_since_the_last_seen_newest_first():
    entries = parse_changelog(CHANGELOG)

    assert [e["version"] for e in entries] == ["0.8.0b3", "0.8.0b2", "0.7.0"]
    shown = changelog_versions(entries, "0.8.0b3", since="0.7.0")
    assert [e["version"] for e in shown] == ["0.8.0b3", "0.8.0b2"]
    assert [e["version"] for e in changelog_versions(entries, "0.8.0b3")] == ["0.8.0b3"]
    assert [e["version"] for e in changelog_versions(entries, "0.8.0b3", since="0.8.0b3")] == [
        "0.8.0b3"]


@workflow(purpose="A version the changelog has no section for, or no "
                  "packaged changelog at all (a source checkout), answers the "
                  "Changelog link instead of sections")
def test_no_section_or_no_packaged_file_answers_the_changelog_link(tmp_path):
    packaged = tmp_path / "CHANGELOG.md"
    packaged.write_text(CHANGELOG, encoding="utf-8")

    assert installed_changelog("0.9.0", path=packaged) == {
        "versions": [], "link": changelog_url()}
    assert installed_changelog("0.9.0", since="0.7.0", path=packaged) == {
        "versions": [], "link": changelog_url()}
    assert installed_changelog("0.8.0b3", path=tmp_path / "absent.md") == {
        "versions": [], "link": changelog_url()}
    assert changelog_url().endswith("CHANGELOG.md")


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    return node


@workflow(purpose="The canvas build's copy step puts the changelog into dist/, "
                  "and fails, naming the file, when it is missing")
def test_the_build_step_carries_the_changelog_and_fails_without_it(tmp_path):
    node = _node()
    source = tmp_path / "CHANGELOG.md"
    source.write_text(CHANGELOG, encoding="utf-8")
    dist = tmp_path / "dist"

    copied = subprocess.run([node, str(COPY_SCRIPT), str(source), str(dist)],
                            capture_output=True, text=True, timeout=60)
    assert copied.returncode == 0, copied.stderr
    assert (dist / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG

    missing = subprocess.run([node, str(COPY_SCRIPT), str(tmp_path / "gone.md"),
                              str(tmp_path / "dist2")],
                             capture_output=True, text=True, timeout=60)
    assert missing.returncode != 0
    assert "gone.md" in missing.stderr
    assert not (tmp_path / "dist2" / "CHANGELOG.md").exists()


def test_the_docs_link_follows_the_installed_versions_channel():
    """A release reads stable, a pre-release or an unknown version latest."""
    assert docs_url("0.7.0", "x.html") == "https://workflow-canvas.readthedocs.io/en/stable/x.html"
    assert docs_url("0.8.0b3") == "https://workflow-canvas.readthedocs.io/en/latest/"
    assert docs_url(None).endswith("/latest/")
    assert docs_url("not-a-version").endswith("/latest/")
