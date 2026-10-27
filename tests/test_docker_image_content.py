"""``docker.image_content`` reads an image's layers and run-time config from one inspect.

Canned ``docker image inspect`` output goes through the subprocess fake; no
Docker daemon is reached.
"""

from __future__ import annotations

import json

import pytest

from tests.fixtures.fakes import canned_process, fake_subprocess_run
from wfc.environments import docker as docker_runner

_REF = "local/tool@sha256:" + "d" * 64
_LAYERS = ["sha256:" + "1" * 64, "sha256:" + "2" * 64]


def _inspected(config: dict | None) -> str:
    """Return one image's inspect JSON, with names, digests and labels set.

    Args:
        config: The image's ``Config`` object (``None`` for a JSON null).

    Returns:
        The JSON text ``docker image inspect --format '{{json .}}'`` prints.
    """
    return json.dumps({
        "Id": "sha256:" + "f" * 64,
        "RepoTags": ["local/tool:1.0"],
        "RepoDigests": ["local/tool@sha256:" + "d" * 64],
        "RootFS": {"Type": "layers", "Layers": _LAYERS},
        "Config": config,
    })


def test_image_content_reads_layers_and_run_config_from_one_inspect(monkeypatch):
    """The layers and Env/User/WorkingDir come back as the daemon reports
    them, from one ``docker image inspect <ref> --format '{{json .}}'``."""
    calls = []
    canned = canned_process(stdout=_inspected({
        "Env": ["PATH=/usr/bin", "LANG=C.UTF-8"], "User": "app",
        "WorkingDir": "/work", "Labels": {"org.example": "x"},
    }))

    def handler(cmd, *args, **kwargs):
        calls.append(cmd)
        return canned(cmd, *args, **kwargs)

    fake_subprocess_run(monkeypatch, handler, only="docker")

    assert docker_runner.image_content(_REF) == {
        "layers": _LAYERS,
        "env": ["PATH=/usr/bin", "LANG=C.UTF-8"],
        "user": "app",
        "workdir": "/work",
    }
    assert calls == [["docker", "image", "inspect", _REF, "--format", "{{json .}}"]]


@pytest.mark.parametrize("config", [
    pytest.param({"Env": None, "User": "", "WorkingDir": ""}, id="null-env"),
    pytest.param(None, id="null-config"),
])
def test_image_content_returns_none_for_a_null_env(monkeypatch, config):
    """A null ``Config.Env`` (or a null ``Config``) gives ``env: None``."""
    fake_subprocess_run(monkeypatch, canned_process(stdout=_inspected(config)),
                        only="docker")

    content = docker_runner.image_content(_REF)

    assert content["env"] is None
    assert content["layers"] == _LAYERS


@pytest.mark.parametrize(("stderr", "missing"), [
    ("Error: No such image: " + _REF, True),
    ("Error response from daemon: No such object: " + _REF, True),
    ("permission denied while trying to connect to the Docker daemon socket",
     False),
])
def test_image_content_tells_a_missing_image_from_another_failure(
        monkeypatch, stderr, missing):
    """A non-zero exit naming a missing image is ``ImageNotFoundError``; any
    other non-zero exit is a plain ``RuntimeError``. Both carry stderr."""
    fake_subprocess_run(monkeypatch,
                        canned_process(returncode=1, stderr=stderr),
                        only="docker")

    with pytest.raises(RuntimeError) as exc:
        docker_runner.image_content(_REF)

    assert isinstance(exc.value, docker_runner.ImageNotFoundError) is missing
    assert stderr in str(exc.value)


@pytest.mark.parametrize("stdout", [
    pytest.param("not json at all", id="not-json"),
    pytest.param("[]", id="json-not-an-object"),
])
def test_image_content_refuses_output_that_is_not_an_image(monkeypatch, stdout):
    """Output that is not one image's JSON object is a ``RuntimeError``
    saying no image JSON was printed, not a missing image."""
    fake_subprocess_run(monkeypatch, canned_process(stdout=stdout), only="docker")

    with pytest.raises(RuntimeError, match="printed no image JSON") as exc:
        docker_runner.image_content(_REF)

    assert not isinstance(exc.value, docker_runner.ImageNotFoundError)
