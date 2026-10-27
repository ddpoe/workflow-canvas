"""Subsystem tests: the build half of a conda env solved for linux-64.

A pinned version with no linux-64 build cannot be solved; micromamba's
solver report says so in the failed build's output, and registration turns
it into a refusal naming the package and suggesting pixi. The report under
``tests/fixtures/conda_solve/micromamba-no-linux-64-build.txt`` is the real
``docker build`` failure of the solve recipe for a spec pinning
``vc14_runtime=14.44.35208`` (a Windows-only package), recorded on Docker
Desktop with the pinned micromamba base.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiom_annotations import workflow

from tests.fixtures.fakes import (stub_docker_build, stub_docker_image_content,
                                  stub_docker_image_inspect)

FIXTURES = Path(__file__).parent / "fixtures" / "conda_solve"


def _recorded_failure() -> str:
    """The recorded micromamba solve failure, as docker.build raises it."""
    return (FIXTURES / "micromamba-no-linux-64-build.txt").read_text(encoding="utf-8")


def _solve_source(*specs: str) -> dict:
    """A solve payload, as live capture on Windows hands it to register."""
    from wfc import layout
    from wfc.environments.introspect import (HOST_CAPTURE_SOURCE_KEY,
                                             SOLVE_SOURCE_KEY, SolveSpec)

    return {
        SOLVE_SOURCE_KEY: SolveSpec(channels=("conda-forge", "defaults"),
                                    specs=specs),
        "pip_freeze_content": "",
        HOST_CAPTURE_SOURCE_KEY: {layout.CAPTURE_HOST_INFO_FILENAME: "{}\n"},
    }


@workflow(
    purpose="A conda env solved for linux-64 whose pinned version has no "
            "linux-64 build is refused from micromamba's recorded solver "
            "report: the error names the package and version and suggests "
            "pixi, and no record, captures folder or build context is left"
)
def test_a_pin_with_no_linux_64_build_is_refused_naming_it_and_pixi(
        tmp_path, monkeypatch):
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()
    stub_docker_build(monkeypatch, RuntimeError(_recorded_failure()))
    stub_docker_image_inspect(monkeypatch, "sha256:" + "e" * 64)
    stub_docker_image_content(monkeypatch)

    with pytest.raises(ValueError) as exc:
        envs_mod.register(
            name="win-env", backend="conda",
            source=_solve_source("python=3.12.11", "vc14_runtime=14.44.35208"),
            project_dir=tmp_path)

    message = str(exc.value)
    assert "vc14_runtime=14.44.35208" in message
    assert "python=3.12.11" not in message
    assert "linux-64" in message
    assert "pixi" in message
    assert not (tmp_path / ".wfc" / "envs.json").exists()
    assert not (tmp_path / ".wfc" / "captures").exists()
    assert not (tmp_path / ".wfc" / "build" / "win-env").exists()


def test_another_build_failure_is_not_read_as_a_missing_build(tmp_path, monkeypatch):
    """A failure with no solver report passes through as Docker's error."""
    from wfc import environments as envs_mod

    (tmp_path / ".wfc").mkdir()
    stub_docker_build(monkeypatch, RuntimeError(
        "docker build failed (exit 1):\nERROR: failed to solve: "
        "failed to fetch anonymous token: unexpected status 503"))

    with pytest.raises(RuntimeError, match="unexpected status 503"):
        envs_mod.register(name="win-env", backend="conda",
                          source=_solve_source("python=3.12.11"),
                          project_dir=tmp_path)


def test_unsolvable_requests_reads_each_missing_pin_once():
    """Every ``└─``/``├─`` ``does not exist`` line names one request; the
    report BuildKit repeats in its summary is read once."""
    from wfc.environments.dockerfiles.conda import unsolvable_requests

    assert unsolvable_requests(_recorded_failure()) == ["vc14_runtime=14.44.35208"]
    two = ("    The following packages are incompatible\n"
           "    ├─ pywin32 =306 * does not exist (perhaps a typo).\n"
           "    └─ conda-forge::wmi =1.5.1 * does not exist (perhaps a typo).\n")
    assert unsolvable_requests(two) == ["pywin32=306", "wmi=1.5.1"]
    assert unsolvable_requests("ERROR: failed to solve: 503") == []


def test_the_solve_recipe_writes_the_list_before_any_pip_layer():
    """The solve recipe installs the staged spec, then writes the image's
    explicit list as root, then the pip set with --no-deps."""
    from wfc.environments.dockerfiles.conda import (IMAGE_EXPLICIT_LIST_PATH,
                                                    SOLVE_SPEC_FILENAME,
                                                    generate_solve)

    text = generate_solve("e", "tomli==2.0.1\n")
    install = text.index(f"micromamba install -y -n base -f /opt/{SOLVE_SPEC_FILENAME}")
    export = text.index(f"micromamba env export -n base --explicit > {IMAGE_EXPLICIT_LIST_PATH}")
    pip = text.index("pip install --no-deps -r /opt/pip-freeze.txt")
    assert install < export < pip
    assert text.rindex("USER root", 0, export) > install
