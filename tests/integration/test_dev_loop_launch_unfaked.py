"""The dev loop launches a real container from a registered env.

Everywhere else the dev loop's launch is replaced and only the argv it built
is read. Here ``wfc exec`` resolves a registered container env over a real
project and hands its argv to Docker, and the container starts: the command it
runs sees Docker's ``/.dockerenv`` marker and writes into the project tree,
which the container sees at ``/work``.
"""
from __future__ import annotations

import pytest

from tests.conftest import requires_docker
from tests.fixtures.conftest import write_env_record

pytestmark = [pytest.mark.integration, requires_docker]

#: The file the in-container command writes into the mounted project.
MARKER = "dev_loop_started.txt"


def test_the_dev_loop_starts_a_container_from_the_registered_env(
        tmp_project, fixture_container_image):
    """``exec_`` exits 0 and the command ran inside the container."""
    from wfc.environments import dev_loop

    write_env_record(tmp_project, "dev-env", image="local/wfc-test-minimal",
                     digest=fixture_container_image)

    rc = dev_loop.exec_("dev-env", [
        "sh", "-c", f"test -f /.dockerenv && echo started-in-container > {MARKER}",
    ])

    assert rc == 0
    marker = tmp_project / MARKER
    assert marker.exists(), "the command did not run in the mounted project"
    assert marker.read_text(encoding="utf-8").strip() == "started-in-container"
