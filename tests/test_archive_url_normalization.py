"""Archive-location normalization and validation for ``wfc init``.

The historic default emitted ``file://C:/...`` on Windows — a form DVC's
config schema rejects, so every push failed on a fresh Windows project
while init's readiness report said OK.  Locations are now stored as plain
absolute paths (DVC accepts those on every platform); ``file://`` and
unknown/uninstalled remote schemes are refused at init time; and doctor's
``check_dvc`` deep-validates the config through DVC itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiom_annotations import workflow

from wfc.init import _normalize_archive, init_project
from wfc.preflight import check_dvc


@workflow(
    purpose="user-typed archive locations normalize to plain absolute "
            "paths; file://, unknown schemes, and schemes whose DVC plugin "
            "is not installed are refused with an actionable message"
)
@pytest.mark.parametrize(
    "case",
    ["relative", "home", "absolute", "https", "file_url", "unknown", "missing_plugin"],
)
def test_normalize_archive_user_inputs(case, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    project = tmp_path / "proj"
    project.mkdir()

    if case == "relative":
        # A bare relative path resolves against the project root — for a
        # default `wfc init` (dir = cwd) that IS the current directory.
        out = _normalize_archive("my-archive", project)
        assert out == (project / "my-archive").resolve().as_posix()
    elif case == "home":
        # expanduser reads the env (USERPROFILE on Windows, HOME on POSIX).
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("HOME", str(home))
        out = _normalize_archive("~/wfc-archives/proj", project)
        assert out == (home / "wfc-archives" / "proj").resolve().as_posix()
    elif case == "absolute":
        target = tmp_path / "elsewhere" / "archive"
        out = _normalize_archive(str(target), project)
        assert out == target.resolve().as_posix()
    elif case == "https":
        # dvc-http ships with the base dvc dependency → accepted verbatim.
        out = _normalize_archive("https://archive.example.org/store", project)
        assert out == "https://archive.example.org/store"
    elif case == "file_url":
        with pytest.raises(ValueError, match="no file:// prefix"):
            _normalize_archive("file:///C:/somewhere/archive", project)
    elif case == "unknown":
        with pytest.raises(ValueError, match="unsupported scheme ftp://"):
            _normalize_archive("ftp://host/archive", project)
    elif case == "missing_plugin":
        # s3 is a recognized DVC scheme but dvc[s3] is not in the
        # dependency set — refuse with the install hint.
        with pytest.raises(ValueError, match=r"pip install 'dvc\[s3\]'"):
            _normalize_archive("s3://bucket/prefix", project)


@workflow(
    purpose="a fresh `wfc init --yes` project passes doctor's DVC "
            "deep-validation — DVC itself parses the config and resolves "
            "the default remote (the check that would have caught the "
            "file://C:/ regression)"
)
def test_init_default_archive_accepted_by_dvc(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    project = tmp_path / "proj"

    init_project(project, assume_yes=True)

    result = check_dvc(project)
    assert result.status == "ok", result.message


@workflow(
    purpose="doctor fails (not OK-then-push-crash) when .dvc/config holds "
            "a remote URL DVC's schema rejects"
)
def test_doctor_catches_dvc_rejected_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    project = tmp_path / "proj"

    init_project(project, assume_yes=True)

    config_path = project / ".dvc" / "config"
    config = config_path.read_text(encoding="utf-8")
    patched = "\n".join(
        "url = badscheme://nowhere/archive" if line.strip().startswith("url =")
        else line
        for line in config.splitlines()
    )
    config_path.write_text(patched, encoding="utf-8")

    result = check_dvc(project)
    assert result.status == "fail"
    assert "DVC rejected the archive configuration" in result.message
