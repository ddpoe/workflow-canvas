"""wfc doctor's bind row against a real Docker daemon.

On a real project whose env image is on this machine, ``wfc doctor`` starts
the bind check's container in that image and the ``binds`` row is ok.
"""

from __future__ import annotations

import pytest

from tests.conftest import requires_docker
from tests.fixtures.fakes import fake_subprocess_run, real_subprocess_run

pytestmark = [pytest.mark.integration, requires_docker]


def test_doctor_on_a_real_project_shows_the_binds_row_ok(
    register_fixture_methods, fixture_container_image, monkeypatch, capsys,
):
    """The real check, run by doctor in the fixture env's image, sees the project."""
    from wfc.cli import cli_main

    root = register_fixture_methods
    starts: list = []

    def record(cmd, *args, **kwargs):
        if list(cmd[:2]) == ["docker", "run"]:
            starts.append(list(cmd))
        return real_subprocess_run(cmd, *args, **kwargs)

    fake_subprocess_run(monkeypatch, record, only="docker")
    monkeypatch.chdir(root)
    capsys.readouterr()
    cli_main(["doctor"])
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines() if line.startswith("  [")
            and line[9:].split()[:1] == ["binds"]]
    assert len(rows) == 1, out
    assert rows[0].startswith("  [  OK] binds"), out
    assert len(starts) == 1
    assert any(fixture_container_image in arg for arg in starts[0])
