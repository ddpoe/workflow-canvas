"""Persistence: the ``[updates]`` settings ``read_config`` returns.

Tier 2: ``[updates] check`` and ``prereleases`` are read from a project's
``wf-canvas.toml`` through ``read_config``, the reader the canvas's about
route hands the update check its settings from. Absent means the check is on
and betas follow the installed wfc (``prereleases`` ``None``); anything but a
table of booleans is refused naming the key, the value and the fix. A project
``wfc init`` makes carries the section, commented, at its default.
"""
from __future__ import annotations

import pytest
from axiom_annotations import workflow

from wfc import layout
from wfc.persistence import read_config


def _write_config(root, text: str) -> None:
    """Write the project's wf-canvas.toml with exactly *text*."""
    marker = layout.marker_path(root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(text, encoding="utf-8")


@workflow(purpose="A project made by wfc init reads [updates] as the default (check on, "
                  "betas following the install), and its file holds the section with "
                  "check = true, a comment saying what the check sends and does, and a "
                  "commented-out prereleases = true line explaining the default",
          inputs="A project made by wfc init",
          outputs="read_config's updates entry and the generated wf-canvas.toml text")
def test_a_new_project_holds_the_update_check_on_with_its_comment(tmp_project):
    """Case 15: the template's section parses to the default and explains itself."""
    assert read_config(tmp_project)["updates"] == {"check": True, "prereleases": None}

    lines = layout.marker_path(tmp_project).read_text(encoding="utf-8").splitlines()
    header = lines.index("[updates]")
    assert lines[header + 1] == "check = true"
    comment = " ".join(line for line in lines[:header] if line.startswith("#"))
    assert "PyPI" in comment and "installs nothing" in comment
    assert "# prereleases = true" in lines[header + 2:]
    explained = " ".join(lines[header + 2:lines.index("# prereleases = true")])
    assert "beta" in explained and "stable" in explained


@workflow(purpose="With no [updates] section the check is on and prereleases is None; "
                  "each key set is read back as written, the other keeping its default",
          inputs="Config files with no [updates], check = false, and prereleases set "
                 "true or false",
          outputs="The updates entry of read_config's result")
def test_the_update_settings_default_and_obey_the_config(tmp_path):
    """Case 5's setting half: absent is on; each key read back alone."""
    _write_config(tmp_path, "")
    assert read_config(tmp_path)["updates"] == {"check": True, "prereleases": None}

    _write_config(tmp_path, "[updates]\ncheck = false\n")
    assert read_config(tmp_path)["updates"] == {"check": False, "prereleases": None}

    _write_config(tmp_path, "[updates]\nprereleases = true\n")
    assert read_config(tmp_path)["updates"] == {"check": True, "prereleases": True}

    _write_config(tmp_path, "[updates]\ncheck = true\nprereleases = false\n")
    assert read_config(tmp_path)["updates"] == {"check": True, "prereleases": False}


@pytest.mark.parametrize(
    "text, named",
    [
        ('updates = "off"\n', 'updates = "off" is not a table'),
        ('[updates]\ncheck = "yes"\n', '[updates] check = "yes"'),
        ("[updates]\ncheck = 0\n", "[updates] check = 0"),
        ("[updates]\nprereleases = 1\n", "[updates] prereleases = 1"),
        ('[updates]\nprereleases = "true"\n', '[updates] prereleases = "true"'),
    ],
    ids=["section-string", "check-string", "check-number", "prereleases-number",
         "prereleases-quoted"],
)
@workflow(purpose="An [updates] value that is not a table, or a check or prereleases "
                  "that is not true or false, is refused with a ValueError naming the "
                  "key, the value as written and the fix",
          inputs="Config files with a malformed [updates] section",
          outputs="The ValueError read_config raises")
def test_a_malformed_update_setting_is_refused_naming_the_fix(tmp_path, text, named):
    """Case 7: every malformed form refused, in the user's own spelling."""
    _write_config(tmp_path, text)
    with pytest.raises(ValueError) as exc:
        read_config(tmp_path)
    message = str(exc.value)
    assert named in message
    if "is not a table" in named:
        assert "[updates]" in message and "check = true" in message
    else:
        assert "true or false" in message
    assert "delete the line" in message
