"""A remote DVC itself has rewritten is still a configured remote.

``wfc init`` mirrors the archive to ``.dvc/config`` as ``[remote "default"]``.
Once a user runs ``dvc remote modify``, DVC rewrites the file and quotes the
header as ``['remote "default"']``. The config here is produced by the real
DVC CLI, never hand-written.
"""
from __future__ import annotations

import configparser
import subprocess
import sys

import pytest

from wfc import layout
from wfc.init import init_project
from wfc.persistence import read_config
from wfc.storage import ensure_dvc_ready, has_remote_configured
from wfc.storage.setup import init_dvc
from wfc.storage.transport import remote_name_of, remote_sections
from tests.fixtures.fakes import stub_readiness_probes


def _dvc(root, *args):
    subprocess.run([sys.executable, "-m", "dvc", *args], cwd=root, check=True,
                   capture_output=True)


@pytest.fixture
def modified_project(tmp_path, monkeypatch):
    """A project scaffolded by wfc init whose remote DVC then modified."""
    archive = tmp_path / "archive"
    root = tmp_path / "proj"
    with monkeypatch.context() as mp:
        stub_readiness_probes(mp)
        init_project(root, archive=str(archive), assume_yes=True)
    _dvc(root, "remote", "modify", "default", "verify", "true")
    return root, read_config(root)["dvc"]["url"]


def test_a_remote_rewritten_by_dvc_remote_modify_is_configured(modified_project):
    root, url = modified_project
    text = layout.dvc_config_path(root).read_text(encoding="utf-8")
    assert "['remote \"default\"']" in text  # DVC's own rewrite, as users get it

    assert has_remote_configured(root) is True
    assert ensure_dvc_ready(root)["url"] == url


def test_wfc_init_again_updates_the_rewritten_remote_in_place(modified_project):
    root, url = modified_project
    init_dvc(root, {"url": url})

    parser = configparser.ConfigParser()
    parser.read(layout.dvc_config_path(root))
    assert list(remote_sections(parser)) == ["default"]
    _dvc(root, "remote", "list")  # DVC still reads the file


def test_a_remote_added_through_dvc_remote_add_is_configured(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    _dvc(root, "init", "--no-scm")
    assert has_remote_configured(root) is False
    _dvc(root, "remote", "add", "-d", "store", (tmp_path / "store").as_uri())
    _dvc(root, "remote", "modify", "store", "verify", "true")
    assert has_remote_configured(root) is True


@pytest.mark.parametrize("section, name", [
    ('remote "default"', "default"),
    ("'remote \"default\"'", "default"),
    ("remote.legacy", "legacy"),
    ("core", None),
    ("'core'", None),
    ('remote ""', None),
    ("remote.", None),
])
def test_remote_name_of_reads_every_header_spelling(section, name):
    assert remote_name_of(section) == name
