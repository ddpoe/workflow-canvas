"""A runnable image for an env image: :func:`ensure_version_runnable`.

A caller about to hand Docker an env image asks this module for the image
reference. The run-time rule has three branches, in order:

1. the image is on this machine: run it (its
   :func:`wfc.environments.argv.daemon_ref`);
2. otherwise, when it can be pulled (its captures folder records a push,
   or it is a byo image recorded by its own registry's digest), pull that
   digest-pinned ref, check that the pulled image's content renders the
   recorded ``env_fingerprint``, and run the registry ref;
3. otherwise refuse, naming the command that recreates the env and
   ``wfc register-method``.

wfc never builds an image here: building is only ever ``wfc register-env``
or ``wfc demo``. :func:`ensure_runnable` is the same rule for an env's
current record (the dev loop's question). :func:`version_run_ref` is the
rule's no-network half, for dispatch: the ref the pre-flight returned,
without pulling, so Docker (run with ``--pull never``) is handed the image
the pre-flight checked and never a recomputed image ID.

Only the local Docker runtime is handled. Cluster Apptainer dispatch is
not supported.
"""

from __future__ import annotations

from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow

from .argv import daemon_ref
from .manifest import EnvRecord, recreate_command


class EnvNotRunnableError(RuntimeError):
    """An env's image is not on this machine and cannot be pulled and run.

    The message names the env and what to do: start Docker, push the image
    from a machine that has it, or recreate the env and register its
    methods again.
    """


def _recorded_image(env: str, fingerprint: str | None, project_dir: Path,
                    image: str | None) -> str:
    """Return the image's recorded ``docker://`` ref, from ``image.json`` if not given."""
    if image:
        return image
    from .captures import CaptureMissingError, image_info

    try:
        return image_info(env, fingerprint, project_dir).image
    except CaptureMissingError as exc:
        raise EnvNotRunnableError(
            f"env {env!r}: the image {fingerprint or '<none>'} cannot be run: "
            f"{exc}. Recreate it with `{recreate_command(env)}`, then register "
            f"the methods that use it again with `wfc register-method`."
        ) from exc


def _present(env: str, ref: str) -> bool:
    """Whether the local daemon holds *ref*; refuse when Docker does not answer."""
    from . import docker as docker_runner

    try:
        docker_runner.image_inspect(ref)
    except docker_runner.ImageNotFoundError:
        return False
    except (RuntimeError, OSError) as exc:
        raise EnvNotRunnableError(
            f"env {env!r}: Docker could not be asked whether it has the "
            f"image {ref}. Check that Docker is running.\n{exc}"
        ) from exc
    return True


def _recreate_advice(env: str) -> str:
    """The remedy for an image no registry can supply: recreate it, then re-register.

    Lower case: a refusal puts it after "Otherwise", or after "Push it ..., or".
    """
    return (f"recreate it with `{recreate_command(env)}` and then register "
            f"the methods that use it again with `wfc register-method`; their "
            f"steps will then run again rather than reuse cached results.")


def _capitalised(text: str) -> str:
    """*text* with its first letter upper case, to start a sentence."""
    return text[:1].upper() + text[1:]


def _never_pushed_advice(env: str) -> str:
    """The remedy for an image never pushed: push it from where it is, or recreate it."""
    return (f"Push it from a machine that has it with `wfc push-env {env}`, or "
            f"{_recreate_advice(env)}")


@task(purpose="Pull an env image by its digest-pinned registry ref and check "
              "that the pulled image's content renders the env_fingerprint "
              "its record names, with the code register fingerprints with",
      inputs="env name, the recorded env fingerprint, the registry ref",
      outputs="nothing, or EnvNotRunnableError naming Docker's cause or the "
              "fingerprint the pulled content renders")
def pull_and_check(env: str, fingerprint: str, ref: str, *,
                   pull: bool = True) -> None:
    """Pull *ref* and check its content against *fingerprint*.

    Args:
        env: The env name.
        fingerprint: The ``env_fingerprint`` the image's record names.
        ref: The digest-pinned registry ref.
        pull: ``False`` when the daemon already holds *ref*: only the
            content is checked.

    Raises:
        EnvNotRunnableError: The pull failed (Docker's words kept), the
            pulled image could not be inspected, or its content renders
            another fingerprint.
    """
    from . import docker as docker_runner
    from .fingerprint import content_fingerprint
    from .registry import ref_host

    if pull:
        print(f"  env {env}: image not on this machine; pulling {ref}",
              flush=True)
        try:
            docker_runner.pull(ref)
        except (RuntimeError, OSError) as exc:
            raise EnvNotRunnableError(
                f"env {env!r}: its image is not on this machine and pulling "
                f"{ref} failed: {exc}\nIf the registry needs a login, run "
                f"`docker login {ref_host(ref)}` and try again (`wfc share` "
                f"checks the project's sharing setup). Otherwise "
                f"{_recreate_advice(env)}"
            ) from exc
    try:
        found = content_fingerprint(docker_runner.image_content(ref))
    except (RuntimeError, OSError, ValueError) as exc:
        raise EnvNotRunnableError(
            f"env {env!r}: the pulled image {ref} could not be inspected: {exc}"
        ) from exc
    if found != fingerprint:
        raise EnvNotRunnableError(
            f"env {env!r}: the image pulled from {ref} is not the image its "
            f"record names: its content renders env fingerprint {found}, the "
            f"record names {fingerprint}, so it is not run. "
            f"{_capitalised(_recreate_advice(env))}"
        )


@workflow(purpose="Return the ref Docker runs for one env image: the image on "
                  "this machine as it is; else pull its pushed or byo registry "
                  "digest, check the pulled content renders the recorded env "
                  "fingerprint, and return the registry ref; else refuse naming "
                  "the recreate command then wfc register-method. Never builds",
          inputs="env name, the image's env fingerprint, project root, "
                 "optionally the recorded image ref",
          outputs="the ref to hand docker run, or EnvNotRunnableError",
          critical="Docker not answering is refused as Docker being down, "
                   "never as a missing image")
def ensure_version_runnable(env: str, fingerprint: str | None,
                            project_dir: Path, *, image: str | None = None) -> str:
    """Make the image *env* recorded at *fingerprint* runnable, or refuse.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint`` (a method version's, or
            the env's current record's).
        project_dir: The project root.
        image: The image's recorded ``docker://`` ref; ``None`` reads it
            from the image's ``image.json``.

    Returns:
        The image's :func:`~wfc.environments.argv.daemon_ref` when the
        daemon holds it; otherwise the registry ref it was pulled by.

    Raises:
        EnvNotRunnableError: Docker does not answer; the image is missing and
            cannot be pulled; the pull failed; or the pulled content renders
            another fingerprint.
    """
    project_dir = Path(project_dir)
    口 = Step(step_num=1, name="Read the recorded image",
             purpose="The image ref image.json records for (env, fingerprint), "
                     "unless the caller has it")
    recorded = _recorded_image(env, fingerprint, project_dir, image)
    local = daemon_ref(recorded)

    口 = Step(step_num=2, name="Run what this machine has",
             purpose="Inspect the image's daemon ref; only the daemon's own "
                     "'no such image' means missing")
    if _present(env, local):
        return local

    口 = Step(step_num=3, name="Find the ref it can be pulled by",
             purpose="A pushed record's <base>/<env>@<digest>, or a byo "
                     "registry image's recorded ref; neither is refused")
    from .captures import CaptureError
    from .registry import pullable_ref

    try:
        ref = pullable_ref(env, fingerprint, recorded, project_dir)
    except (ValueError, CaptureError) as exc:
        raise EnvNotRunnableError(f"env {env!r}: {exc}") from exc
    if ref is None:
        raise EnvNotRunnableError(
            f"env {env!r}: its image {local} is not on this machine and was "
            f"never pushed to a registry, so it cannot be pulled. "
            f"{_never_pushed_advice(env)}"
        )
    if not fingerprint:
        raise EnvNotRunnableError(
            f"env {env!r}: its image {local} is not on this machine, and its "
            f"record names no env fingerprint to check a pulled image "
            f"against. {_capitalised(_recreate_advice(env))}"
        )
    already = ref != local and _present(env, ref)

    口 = AutoStep(step_num=4, name="Pull and check the content")
    pull_and_check(env, fingerprint, ref, pull=not already)
    return ref


def version_run_ref(env: str, fingerprint: str | None, project_dir: Path, *,
                    image: str | None = None) -> str:
    """Return the ref dispatch hands Docker for an image the pre-flight made runnable.

    The no-network half of :func:`ensure_version_runnable`: the recorded
    image's daemon ref when the daemon holds it, else the ref it is pulled
    by. Nothing is pulled, built or committed, and the ref is never a
    recomputed image ID. An image that cannot be pulled (or a Docker that
    does not answer) gets its daemon ref, and ``docker run --pull never``
    reports what is wrong.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        project_dir: The project root.
        image: The image's recorded ``docker://`` ref; ``None`` reads it
            from ``image.json``.

    Returns:
        The reference to hand ``docker run``.

    Raises:
        EnvNotRunnableError: *image* is ``None`` and ``image.json`` cannot
            be read.
    """
    from . import docker as docker_runner
    from .captures import CaptureError
    from .registry import pullable_ref

    recorded = _recorded_image(env, fingerprint, Path(project_dir), image)
    local = daemon_ref(recorded)
    try:
        ref = pullable_ref(env, fingerprint, recorded, Path(project_dir))
    except (ValueError, CaptureError):
        return local
    if ref is None or ref == local:
        return local
    try:
        docker_runner.image_inspect(local)
    except docker_runner.ImageNotFoundError:
        return ref
    except (RuntimeError, OSError):
        return local
    return local


def ensure_runnable(name: str, record: EnvRecord, project_dir: Path) -> str:
    """Return a runnable ref for the image *name*'s current record names.

    :func:`ensure_version_runnable` for the record's own image: present,
    else pulled and checked, else refused. Nothing is built.

    Args:
        name: The env name (the key in ``.wfc/envs.json``).
        record: The env's record.
        project_dir: The project root.

    Returns:
        The reference to hand ``docker run``.

    Raises:
        EnvNotRunnableError: As :func:`ensure_version_runnable` raises.
    """
    return ensure_version_runnable(name, record.env_fingerprint, project_dir,
                                   image=record.container)
