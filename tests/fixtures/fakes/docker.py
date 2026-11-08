"""Docker boundary: the registry calls of register-env and the dev loop's launch.

``wfc.environments.docker`` has nine entry points a registration or a push
reaches -- ``image_inspect``, ``image_content``, ``image_labels``,
``repo_digest``, ``pull``, ``build``, ``tag``, ``push``, ``read_manifest``
-- and the tests replace them with a digest, a content dict, a recorder, a
raiser or a refuser. One entry per entry point, each taking the replacement
the site wrote; ``refuse_docker`` arms all nine at once for a test that
proves docker is never reached. ``build_labelled`` is not an entry point of
its own: it calls ``tag`` and ``build``, and the tests see those.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

from ._entry import fake


def _as_callable(replacement):
    """Turn a fixed value, an exception or a callable into a callable."""
    if isinstance(replacement, BaseException):
        def raiser(*args, **kwargs):
            raise replacement
        return raiser
    if callable(replacement):
        return replacement
    return lambda *args, **kwargs: replacement


@fake(
    boundary="wfc.environments.docker.image_inspect -- docker image inspect, "
             "the digest probe of a byo registration and of a fresh build, "
             "and ensure_runnable's presence probe of a local/ image, where "
             "a missing image is an ImageNotFoundError",
    preserves="the caller's own sequencing (a registration's probe, pull on "
              "a miss, probe again, compare with a supplied digest; "
              "ensure_runnable's probe, then pull and check or refuse)",
    not_proven="that the daemon holds the image or reports that digest",
    backed_by="pm_mvp::tests.integration.test_pixi_dockerfile_builds::"
              "test_generated_pixi_dockerfile_builds_and_records_live_interpreter",
)
def stub_docker_image_inspect(monkeypatch, replacement) -> None:
    """Replace ``image_inspect``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: A digest string (``"sha256:..."``) returned for every
            ref; an exception raised for every ref; or a callable
            ``(ref) -> str`` that decides per call (a miss then a hit).
    """
    from wfc.environments import docker as docker_runner

    monkeypatch.setattr(docker_runner, "image_inspect",
                        _as_callable(replacement))


@fake(
    boundary="wfc.environments.docker.image_content -- docker image inspect "
             "of an image's layers, Env, User and WorkingDir, the content a "
             "registration's record step renders the env_fingerprint from, "
             "and the run-time rule checks a pulled image's content against",
    preserves="the record step's own sequencing (inspect the ref Docker "
              "starts the env from, render the blob, store it, record its "
              "md5); the ref asked for, seen by a callable replacement",
    not_proven="that the daemon reports that content for the image, or that "
               "a second name for the same image reports the same content",
    backed_by="pm_mvp::tests.integration.test_env_content_fingerprint::"
              "test_content_fingerprint_follows_image_content_not_its_name",
)
def stub_docker_image_content(monkeypatch, replacement=None) -> None:
    """Replace ``image_content``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``None`` for :data:`FAKE_IMAGE_CONTENT`; a content dict
            (``layers``, ``env``, ``user``, ``workdir``) returned for every
            ref; an exception raised for every ref; or a callable
            ``(ref) -> dict`` that decides per call.
    """
    from wfc.environments import docker as docker_runner

    if replacement is None:
        replacement = FAKE_IMAGE_CONTENT
    monkeypatch.setattr(docker_runner, "image_content",
                        _as_callable(replacement))


@fake(
    boundary="wfc.environments.docker.image_labels -- docker image inspect "
             "of an image's Config.Labels, which push_image reads to tell "
             "whether the image already carries the repository's source label",
    preserves="the push's own sequencing (read the labels of the image's "
              "daemon ref, copy it with the label when it lacks it, check "
              "the copy's content); the ref asked for, seen by a callable "
              "replacement",
    not_proven="that the daemon reports those labels for the image, or that "
               "a labelled build carries the label it was given",
    backed_by="pm_mvp::tests.integration.test_ghcr_package_link::"
              "test_a_labelled_ghcr_push_keeps_its_label_and_gh_finds_the_package",
)
def stub_docker_image_labels(monkeypatch, replacement=None) -> None:
    """Replace ``image_labels``. A true external edge: the Docker daemon.

    Considered: ``stub_docker_image_content``, which reports the content an
    env fingerprint is rendered from and carries no labels; no existing
    entry stands at this boundary.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``None`` for no labels on any image; a labels dict
            returned for every ref; an exception raised for every ref; or a
            callable ``(ref) -> dict`` that decides per call.
    """
    from wfc.environments import docker as docker_runner

    if replacement is None:
        replacement = {}
    monkeypatch.setattr(docker_runner, "image_labels",
                        _as_callable(replacement))


#: The content :func:`stub_docker_image_content` reports by default: one
#: layer and the config a typical image carries.
FAKE_IMAGE_CONTENT = {
    "layers": ["sha256:" + "1" * 64],
    "env": ["PATH=/usr/local/bin:/usr/bin:/bin"],
    "user": "",
    "workdir": "/",
}


@fake(
    boundary="wfc.environments.docker.repo_digest -- docker image inspect "
             "of RepoDigests, the registry digest a byo registration records "
             "for an image outside local/",
    preserves="the registration's own sequencing (probe, pull on a miss, "
              "read the registry digest, compare with a supplied digest)",
    not_proven="that the daemon lists that RepoDigest for the repository, or "
               "that Docker Hub names match in their familiar form",
    backed_by="pm_mvp::tests.integration.test_multilang_methods::"
              "test_python_to_r_pipeline_hands_off_across_languages",
)
def stub_docker_repo_digest(monkeypatch, replacement) -> None:
    """Replace ``repo_digest``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: A digest string (``"sha256:..."``) returned for every
            ref; an exception raised for every ref; or a callable
            ``(ref, repository) -> str`` that decides per call.
    """
    from wfc.environments import docker as docker_runner

    monkeypatch.setattr(docker_runner, "repo_digest",
                        _as_callable(replacement))


@fake(
    boundary="wfc.environments.docker.pull -- docker pull of a byo image",
    preserves="the ref asked for, recorded by a recording replacement",
    not_proven="that the image is pulled or that the registry answers",
    backed_by="pm_mvp::tests.integration.test_pixi_dockerfile_builds::"
              "test_pinned_base_digests_exist_in_registries",
)
def stub_docker_pull(monkeypatch, replacement=None) -> None:
    """Replace ``pull``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``None`` for a no-op; an exception to raise; or a
            callable ``(ref)`` (a recorder).
    """
    from wfc.environments import docker as docker_runner

    monkeypatch.setattr(docker_runner, "pull", _as_callable(replacement))


@fake(
    boundary="wfc.environments.docker.build -- docker build of a generated "
             "Dockerfile",
    preserves="the Dockerfile directory and tag asked for, recorded by a "
              "recording replacement; the staging that produced them",
    not_proven="that the Dockerfile builds",
    backed_by="pm_mvp::tests.integration.test_pixi_dockerfile_builds::"
              "test_generated_pixi_dockerfile_builds_and_records_live_interpreter",
)
def stub_docker_build(monkeypatch, replacement=None) -> None:
    """Replace ``build``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``None`` for a no-op; an exception to raise; or a
            callable ``(dockerfile_dir, tag)`` (a recorder).
    """
    from wfc.environments import docker as docker_runner

    monkeypatch.setattr(docker_runner, "build", _as_callable(replacement))


@fake(
    boundary="wfc.environments.docker.tag -- docker tag, the registry name "
             "a local image is given before it is pushed",
    preserves="the source and target asked for, recorded by a recording "
              "replacement; the caller's sequencing (tag, then push)",
    not_proven="that the daemon holds the source image or accepts the name",
    backed_by="pm_mvp::tests.integration.test_env_image_registry_round_trip::"
              "test_a_pushed_env_image_is_pulled_by_digest_and_runs",
)
def stub_docker_tag(monkeypatch, replacement=None) -> None:
    """Replace ``tag``. A true external edge: the Docker daemon.

    Considered: ``stub_docker_build``'s recorder pattern; no existing entry
    stands at this boundary.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``None`` for a no-op; an exception to raise; or a
            callable ``(source, target)`` (a recorder).
    """
    from wfc.environments import docker as docker_runner

    monkeypatch.setattr(docker_runner, "tag", _as_callable(replacement))


@fake(
    boundary="wfc.environments.docker.push -- docker push of an env image "
             "to the project's registry, returning the digest the registry "
             "reported",
    preserves="the ref asked for, seen by a callable replacement; the "
              "caller's sequencing (push before the record and the commit; a "
              "push failure as RuntimeError carrying docker's text)",
    not_proven="that the registry accepts the push, that docker is logged "
               "in, or that the digest returned is the one the registry serves",
    backed_by="pm_mvp::tests.integration.test_env_image_registry_round_trip::"
              "test_a_pushed_env_image_is_pulled_by_digest_and_runs",
)
def stub_docker_push(monkeypatch, replacement=None) -> None:
    """Replace ``push``. A true external edge: Docker and the registry.

    Considered: ``stub_docker_pull`` (the opposite direction, returns
    nothing); no existing entry stands at this boundary.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``None`` for :data:`FAKE_PUSHED_DIGEST` returned for
            every ref; a digest string; an exception to raise; or a callable
            ``(ref) -> str | None`` (a recorder).
    """
    from wfc.environments import docker as docker_runner

    if replacement is None:
        replacement = FAKE_PUSHED_DIGEST
    monkeypatch.setattr(docker_runner, "push", _as_callable(replacement))


#: The digest :func:`stub_docker_push` reports by default.
FAKE_PUSHED_DIGEST = "sha256:" + "d" * 64


@fake(
    boundary="wfc.environments.docker.read_manifest -- docker manifest "
             "inspect, the one registry read that tells found, missing, "
             "unauthorized and unreachable apart through Docker's own "
             "credentials",
    preserves="the ref asked for, seen by a callable replacement; the "
              "RegistryRead shape the caller classifies on",
    not_proven="that a registry gives that answer, or that docker's wording "
               "for it is the one classify_manifest_answer reads",
    backed_by="pm_mvp::tests.integration.test_env_image_registry_round_trip::"
              "test_a_pushed_env_image_is_pulled_by_digest_and_runs",
)
def stub_docker_read_manifest(monkeypatch, replacement) -> None:
    """Replace ``read_manifest``. A true external edge: the registry.

    Considered: ``stub_readiness_probes`` (a whole readiness row, not a
    registry read); no existing entry stands at this boundary.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: A state string (``"found"``, ``"missing"``,
            ``"unauthorized"``, ``"unreachable"``, ``"error"``) answered for
            every ref with a canned detail; a ``RegistryRead``; an exception;
            or a callable ``(ref) -> RegistryRead``.
    """
    from wfc.environments import docker as docker_runner

    if isinstance(replacement, str):
        state = replacement

        def answer(ref):
            return docker_runner.RegistryRead(ref, state, _CANNED_DETAIL[state])

        replacement = answer
    monkeypatch.setattr(docker_runner, "read_manifest", _as_callable(replacement))


#: Docker's wording for each registry answer, as the fake reports it.
_CANNED_DETAIL = {
    "found": '{"schemaVersion": 2}',
    "missing": "manifest unknown: manifest unknown",
    "unauthorized": "unauthorized: authentication required",
    "unreachable": "dial tcp 127.0.0.1:1: connect: connection refused",
    "error": "docker failed",
}


@fake(
    boundary="wfc.environments.docker.build / pull / image_inspect / "
             "image_content / image_labels / repo_digest / tag / push / "
             "read_manifest together",
    preserves="everything before the first docker call -- name validation, "
              "lock validation, the manifest check",
    not_proven="anything past the refusal; the entry proves docker was NOT "
               "reached",
    backed_by="pm_mvp::tests.integration.test_pixi_dockerfile_builds::"
              "test_generated_pixi_dockerfile_builds_and_records_live_interpreter",
)
def refuse_docker(monkeypatch, reason: str = "docker must not be reached"
                  ) -> None:
    """Arm every docker entry point to fail the test.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        reason: The assertion message.
    """
    from wfc.environments import docker as docker_runner

    def never(*args, **kwargs):
        raise AssertionError(reason)

    for entry_point in ("build", "pull", "image_inspect", "image_content",
                        "image_labels", "repo_digest", "tag", "push",
                        "read_manifest"):
        monkeypatch.setattr(docker_runner, entry_point, never)


@fake(
    boundary="wfc.environments.bind_check._run_probe -- the docker run of the "
             "launch's bind check: one short container from the launch's "
             "image with a step's binds, printing what it sees",
    preserves="the argv the check built (recorded by a callable replacement); "
              "the check's own verdict over the probe's output, and every "
              "caller's handling of it",
    not_proven="that a container started from the image would print that "
               "output, or that Docker shares those host paths",
    backed_by="pm_mvp::tests.integration.test_launch_bind_check_docker::"
              "test_a_real_project_is_visible_in_one_container_start_and_nothing_is_written",
)
def stub_bind_probe(monkeypatch, replacement) -> None:
    """Replace the bind check's container run. A true external edge: the Docker CLI.

    Armed suite-wide by default: ``tests/conftest.py``'s autouse fixture
    ``_bind_probe_sees_the_project`` answers "visible" in every test not
    marked ``integration``, since every ``run_pipeline`` and ``wfc try``
    launch reaches this call. A test that wants another answer calls this
    entry itself; the later patch wins.

    Considered: ``ready_preflight`` (opt-in, and at another boundary: the
    readiness probes), whose opt-in pattern would mean arming this fake in
    the thirty or so files that drive ``run_pipeline``, and every new
    pipeline test remembering to; ``fake_subprocess_run`` (every subprocess
    at once, so the env preflight's and the engine's fakes would need a
    handler too) and ``stub_dev_loop_launch`` (another caller's launch);
    none stands at this boundary.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``"visible"`` (the probe saw what the host has),
            ``"empty"`` (the container sees no config file and an empty
            cache), an exception raised (``DockerTimeoutError``, ``OSError``),
            a ``CompletedProcess``, or a callable ``(argv, timeout=...)``.
    """
    import subprocess

    from wfc.environments import bind_check

    if replacement in ("visible", "empty"):
        seen = replacement

        def answer(argv, *, timeout):
            config, cache = argv[-2], argv[-1]
            lines = []
            if config:
                size = (_host_config_size(argv, config) if seen == "visible" else None)
                lines.append(f"config {size}" if size is not None else "config none")
            if cache:
                lines.append(f"cache {_host_cache_count(argv) if seen == 'visible' else 0}")
            return subprocess.CompletedProcess(argv, 0, stdout="\n".join(lines) + "\n",
                                               stderr="")

        replacement = answer
    elif isinstance(replacement, subprocess.CompletedProcess):
        replacement = _as_callable(replacement)
    monkeypatch.setattr(bind_check, "_run_probe", _as_callable(replacement))


def _bound_host(argv, container: str) -> str | None:
    """The host side of the ``-v`` row bound at *container* in a docker argv."""
    for flag, value in zip(argv, argv[1:]):
        if flag == "-v":
            host, _, rest = value.rpartition(":" + container)
            if host and rest in ("", ":ro"):
                return host
    return None


def _host_config_size(argv, config: str) -> int | None:
    """The host size of the config file the probe was asked about."""
    from pathlib import Path

    host = _bound_host(argv, "/work")
    if host is None:
        return None
    target = Path(host) / config.removeprefix("/work/")
    return target.stat().st_size if target.is_file() else None


def _host_cache_count(argv) -> int:
    """The host cache's top-level entry count, read through its ``-v`` row."""
    import os

    host = _bound_host(argv, "/dvc-cache")
    return len(os.listdir(host)) if host else 0


@fake(
    boundary="wfc.environments.argv.build_docker_command -- the docker run "
             "argv builder the dev loop shares with dispatch",
    preserves="the inner argv the dev loop hands the builder, recorded",
    not_proven="the argv the builder would have produced",
    backed_by="pm_mvp::tests.test_run_step_container_dispatch::"
              "test_dispatch_and_dev_loop_argv_agree_on_user_binds_and_workdir",
)
def stub_docker_command_builder(replacement: Callable):
    """Return a patch replacing the docker argv builder.

    Args:
        replacement: ``(env_name, inner_argv, ...) -> list[str]``, in the
            builder's own signature.

    Returns:
        The patch context.
    """
    return patch("wfc.environments.argv.build_docker_command",
                 side_effect=replacement)


@fake(
    boundary="wfc.environments.dev_loop._run_subprocess -- the dev loop's "
             "docker run launch",
    preserves="the argv the dev loop built, recorded",
    not_proven="that the container starts",
    backed_by="pm_mvp::tests.integration.test_dev_loop_launch_unfaked::"
              "test_the_dev_loop_starts_a_container_from_the_registered_env",
)
def stub_dev_loop_launch(monkeypatch, replacement: Callable) -> None:
    """Replace the dev loop's subprocess launch.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``(argv) -> int``.
    """
    from wfc.environments import dev_loop

    monkeypatch.setattr(dev_loop, "_run_subprocess", replacement)
