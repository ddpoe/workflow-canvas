"""Subsystem tests: live capture of a conda env on a non-Linux host.

A Windows or macOS conda env cannot be installed into a linux-64 image from
its own explicit list, so live capture records what the user asked for
instead: each requested package (``conda env export --from-history``) pinned
to the version installed (``conda list --json``), the channels the installed
packages came from, and the pip set (the ``pypi``-channel packages). The host's own
capture is kept for the env's ``host/`` folder.

Everything is driven from the files under ``tests/fixtures/conda_solve/``;
conda, pip and the host platform are faked, so the Windows branch runs on
any machine.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from axiom_annotations import workflow

from tests.fixtures.fakes import (fake_conda_list_explicit,
                                  fake_conda_solve_capture, fake_pip_freeze,
                                  stub_host_conda_platform)

FIXTURES = Path(__file__).parent / "fixtures" / "conda_solve"


def _fixture(name: str) -> str:
    """Return a fixture file's text."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def _installed() -> list[dict]:
    """The fixture env's ``conda list --json`` answer."""
    return json.loads(_fixture("conda-list.json"))


def _windows_env(tmp_path: Path) -> tuple[Path, Path]:
    """A conda env prefix with a python, under a project whose [conda] root is it.

    The interpreter exists in both the Windows and the POSIX layout, so the
    env resolves on any machine.

    Returns:
        ``(project_dir, env_dir)``.
    """
    conda_root = tmp_path / "conda_root"
    env_dir = conda_root / "envs" / "imaging"
    (env_dir / "conda-meta").mkdir(parents=True)
    (env_dir / "bin").mkdir()
    (env_dir / "python.exe").write_bytes(b"")
    (env_dir / "bin" / "python").write_bytes(b"")
    project_dir = tmp_path / "project"
    (project_dir / ".wfc").mkdir(parents=True)
    (project_dir / ".wfc" / "wf-canvas.toml").write_text(
        f'[conda]\nroot = "{conda_root.as_posix()}"\n', encoding="utf-8")
    return project_dir, env_dir


@workflow(
    purpose="Live capture of conda:<env> on a Windows host solves for "
            "linux-64: the source carries each requested package pinned "
            "name=<installed version> from the channels its packages came "
            "from, a pip "
            "set of only the pypi-channel packages and no host explicit list; "
            "the host's explicit list, export, pinned spec, full pip freeze "
            "and platform/conda version/capture time are kept for host/; and "
            "the conda row's source check lets the payload through"
)
def test_a_windows_live_capture_pins_requested_packages_and_keeps_the_host_record(
        tmp_path, monkeypatch):
    from wfc import layout
    from wfc.environments.backends import backend_row
    from wfc.environments.introspect import (HOST_CAPTURE_SOURCE_KEY,
                                             SOLVE_SOURCE_KEY)
    from wfc.environments.verbs import _stage_live_env_source

    project_dir, env_dir = _windows_env(tmp_path)
    win_list = _fixture("win-64-explicit-list.txt")
    # conda on Windows writes CRLF and a prefix: line; host/ keeps it as given.
    from_history = _fixture("from-history.yml").replace("\n", "\r\n")
    host_freeze = _fixture("pip-freeze.txt")
    seen: dict = {}
    stub_host_conda_platform(monkeypatch, "win-64")
    fake_conda_list_explicit(monkeypatch, win_list)
    fake_conda_solve_capture(monkeypatch, from_history=from_history,
                             list_json=_installed(), conda_version="24.11.3",
                             seen=seen)
    fake_pip_freeze(monkeypatch, host_freeze)

    source = _stage_live_env_source("conda:imaging", project_dir)

    assert seen == {"from_history": env_dir.resolve(),
                    "list_json": env_dir.resolve()}
    spec = source[SOLVE_SOURCE_KEY]
    assert spec.platform == "linux-64"
    assert spec.channels == ("conda-forge", "defaults")
    assert spec.specs == ("numpy=2.4.6", "python=3.11.16",
                          "conda-forge::scipy=1.13.1")
    # The pip set: the pypi-channel package only, never the conda-managed
    # `@ file:///` lines or pip itself.
    assert source["pip_freeze_content"] == "tifffile==2024.8.30\n"
    assert "explicit_list_content" not in source

    host = source[HOST_CAPTURE_SOURCE_KEY]
    assert set(host) == {
        layout.CAPTURE_HOST_EXPLICIT_LIST_FILENAME,
        layout.CAPTURE_HOST_FROM_HISTORY_FILENAME,
        layout.CAPTURE_HOST_PINNED_SPEC_FILENAME,
        layout.CAPTURE_HOST_PIP_FREEZE_FILENAME,
        layout.CAPTURE_HOST_INFO_FILENAME,
    }
    assert host[layout.CAPTURE_HOST_EXPLICIT_LIST_FILENAME] == win_list
    assert host[layout.CAPTURE_HOST_FROM_HISTORY_FILENAME] == from_history
    assert host[layout.CAPTURE_HOST_PINNED_SPEC_FILENAME] == spec.to_yaml()
    assert host[layout.CAPTURE_HOST_PIP_FREEZE_FILENAME] == host_freeze
    info = json.loads(host[layout.CAPTURE_HOST_INFO_FILENAME])
    assert (info["platform"], info["conda_version"]) == ("win-64", "24.11.3")
    assert datetime.fromisoformat(info["captured_at"]).tzinfo is not None

    # The tag routes the build past the linux-64 explicit-list refusal.
    backend_row("conda").validate_source(source, "imaging")


def test_the_pinned_spec_pins_each_requested_package_to_its_installed_version():
    """The spec keeps the export's channels and order when the packages came
    from them, and any ``channel::`` prefix, and replaces each request's
    constraint with ``=<installed version>`` (no build string)."""
    from wfc.environments.introspect import pinned_spec

    spec = pinned_spec(_fixture("from-history.yml").replace("\n", "\r\n"),
                       _installed())

    assert spec.to_yaml() == (
        "channels:\n"
        "  - conda-forge\n"
        "  - defaults\n"
        "dependencies:\n"
        "  - numpy=2.4.6\n"
        "  - python=3.11.16\n"
        "  - conda-forge::scipy=1.13.1\n"
    )


@workflow(
    purpose="Live capture of a Windows conda env made from conda-forge with "
            "--override-channels, whose history lists only the configured "
            "defaults channel, solves from conda-forge: the spec's channels "
            "are the ones the installed packages came from, and each request "
            "is pinned to conda-forge"
)
def test_a_capture_whose_packages_came_from_conda_forge_solves_them_there(
        tmp_path, monkeypatch):
    from wfc.environments.introspect import SOLVE_SOURCE_KEY
    from wfc.environments.verbs import _stage_live_env_source

    project_dir, _ = _windows_env(tmp_path)
    stub_host_conda_platform(monkeypatch, "win-64")
    fake_conda_list_explicit(monkeypatch, _fixture("win-64-explicit-list.txt"))
    fake_conda_solve_capture(
        monkeypatch,
        from_history=_fixture("conda-forge-only/from-history.yml")
        .replace("\n", "\r\n"),
        list_json=json.loads(_fixture("conda-forge-only/conda-list.json")))
    fake_pip_freeze(monkeypatch, "")

    spec = _stage_live_env_source("conda:imaging", project_dir)[SOLVE_SOURCE_KEY]

    assert spec.channels == ("conda-forge",)
    assert spec.specs == ("conda-forge::python=3.12.16",
                          "conda-forge::pyfiglet=1.0.4")


@pytest.mark.parametrize(("listed", "channels", "specs"), [
    (["defaults"], ("defaults", "conda-forge"),
     ("numpy=2.4.6", "conda-forge::scipy=1.13.1")),
    # A history channel given as a URL is the channel of the packages whose
    # base_url it is.
    (["https://conda.anaconda.org/conda-forge", "defaults"],
     ("https://conda.anaconda.org/conda-forge", "defaults"),
     ("numpy=2.4.6", "https://conda.anaconda.org/conda-forge::scipy=1.13.1")),
    # A history channel that ends a package's base_url is that package's.
    (["pkgs/main"], ("pkgs/main", "conda-forge"),
     ("pkgs/main::numpy=2.4.6", "conda-forge::scipy=1.13.1")),
])
def test_each_requested_package_is_pinned_to_the_channel_it_was_installed_from(
        listed, channels, specs):
    """In an env mixing channels, a request installed from a channel the
    history does not list is pinned to that channel and the channel is
    added after the history's; a request from Anaconda's own channels is
    solved from ``defaults``. A history channel named by URL or by the end
    of a package's ``base_url`` is that package's channel."""
    from wfc.environments.introspect import pinned_spec

    from_history = ("channels:\n" + "".join(f"  - {c}\n" for c in listed)
                    + "dependencies:\n  - numpy\n  - scipy\n")
    installed = [p for p in _installed() if p["name"] in {"numpy", "scipy"}]

    spec = pinned_spec(from_history, installed)

    assert spec.channels == channels
    assert spec.specs == specs


@pytest.mark.parametrize("local_channel", [
    {"channel": "local",
     "base_url": "file:///C:/Users/someone/anaconda3/conda-bld"},
    {"channel": "file:///C:/Users/someone/builds",
     "base_url": "file:///C:/Users/someone/builds"},
    {"channel": "<unknown>"},
])
def test_a_package_from_a_local_build_is_solved_from_the_other_channels(
        local_channel):
    """A package installed from a local build or tarball (``local``,
    ``<unknown>`` or a ``file:`` channel) comes from a channel the linux
    image cannot reach: the channel is left out of the solve, and a request
    from it is pinned with no ``channel::`` prefix."""
    from wfc.environments.introspect import pinned_spec

    from_history = _fixture("conda-forge-only/from-history.yml").replace(
        "  - pyfiglet\n", "  - pyfiglet\n  - mytool\n")
    installed = json.loads(_fixture("conda-forge-only/conda-list.json")) + [
        {**local_channel, "name": name, "version": version,
         "build_string": "0", "platform": "win-64"}
        for name, version in [("libmytool", "0.3"), ("mytool", "1.0")]]

    spec = pinned_spec(from_history, installed)

    assert spec.channels == ("conda-forge",)
    assert spec.specs == ("conda-forge::python=3.12.16",
                          "conda-forge::pyfiglet=1.0.4", "mytool=1.0")


def test_a_requested_package_that_is_not_installed_is_refused_by_name():
    """A request the env no longer holds cannot be pinned; the error names it."""
    from wfc.environments.introspect import pinned_spec

    installed = [p for p in _installed() if p["name"] != "scipy"]

    with pytest.raises(ValueError, match=r"conda-forge::scipy>=1\.10"):
        pinned_spec(_fixture("from-history.yml"), installed)


def test_the_pip_set_is_only_the_pypi_channel_packages_pinned():
    """``conda list --json``'s pypi entries become ``name==version`` lines."""
    from wfc.environments.introspect import pypi_pip_set

    installed = _installed() + [{
        "base_url": "https://pypi.org/", "build_number": 0,
        "build_string": "pypi_0", "channel": "pypi",
        "dist_name": "aicsimageio-4.14.0-pypi_0", "name": "aicsimageio",
        "platform": "pypi", "version": "4.14.0",
    }]

    assert pypi_pip_set(installed) == ("aicsimageio==4.14.0\n"
                                       "tifffile==2024.8.30\n")
    assert pypi_pip_set([p for p in installed if p["channel"] != "pypi"]) == ""


def test_a_conda_call_that_hangs_is_refused_naming_the_command(monkeypatch):
    """Each conda call of a solve capture runs under a timeout; on expiry it
    raises naming the command (register-env reports it as a refusal)."""
    import subprocess

    from tests.fixtures.fakes import fake_subprocess_run
    from wfc.environments.introspect import conda_version

    def hang(cmd, *args, **kwargs):
        assert kwargs.get("timeout"), f"{cmd!r} ran with no timeout and would hang"
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    fake_subprocess_run(monkeypatch, hang, only="conda")

    with pytest.raises(RuntimeError, match=r"`conda --version` did not finish"):
        conda_version()
