"""Tier 3 test: an unlabelled image wfc pushes to GHCR arrives labelled, and gh finds its package.

The test makes a project whose git remote is ``https://github.com/
<GITHUB_REPOSITORY>`` and whose registry base is ``ghcr.io/<owner>/<repo>``,
builds a one-file image with wfc's Docker wrapper and no labels, and
registers it as a byo ``local/`` image with ``wfc register-env``, which
pushes it before it commits. The image lacks the repository's source label
(``org.opencontainers.image.source``), so the push sends a labelled copy to
the throwaway package ``<repo>/wfc-ci-probe``. The test also tags that
version ``WFC_GHCR_TAG``, removes the local copies, pulls the recorded
digest back to see the label arrived, and reads the package through wfc's
own ``gh api`` read.

In CI's ``ghcr`` job (pushes to main, and manual runs; ``GITHUB_ACTIONS`` is
set) Docker is logged in to ``ghcr.io`` with the workflow's
``GITHUB_TOKEN``, and the job deletes the version tagged ``WFC_GHCR_TAG``
afterwards (the whole probe package when that version is its last). That
token reads the package, but GitHub does not show it the package's
repository (the packages REST API documents classic tokens only), so there
the test asserts only that GitHub found the package. Run by hand with a
user's ``gh`` login (``GITHUB_REPOSITORY`` naming a throwaway repository, and
any ``WFC_GHCR_TAG``), it asserts the package is connected to the
repository; nothing deletes that version.

This is the backing witness of ``stub_gh`` (the ``gh api`` package read and
its ``--jq`` expression run against GitHub itself) and of
``stub_docker_image_labels`` (the labels read and the labelled copy, on a
real daemon and a real registry).

Marked ``ghcr`` (deselected by default, like ``integration``). It skips
without ``WFC_GHCR_TAG`` and ``GITHUB_REPOSITORY``.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.conftest import requires_docker
from wfc.environments.registry import labelled_copy_ref

pytestmark = [
    pytest.mark.ghcr,
    requires_docker,
    pytest.mark.skipif(not (os.environ.get("WFC_GHCR_TAG")
                            and os.environ.get("GITHUB_REPOSITORY")),
                       reason="pushes to GHCR; needs WFC_GHCR_TAG and "
                              "GITHUB_REPOSITORY (CI's ghcr job sets both)"),
]

#: The env name of the throwaway package (``<repo>/wfc-ci-probe``).
PROBE_ENV = "wfc-ci-probe"

#: Seconds to wait for GitHub to list a freshly pushed package.
_FIND_WAIT_S = 120


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@workflow(purpose="An image built with no labels in a project whose git remote "
                  "is the GitHub repository and whose base is GHCR is pushed "
                  "by register-env as a labelled copy with the same env "
                  "fingerprint; pulled back by the recorded digest it carries "
                  "the repository's source label, and wfc's gh api read finds "
                  "the package on GitHub (with a user's gh login: connected "
                  "to the repository)")
def test_a_labelled_ghcr_push_keeps_its_label_and_gh_finds_the_package(
        tmp_project: Path) -> None:
    """An unlabelled image wfc pushes to GHCR arrives labelled, and gh finds its package."""
    from wfc.cli import cli_main
    from wfc.environments import docker as docker_runner
    from wfc.environments.captures import pushed_record
    from wfc.environments.fingerprint import content_fingerprint
    from wfc.environments.gh import (
        PACKAGE_CONNECTED,
        PACKAGE_NOT_FOUND,
        PACKAGE_UNKNOWN,
        package_connection,
        package_name,
    )
    from wfc.environments.manifest import env_record_for_spec
    from wfc.environments.registry import (
        SOURCE_LABEL,
        push_target,
        registry_ref,
        source_labels,
    )
    from wfc.persistence.config import set_registry_url

    repository = os.environ["GITHUB_REPOSITORY"]
    run_tag = os.environ["WFC_GHCR_TAG"]
    base = f"ghcr.io/{repository.lower()}"
    local = f"local/{PROBE_ENV}:{run_tag}"
    refs = [local]
    try:
        口 = Step(step_num=1, name="A project on the repository, with a GHCR base",
                 purpose="git remote origin is https://github.com/<repo>.git; "
                         "[registry] url is ghcr.io/<owner>/<repo>, committed")
        _git(tmp_project, "remote", "add", "origin",
             f"https://github.com/{repository}.git")
        labels = source_labels(tmp_project)
        assert labels.get(SOURCE_LABEL, "").lower() == (
            f"https://github.com/{repository}".lower()), labels
        config = set_registry_url(tmp_project, base)
        _git(tmp_project, "add", "--", str(config))
        _git(tmp_project, "commit", "-q", "-m", "registry: GHCR")

        口 = Step(step_num=2, name="Build an image with no labels",
                 purpose="A one-file image built by wfc's docker.build with no "
                         "labels, as an image built before the remote was added")
        context = tmp_project.parent / "image"
        context.mkdir()
        (context / "marker.txt").write_text(run_tag, encoding="utf-8")
        (context / "Dockerfile").write_text(
            "FROM scratch\nCOPY marker.txt /marker.txt\n", encoding="utf-8")
        docker_runner.build(context, local)
        assert SOURCE_LABEL not in docker_runner.image_labels(local)

        口 = Step(step_num=3, name="register-env pushes it",
                 purpose="A byo local/ registration with the base set pushes "
                         "before it commits; pushed.json records the digest")
        assert cli_main(["register-env", PROBE_ENV, "--backend", "byo",
                         "--image", f"docker://{local}"]) == 0
        record, _ = env_record_for_spec(PROBE_ENV, tmp_project)
        assert record is not None and record.env_fingerprint
        fingerprint = record.env_fingerprint
        pushed = pushed_record(PROBE_ENV, fingerprint, tmp_project)
        assert pushed is not None
        target = push_target(base, PROBE_ENV, fingerprint)
        ref = registry_ref(base, PROBE_ENV, pushed.digest)
        refs += [target, ref]

        口 = Step(step_num=4, name="Tag the pushed version for this run",
                 purpose="The same manifest pushed again under WFC_GHCR_TAG, "
                         "the tag CI's cleanup step deletes the version by")
        run_ref = f"{base}/{PROBE_ENV}:{run_tag}"
        docker_runner.tag(target, run_ref)
        refs.append(run_ref)
        assert docker_runner.push(run_ref) in (None, pushed.digest)

        口 = Step(step_num=5, name="The pushed image carries the source label",
                 purpose="With the local copies removed, the image pulled back "
                         "by the recorded digest holds the label and renders "
                         "the recorded env fingerprint")
        image_id = docker_runner.image_inspect(local)
        for name in (local, target, run_ref,
                     labelled_copy_ref(PROBE_ENV, fingerprint), image_id):
            subprocess.run(["docker", "image", "rm", "-f", name],
                           capture_output=True, timeout=120)
        subprocess.run(["docker", "pull", ref], check=True, capture_output=True,
                       timeout=600)
        assert docker_runner.image_labels(ref).get(SOURCE_LABEL) == labels[SOURCE_LABEL]
        assert content_fingerprint(docker_runner.image_content(ref)) == fingerprint

        口 = Step(step_num=6, name="wfc's gh read finds the package on GitHub",
                 purpose="wfc's gh api read of the package answers with it; "
                         "with a user's gh login it is connected to the "
                         "repository (CI's token is not shown the repository)")
        named = package_name(base, PROBE_ENV)
        assert named is not None
        owner, package = named
        on_ci = os.environ.get("GITHUB_ACTIONS") == "true"

        def settled(conn) -> bool:
            if on_ci:
                return conn.state not in (PACKAGE_NOT_FOUND, PACKAGE_UNKNOWN)
            return conn.state == PACKAGE_CONNECTED

        deadline = time.monotonic() + _FIND_WAIT_S
        while True:
            connection = package_connection(owner, package, repository)
            if settled(connection) or time.monotonic() > deadline:
                break
            time.sleep(5)
        assert settled(connection), connection
    finally:
        for name in refs:
            subprocess.run(["docker", "image", "rm", "-f", name],
                           capture_output=True, timeout=120)
