"""The registry base in ``wf-canvas.toml``, and the project's git remote.

``set_registry_url`` sets the one ``[registry] url`` key by a text edit:
every other byte of the file stays as it was, whichever of the three shapes
the file is in (no table, a table without the key, a table with it), and
whatever its line endings. ``read_config`` reads the key back.

``git_remote_url`` reads ``origin``, else the only remote; remotes are made
with real ``git remote add`` in the project ``wfc init`` produced.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from axiom_annotations import workflow


def _config(project: Path) -> Path:
    return project / ".wfc" / "wf-canvas.toml"


@workflow(purpose="Setting [registry] url on the config wfc init wrote appends "
                  "the table and changes no byte before it; read_config then "
                  "answers the base, and setting the same base again writes "
                  "the same bytes")
def test_setting_the_base_appends_the_table_and_keeps_the_file(tmp_project):
    from wfc.persistence import read_config, set_registry_url

    path = _config(tmp_project)
    before = path.read_bytes()
    assert read_config(tmp_project)["registry"] is None

    set_registry_url(tmp_project, "ghcr.io/lab/project")

    after = path.read_bytes()
    newline = b"\r\n" if b"\r\n" in before else b"\n"  # init writes the OS's
    assert after.startswith(before)
    assert after[len(before):] == (b'\n[registry]\nurl = "ghcr.io/lab/project"\n'
                                   .replace(b"\n", newline))
    assert read_config(tmp_project)["registry"] == {"url": "ghcr.io/lab/project"}

    set_registry_url(tmp_project, "ghcr.io/lab/project")
    assert path.read_bytes() == after


@pytest.mark.parametrize(("newline"), ["\n", "\r\n"])
@pytest.mark.parametrize(("body", "expected"), [
    # A table holding the key: only the key's line changes, indent kept.
    ('# top\n[registry]\n  # where images go\n  url = "old.example/x"  # was\n'
     'other = 1\n\n[dvc]\nurl = "file:///a"\n',
     '# top\n[registry]\n  # where images go\n  url = "ghcr.io/lab/project"\n'
     'other = 1\n\n[dvc]\nurl = "file:///a"\n'),
    # A table without the key, another table after it: the line goes under
    # the header.
    ('[registry]\nother = 1\n[dvc]\nurl = "file:///a"\n',
     '[registry]\nurl = "ghcr.io/lab/project"\nother = 1\n[dvc]\nurl = "file:///a"\n'),
    # No table, no final newline: one is added before the appended table.
    ('[dvc]\nurl = "file:///a"',
     '[dvc]\nurl = "file:///a"\n\n[registry]\nurl = "ghcr.io/lab/project"\n'),
])
def test_the_edit_touches_only_the_registry_key(tmp_project, body, expected, newline):
    from wfc.persistence import read_config, set_registry_url

    path = _config(tmp_project)
    path.write_bytes(body.replace("\n", newline).encode("utf-8"))

    set_registry_url(tmp_project, "ghcr.io/lab/project")

    assert path.read_bytes() == expected.replace("\n", newline).encode("utf-8")
    assert read_config(tmp_project)["registry"] == {"url": "ghcr.io/lab/project"}


def test_a_base_spelled_another_way_is_refused_untouched(tmp_project):
    """A dotted ``registry.url`` key cannot be edited in place; appending a
    second definition would break the file, so nothing is written."""
    from wfc.persistence import set_registry_url

    path = _config(tmp_project)
    path.write_bytes(b'registry.url = "old.example/x"\n\n[dvc]\nurl = "file:///a"\n')
    before = path.read_bytes()

    with pytest.raises(ValueError, match=r"\[registry\] url"):
        set_registry_url(tmp_project, "ghcr.io/lab/project")
    assert path.read_bytes() == before


def test_a_registry_url_that_is_not_a_string_is_refused(tmp_project):
    from wfc.persistence import read_config

    _config(tmp_project).write_bytes(b"[registry]\nurl = 3\n")

    with pytest.raises(ValueError, match="not a string"):
        read_config(tmp_project)


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)


def test_the_remote_read_is_origin_else_the_only_remote(tmp_project):
    from wfc.version import git_remote_url

    assert git_remote_url(tmp_project) is None

    _git(tmp_project, "remote", "add", "upstream", "git@github.com:Lab/Up.git")
    assert git_remote_url(tmp_project) == "git@github.com:Lab/Up.git"

    _git(tmp_project, "remote", "add", "fork", "https://github.com/me/Up")
    assert git_remote_url(tmp_project) is None

    _git(tmp_project, "remote", "add", "origin", "https://github.com/Lab/Project.git")
    assert git_remote_url(tmp_project) == "https://github.com/Lab/Project.git"


def test_the_remote_read_outside_git_is_none(tmp_path):
    from wfc.version import git_remote_url

    assert git_remote_url(tmp_path) is None
