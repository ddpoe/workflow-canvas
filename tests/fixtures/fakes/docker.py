"""Docker boundary: the registry calls of register-env and the dev loop's launch.

``wfc.environments.docker`` has four entry points a registration reaches --
``image_inspect``, ``repo_digest``, ``pull``, ``build`` -- and the
register-env tests replace them with a digest, a recorder, a raiser or a
refuser. One entry per entry point, each taking the replacement the site
wrote; ``refuse_docker`` arms all four at once for a test that proves docker
is never reached.
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
              "ensure_runnable's probe, then rebuild or refuse)",
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
    boundary="wfc.environments.docker.build / pull / image_inspect / "
             "repo_digest together",
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

    for entry_point in ("build", "pull", "image_inspect", "repo_digest"):
        monkeypatch.setattr(docker_runner, entry_point, never)


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
