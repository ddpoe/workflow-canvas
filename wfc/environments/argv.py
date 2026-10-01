"""Container-runtime argv builders.

This module is the single boundary where ``--user`` + bind-mount + GPU-flag
discipline is encoded for both local Docker (a fresh container per
``wfc run-step``) and cluster-side Apptainer. Cluster Apptainer dispatch
is not supported, so no caller invokes the Apptainer builder; it carries
full unit-test coverage.

Both functions are **pure**: they return ``list[str]`` argv arrays and
never call ``subprocess.run``, ``os.getuid()``, or touch the filesystem.
UID/GID for the Docker helper are explicit kwargs so the caller (typically
``wfc.execution.dispatch``) handles the platform gate (``os.getuid()`` does not
exist on Windows; Docker Desktop ignores ``--user`` on Windows/macOS).

Bind-mount layout is fixed and the same for Docker and Apptainer:

  - ``<project_root>:/work`` -- the wfc project tree, including
    ``methods/``, ``.runs/`` and ``.wfc/``, so the method script and its
    run directory are reachable inside the container.
  - ``<dvc_cache_dir>:/dvc-cache`` -- the DVC content-addressed cache, so
    input paths that point into the cache resolve inside the container.

Nothing else is mounted. The container sees a minimal, well-defined view
of the host filesystem.

GPU plumbing:

  - Docker: ``--gpus all`` (requires nvidia-container-runtime on the host;
    wfc does no pre-flight check -- Docker's own error message propagates
    verbatim).
  - Apptainer: ``--nv`` (the equivalent NVIDIA passthrough flag).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from axiom_annotations import task

from .. import layout


def strip_docker_scheme(ref: str) -> str:
    """Return *ref* without its ``docker://`` scheme.

    The byo registration strips a user-supplied reference through here
    before probing or pulling it. :func:`build_apptainer_command` re-adds
    the scheme, which Apptainer needs. A ``docker run`` caller takes
    :func:`daemon_ref` instead, which also names a local env by its image
    ID. A ref without the scheme comes back unchanged.

    Args:
        ref: An image reference, with or without ``docker://``.

    Returns:
        The reference without the scheme.
    """
    return ref.removeprefix("docker://")


#: Repository prefix of every env image wfc builds locally (pixi, conda)
#: and of the demo's locally built byo image.
_LOCAL_REPOSITORY_PREFIX = "local/"


def daemon_ref(record_container: str) -> str:
    """Return the image reference to hand Docker for a recorded env.

    A record's container is ``docker://<host>/<path>@sha256:<hex>``. For a
    registry image that ``@sha256:`` is the registry digest, and the bare
    ``<host>/<path>@sha256:<hex>`` runs on every image store. A locally
    built env is recorded as ``docker://local/<name>@sha256:<hex>`` with
    its image ID in the digest slot; only the containerd store resolves
    that, and the classic store pulls ``local/<name>`` instead. So a
    ``local/`` record is handed over as its bare image ID,
    ``sha256:<hex>``, which both stores run and neither pulls.

    Dispatch and the dev loop hand Docker what this returns; no caller
    parses ``local/`` itself.

    Args:
        record_container: The record's container ref, with or without the
            ``docker://`` scheme.

    Returns:
        ``sha256:<hex>`` for a ``local/`` image, otherwise the reference
        without its scheme.
    """
    bare = strip_docker_scheme(record_container)
    if bare.startswith(_LOCAL_REPOSITORY_PREFIX) and "@" in bare:
        return bare.rsplit("@", 1)[1]
    return bare


@task(purpose="Assemble the docker run argv — image ref, project + DVC-cache bind "
              "mounts, --user uid:gid, optional --gpus")
def build_docker_command(
    image_ref: str,
    project_root: str | Path,
    dvc_cache_dir: str | Path,
    run_step_argv: Sequence[str],
    *,
    uid: int,
    gid: int,
    gpus: bool = False,
) -> list[str]:
    """Assemble the ``docker run --rm --user <uid>:<gid> ...`` argv.

    The argv shape is verbatim::

        docker run --rm --user <uid>:<gid>
                   -v <project_root>:/work -w /work
                   -v <dvc_cache_dir>:/dvc-cache
                   [--gpus all]
                   <image_ref>
                   <run_step_argv...>

    Args:
        image_ref: The record's daemon ref from :func:`daemon_ref`:
            ``sha256:<hex>`` for a local env, or a registry image's
            ``ghcr.io/dante/image-io@sha256:<hex>``. No scheme prefix.
        project_root: Absolute host path to the wfc project. Mounted at
            ``/work`` inside the container; ``-w /work`` makes the project
            root the container's cwd.
        dvc_cache_dir: Absolute host path to the project's DVC cache
            (typically ``<project_root>/.dvc/cache``). Mounted at
            ``/dvc-cache`` so input paths that point into the cache resolve
            inside the container.
        run_step_argv: The command line that runs *inside* the container.
            For a method run it is ``[<env interpreter>, <script path>]``:
            the script runs directly under the env's own interpreter, with
            no wfc inside the image. The dev-loop commands (shell, exec,
            jupyter) pass their own command. Appended verbatim after the
            image ref.
        uid: Host UID to run the container process as. Caller passes
            ``os.getuid()`` on Linux; on Windows/macOS the value is
            ignored by Docker Desktop but must still be supplied (caller
            uses ``getattr(os, 'getuid', lambda: 0)()``).
        gid: Host GID, supplied the same way as ``uid``.
        gpus: When true, inject ``--gpus all`` before the image ref so
            CUDA/PyTorch methods get host GPU access. No pre-flight check
            -- if the host lacks ``nvidia-container-runtime``, Docker's
            own error propagates verbatim.

    Returns:
        argv list ready to pass to :func:`wfc.execution.dispatch._run_method_subprocess`.
    """
    # Binds render from Layout's mount table — the same rows translation
    # uses — with the workdir set on the project mount.
    cmd: list[str] = ["docker", "run", "--rm", "--user", f"{uid}:{gid}"]
    for mount in layout.mount_table(project_root, dvc_cache_dir):
        cmd.extend(["-v", layout.bind_spec(mount)])
        if mount.container == layout.CONTAINER_WORKDIR:
            cmd.extend(["-w", layout.CONTAINER_WORKDIR])
    if gpus:
        cmd.extend(["--gpus", "all"])
    cmd.append(image_ref)
    cmd.extend(list(run_step_argv))
    return cmd


@task(purpose="Assemble the apptainer exec argv for the cluster path — docker:// "
              "image, binds, env")
def build_apptainer_command(
    image_ref: str,
    project_root: str | Path,
    dvc_cache_dir: str | Path,
    run_step_argv: Sequence[str],
    *,
    gpus: bool = False,
) -> list[str]:
    """Assemble the ``apptainer exec --bind ... docker://<ref> ...`` argv.

    Argv builder for cluster dispatch, covered by unit tests. No caller
    invokes this function: ``wfc.execution.dispatch`` rejects
    ``executor=slurm`` with an explicit "cluster Apptainer dispatch
    (executor=slurm) is not supported" error.

    Note the absence of ``--user``: Apptainer relies on user-namespaces
    to run as the invoking user automatically, so the docker-style UID/GID
    flag has no equivalent. GPU passthrough uses ``--nv`` rather than
    Docker's ``--gpus all``.

    Args:
        image_ref: Digest-pinned image reference. The helper prefixes
            with ``docker://`` automatically because Apptainer pulls from
            OCI registries via that URI scheme; the caller supplies the
            bare ref as stored in the manifest.
        project_root: Absolute host path; bound at ``/work``.
        dvc_cache_dir: Absolute host path; bound at ``/dvc-cache``.
        run_step_argv: Inner command line, appended verbatim after the
            image URI.
        gpus: When true, inject ``--nv`` (Apptainer's NVIDIA passthrough).

    Returns:
        argv list. **Not invoked by any caller.**
    """
    cmd: list[str] = ["apptainer", "exec"]
    for mount in layout.mount_table(project_root, dvc_cache_dir):
        cmd.extend(["--bind", layout.bind_spec(mount)])
    cmd.extend(["--pwd", layout.CONTAINER_WORKDIR])
    if gpus:
        cmd.append("--nv")
    cmd.append(f"docker://{image_ref}")
    cmd.extend(list(run_step_argv))
    return cmd
