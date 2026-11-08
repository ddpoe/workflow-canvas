"""A real build of the conda recipe: readable as any user, fast, no larger than its env.

Builds :func:`wfc.environments.dockerfiles.conda.generate`'s recipe for a
small env (python + numpy from conda-forge, and a two-package pip set)
against the pinned micromamba base, with ``docker build`` directly, then
checks the image:

* a step's ``--user 1234:1234`` imports the env's packages, and no file
  under the env lacks read access for every user;
* no build step after the install takes over a minute (a separate chmod
  step over the env took eight to nine minutes on this env), and the image's
  layers add up to no more than the base's layers plus the env tree;
* micromamba's fallback package directory, ``/home/mambauser/.mamba/pkgs``,
  is not in the image: the install wrote the package cache mount.

Sizes are the uncompressed layer sizes ``docker history`` reports, summed,
set against ``du -sk`` of the env tree: under the containerd image store
``docker image inspect``'s ``Size`` is the compressed size, and ``du -sb``
(apparent size) undercounts the blocks the layer holds.

The explicit list is made in a throwaway container of the pinned base, the
way the solve recipe makes one. ``integration``-marked (deselected by
default) and skipped without a reachable daemon; the timing test is also
``slow``. The test removes the one image it builds.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid

import pytest

from axiom_annotations import workflow

from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

_PIP_SET = "tqdm==4.66.5\nsix==1.16.0\n"
_SPEC = "python=3.11 numpy=1.26.4"
_RUN_AS = "1234:1234"


def _run(cmd: list[str], timeout: int, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, **kwargs)


def _layers_bytes(ref: str) -> int:
    """The image's layer sizes, uncompressed, summed."""
    out = _run(["docker", "history", "--human=false", "--format", "{{.Size}}", ref],
               timeout=60)
    assert out.returncode == 0, out.stderr
    return sum(int(size) for size in out.stdout.split())


def _step_times(log: str) -> list[tuple[int, str, float]]:
    """The recipe steps of a ``--progress=plain`` build: index, title, seconds."""
    titles: dict[str, tuple[int, str]] = {}
    times = []
    for line in log.splitlines():
        # e.g. ``#8 [stage-0 2/5] COPY explicit-list.txt /opt/``
        title = re.match(r"#(\d+) \[(?:\S+ )?(\d+)/\d+\] (.*)", line)
        if title and title.group(1) not in titles:
            titles[title.group(1)] = (int(title.group(2)), title.group(3))
        done = re.match(r"#(\d+) DONE ([\d.]+)s", line)
        if done and done.group(1) in titles:
            index, text = titles[done.group(1)]
            times.append((index, text, float(done.group(2))))
    return sorted(times)


@pytest.fixture(scope="module")
def conda_image(tmp_path_factory):
    """Build the conda recipe once for the module; remove the image after."""
    from wfc.environments.dockerfiles.bases import MICROMAMBA_BASE
    from wfc.environments.dockerfiles.conda import generate

    capture = _run(
        ["docker", "run", "--rm", MICROMAMBA_BASE, "bash", "-c",
         f"micromamba install -y -n base -c conda-forge {_SPEC} >/dev/null"
         " && micromamba env export -n base --explicit --md5"],
        timeout=900,
    )
    assert capture.returncode == 0, capture.stderr
    assert "@EXPLICIT" in capture.stdout, capture.stdout

    context = tmp_path_factory.mktemp("conda-recipe")
    for name, text in (("Dockerfile", generate("permtest", _PIP_SET)),
                       ("explicit-list.txt", capture.stdout),
                       ("pip-freeze.txt", _PIP_SET)):
        (context / name).write_text(text, encoding="utf-8", newline="\n")

    tag = f"local/wfc-test-recipe-perms:{uuid.uuid4().hex[:12]}"
    build = _run(
        ["docker", "build", "--no-cache", "--provenance=false", "--progress=plain",
         "-t", tag, str(context)],
        timeout=1800, env=dict(os.environ, DOCKER_BUILDKIT="1"),
    )
    log = build.stdout + build.stderr
    try:
        assert build.returncode == 0, log[-4000:]
        yield {"tag": tag, "base": MICROMAMBA_BASE, "log": log}
    finally:
        _run(["docker", "image", "rm", "-f", tag], timeout=120)


@workflow(
    purpose="An image built from the conda recipe runs as a step's non-root "
            "--user: the env's conda and pip packages import, and no file under "
            "the env lacks read access for every user (no directory lacks "
            "search access)",
)
def test_the_conda_env_reads_and_runs_as_another_user(conda_image):
    tag = conda_image["tag"]

    run = _run(["docker", "run", "--rm", "--user", _RUN_AS, tag,
                "/opt/conda/bin/python", "-c",
                "import numpy, six, tqdm; print('imported', numpy.__version__)"],
               timeout=300)
    assert run.returncode == 0, run.stderr
    assert "imported 1.26.4" in run.stdout, run.stdout

    unreadable = _run(
        ["docker", "run", "--rm", "--user", "0:0", "--entrypoint", "sh", tag, "-c",
         "find /opt/conda ! -type l ! -perm -a+r; "
         "find /opt/conda -type d ! -perm -a+x"],
        timeout=300,
    )
    assert unreadable.returncode == 0, unreadable.stderr
    assert unreadable.stdout.strip() == "", unreadable.stdout[:2000]


@pytest.mark.slow
@workflow(
    purpose="Building the conda recipe takes no step over a minute after the "
            "install, and the image is no larger than the base plus the env "
            "tree (summed uncompressed layers against the base's layers plus "
            "du -sk of the env)",
)
def test_no_step_after_the_install_is_slow_and_the_image_holds_only_the_env(
        conda_image):
    steps = _step_times(conda_image["log"])
    install = [index for index, text, _ in steps if "micromamba install" in text]
    assert len(install) == 1, steps
    after = [(text, seconds) for index, text, seconds in steps if index > install[0]]
    assert after, steps
    slow = [(text[:80], seconds) for text, seconds in after if seconds > 60]
    assert slow == [], slow

    du = _run(["docker", "run", "--rm", "--user", "0:0", "--entrypoint", "du",
               conda_image["tag"], "-sk", "/opt/conda"], timeout=300)
    assert du.returncode == 0, du.stderr
    env_bytes = int(du.stdout.split()[0]) * 1024
    image_bytes = _layers_bytes(conda_image["tag"])
    base_bytes = _layers_bytes(conda_image["base"])
    extra = image_bytes - base_bytes - env_bytes
    # The pip layer's files are counted in both its layer and the env tree's
    # blocks, about 1 % here; a second copy of the env, or of the package
    # tarballs, is a third of the env or more.
    assert extra <= 0.03 * env_bytes, (
        f"image {image_bytes} B = base {base_bytes} B + env {env_bytes} B "
        f"+ {extra} B")


@workflow(
    purpose="The conda install writes its package cache mount, so "
            "micromamba's fallback package directory "
            "/home/mambauser/.mamba/pkgs is not in the image",
)
def test_the_package_tarballs_stay_out_of_the_image(conda_image):
    probe = _run(["docker", "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
                  conda_image["tag"], "-c",
                  "if [ -e /home/mambauser/.mamba/pkgs ]; then "
                  "du -sh /home/mambauser/.mamba/pkgs; else echo absent; fi"],
                 timeout=300)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "absent", probe.stdout
