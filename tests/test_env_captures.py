"""Unit tests: the verified package list refuses a capture that no longer matches.

:func:`wfc.environments.verified_package_list` reads ``packages.lock`` from
the captures folder of an env's current image and checks its md5 against
the record's ``source_fingerprint``. A changed byte or a missing folder is
refused naming :func:`wfc.environments.capture_remedy`'s plain remedy.
"""

from __future__ import annotations

import shutil

import pytest

from tests.fixtures.fakes import (
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
)

ENV = "tools"
FIX = "`wfc register-env <new-name> ...`"


@pytest.fixture
def conda_env(tmp_project, monkeypatch):
    """A conda env registered from an explicit list (Docker faked)."""
    from wfc import environments as envs_mod

    stub_docker_build(monkeypatch, None)
    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)
    record = envs_mod.register(
        name=ENV, backend="conda",
        source={"explicit_list_content": "@EXPLICIT\nhttps://x/python-3.11.0-h.conda#0a\n",
                "pip_freeze_content": "numpy==1.24.0\n"},
        project_dir=tmp_project,
    )
    folder = tmp_project / ".wfc" / "captures" / ENV / record.env_fingerprint
    return tmp_project, folder


def test_verified_package_list_refuses_a_one_byte_change(conda_env):
    from wfc.environments import CaptureMismatchError, verified_package_list

    project, folder = conda_env
    lock = folder / "packages.lock"
    data = bytearray(lock.read_bytes())
    data[-2] = ord("1") if data[-2] != ord("1") else ord("2")
    lock.write_bytes(bytes(data))

    with pytest.raises(CaptureMismatchError) as exc:
        verified_package_list(ENV, project)
    assert FIX in str(exc.value)
    assert (f"env {ENV!r}'s capture files under .wfc/captures/{ENV}/"
            f"{folder.name}/ were changed outside wfc.") in str(exc.value)


def test_verified_package_list_refuses_a_missing_folder(conda_env):
    from wfc.environments import CaptureMissingError, verified_package_list

    project, folder = conda_env
    shutil.rmtree(folder)

    with pytest.raises(CaptureMissingError) as exc:
        verified_package_list(ENV, project)
    assert FIX in str(exc.value)


def test_capture_remedy_sends_the_demo_env_to_wfc_demo_force():
    """The reserved demo env is re-created by ``wfc demo --force`` (which
    removes its captures first), not registered under a new name."""
    from wfc.demo.scaffold import DEMO_ENV
    from wfc.environments import capture_remedy

    for lock_differs, lead in ((False, "were changed outside wfc."),
                               (True, "wfc doesn't rewrite a capture folder.")):
        text = capture_remedy(DEMO_ENV, "0" * 32, lock_differs=lock_differs)

        assert lead in text
        assert text.endswith("`wfc demo --force`; it removes the demo, "
                             "captures included, first.")
        assert "<new-name>" not in text
        assert "git" not in text
