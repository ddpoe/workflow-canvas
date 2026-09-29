"""The four run-readiness probes answered by the machine itself.

Everywhere else the probes are stubbed. Here git, DVC, the Docker daemon and
the sample registry each answer for a project a user could have: built and
committed by the project route, with its DVC archive configured and a sample
registered.
"""
from __future__ import annotations

import pytest

from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]


def test_the_four_probes_pass_unfaked_over_a_real_project(tmp_project,
                                                          monkeypatch):
    """check_git, check_dvc, check_docker and check_samples each report ok."""
    from wfc.execution.readiness import (check_docker, check_dvc, check_git,
                                         check_samples)

    from tests.fixtures.routes import completed_run

    completed_run(tmp_project, monkeypatch=monkeypatch, method="probed")

    results = {
        "git": check_git(tmp_project),
        "dvc": check_dvc(tmp_project),
        "docker": check_docker(),
        "samples": check_samples(tmp_project),
    }

    assert {name: r.name for name, r in results.items()} == {
        "git": "git", "dvc": "dvc", "docker": "docker", "samples": "samples"}
    not_ok = {name: (r.status, r.message) for name, r in results.items()
              if r.status != "ok"}
    assert not not_ok, not_ok
