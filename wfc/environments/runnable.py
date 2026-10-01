"""A runnable image for an env: :func:`ensure_runnable`.

A caller about to hand Docker an env's image asks this module for the
image reference. :func:`wfc.environments.argv.daemon_ref` turns a record
into that reference without checking anything; :func:`ensure_runnable`
also makes sure the local Docker daemon can run it. A locally built
(``local/``) image that is missing is rebuilt from the build context
``wfc register-env`` staged, or refused with the command that recreates
it. wfc never pulls a ``local/`` image. A registry image passes straight
through, since Docker pulls a registry digest itself.

Only the local Docker runtime is handled. Cluster Apptainer dispatch is
not supported; a runtime branch for it belongs in :func:`ensure_runnable`.
"""

from __future__ import annotations

from pathlib import Path

from axiom_annotations import AutoStep, Step, workflow

from ..reserved import RESERVED_DEMO_PREFIX
from .argv import _LOCAL_REPOSITORY_PREFIX, daemon_ref, strip_docker_scheme
from .manifest import EnvRecord


class EnvNotRunnableError(RuntimeError):
    """An env's image is missing and wfc cannot rebuild it.

    The message names the env and the command that recreates the image.
    """


def _recreate_command(name: str) -> str:
    """Return the command that recreates *name*'s image from scratch.

    Args:
        name: The env name.

    Returns:
        ``wfc demo --force`` for the reserved demo env, otherwise
        ``wfc register-env <name> --force``.
    """
    if name.startswith(RESERVED_DEMO_PREFIX):
        return "wfc demo --force"
    return f"wfc register-env {name} --force"


@workflow(purpose="Return a daemon ref the local Docker daemon can run for an "
                  "env: probe a local/ image, rebuild a missing pixi or conda "
                  "image from its verified build context or refuse naming the "
                  "command that recreates it; never pull a local/ image",
          inputs="env name, its record, project root",
          outputs="the daemon ref to hand docker run")
def ensure_runnable(name: str, record: EnvRecord, project_dir: Path) -> str:
    """Return a runnable daemon ref for *name*, rebuilding it if needed.

    Args:
        name: The env name (the key in ``.wfc/envs.json``).
        record: The env's record.
        project_dir: The project root.

    Returns:
        The reference to hand ``docker run``: the record's
        :func:`~wfc.environments.argv.daemon_ref`, or the rebuilt record's.

    Raises:
        EnvNotRunnableError: If Docker cannot be asked about a ``local/``
            image, or the image is missing and cannot be rebuilt: a byo
            record, or a pixi/conda env whose build context is missing or no
            longer matches the record.
        RuntimeError: If a rebuild's ``docker build`` fails.
    """
    from . import docker as docker_runner

    口 = Step(step_num=1, name="Probe the image",
             purpose="Pass a registry image through; inspect a local/ image by "
                     "its image ID. Only the daemon's own 'no such image' "
                     "means missing; any other failure is refused as Docker "
                     "being unreachable, never rebuilt")
    ref = daemon_ref(record.container)
    if not strip_docker_scheme(record.container).startswith(
            _LOCAL_REPOSITORY_PREFIX):
        return ref
    try:
        docker_runner.image_inspect(ref)
        return ref
    except docker_runner.ImageNotFoundError:
        pass
    except RuntimeError as exc:
        raise EnvNotRunnableError(
            f"env {name!r}: Docker could not be asked whether it has the "
            f"image {ref}. Check that Docker is running.\n{exc}"
        ) from exc

    from .build import load_build_context, rebuild

    口 = Step(step_num=2, name="Refuse a byo image",
             purpose="wfc does not build a byo image, so a missing one is "
                     "refused naming the recreate command; nothing is pulled")
    missing = (f"env {name!r}: its image {ref} is not in the local Docker "
               f"daemon")
    if record.backend not in ("pixi", "conda"):
        raise EnvNotRunnableError(
            f"{missing}, and wfc does not rebuild a {record.backend} image. "
            f"Recreate it with `{_recreate_command(name)}`."
        )

    口 = AutoStep(step_num=3, name="Load and check the build context")
    source, problem = load_build_context(name, record, project_dir)
    if source is None:
        raise EnvNotRunnableError(
            f"{missing}, and it cannot be rebuilt: {problem}. "
            f"Recreate it with `{_recreate_command(name)}`."
        )

    口 = AutoStep(step_num=4, name="Rebuild the image")
    new_record = rebuild(name, record, source, project_dir)

    口 = Step(step_num=5, name="Return the daemon ref",
             purpose="Hand back the rebuilt record's daemon ref")
    return daemon_ref(new_record.container)
