"""Container-runtime argv builders for ADR-019 Cycle D.

This module is the single boundary where ``--user`` + bind-mount + GPU-flag
discipline is encoded for both local Docker (Architecture A: fresh
container per ``wfc run-step``) and the cluster-side Apptainer forward-port
(ADR-019 amendment 2026-05-17 carved cluster dispatch out of v1, but the
argv builder ships now with full coverage so the v1.x wire-up cycle is a
pure plumbing job).

Both functions are **pure**: they return ``list[str]`` argv arrays and
never call ``subprocess.run``, ``os.getuid()``, or touch the filesystem.
UID/GID for the Docker helper are explicit kwargs so the caller (typically
``wfc.cli.run_step``) handles the platform gate (``os.getuid()`` does not
exist on Windows; Docker Desktop ignores ``--user`` on Windows/macOS).

Bind-mount layout is fixed and the same for Docker and Apptainer:

  - ``<project_root>:/work`` -- the wfc project tree, including
    ``methods/``, ``.runs/``, ``.wfc/``, and the pipeline JSON path that
    wfc receives via ``WFC_PIPELINE_JSON``.
  - ``<dvc_cache_dir>:/dvc-cache`` -- the DVC content-addressed cache so
    container-side wfc can resolve inputs and write outputs that survive
    the container teardown.

Nothing else is mounted. The container sees a minimal, well-defined view
of the host filesystem.

GPU plumbing:

  - Docker: ``--gpus all`` (requires nvidia-container-runtime on the host;
    wfc does no pre-flight check -- Docker's own error message propagates
    verbatim).
  - Apptainer: ``--nv`` (the equivalent NVIDIA passthrough flag).

ADR-019 §runtime-execution and §amendments (2026-05-16 dropped
``--verify-env``; 2026-05-17 carved cluster Apptainer out of v1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence


def _posix(p: str | Path) -> str:
    """Normalise *p* to an absolute POSIX-style path string.

    Docker Desktop on Windows and stock Docker on Linux both accept
    POSIX-style paths in ``-v`` bind specs; raw backslashes confuse the
    parser. Apptainer follows the same convention.
    """
    return Path(p).resolve().as_posix()


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
        image_ref: Digest-pinned image reference (e.g.
            ``ghcr.io/dante/image-io@sha256:<hex>``). No scheme prefix --
            the caller has already validated the ref via
            :func:`wfc.envs.validate_container_ref` and stripped the
            ``docker://`` prefix if necessary.
        project_root: Absolute host path to the wfc project. Mounted at
            ``/work`` inside the container; ``-w /work`` is set so the
            container's cwd matches what host-Python execution sees.
        dvc_cache_dir: Absolute host path to the project's DVC cache
            (typically ``<project_root>/.dvc/cache``). Mounted at
            ``/dvc-cache`` so container-side wfc can resolve inputs and
            persist outputs that survive the container teardown.
        run_step_argv: The command line that runs *inside* the container
            -- usually ``[python, -m, wfc, run-step, --node-id, ..., ...]``.
            Appended verbatim after the image ref.
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
        argv list ready to pass to :func:`wfc.cli._run_method_subprocess`.
    """
    cmd: list[str] = [
        "docker", "run", "--rm",
        "--user", f"{uid}:{gid}",
        "-v", f"{_posix(project_root)}:/work",
        "-w", "/work",
        "-v", f"{_posix(dvc_cache_dir)}:/dvc-cache",
    ]
    if gpus:
        cmd.extend(["--gpus", "all"])
    cmd.append(image_ref)
    cmd.extend(list(run_step_argv))
    return cmd


def build_apptainer_command(
    image_ref: str,
    project_root: str | Path,
    dvc_cache_dir: str | Path,
    run_step_argv: Sequence[str],
    *,
    gpus: bool = False,
) -> list[str]:
    """Assemble the ``apptainer exec --bind ... docker://<ref> ...`` argv.

    Forward-port hook for the v1.x cycle that wires cluster dispatch.
    Cycle D ships this argv builder with full unit-test coverage so the
    v1.x cycle is a pure plumbing job (engine select -> build argv ->
    pass to ``_run_method_subprocess``). No v1 caller invokes this
    function; ``wfc.cli.run_step`` rejects ``executor=slurm`` with an
    explicit "out of scope for v1" error.

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
        argv list. **Not invoked by any v1 caller** -- shipped for
        coverage so the v1.x wire-up cycle has no implementation risk.
    """
    cmd: list[str] = [
        "apptainer", "exec",
        "--bind", f"{_posix(project_root)}:/work",
        "--bind", f"{_posix(dvc_cache_dir)}:/dvc-cache",
        "--pwd", "/work",
    ]
    if gpus:
        cmd.append("--nv")
    cmd.append(f"docker://{image_ref}")
    cmd.extend(list(run_step_argv))
    return cmd
