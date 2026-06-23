"""Tier 1 tests: container_runner argv assembly is pure and deterministic.

Covers the boundary discipline encoded in ``wfc.container_runner``:

  - ``build_docker_command`` emits the exact ``docker run --rm --user
    <uid>:<gid>`` argv shape, including the two bind-mounts
    (``<project>:/work`` and ``<dvc_cache>:/dvc-cache``), ``-w /work``,
    and optional ``--gpus all``. Bind-spec paths are normalised to POSIX
    so Docker Desktop accepts Windows-style host paths.
  - ``build_apptainer_command`` emits the cluster-side equivalent with
    ``--bind`` flags, ``--pwd /work``, optional ``--nv``, and never a
    ``--user`` flag (user namespaces handle UID/GID on cluster nodes).

These are forward-port hooks: the cycle ships Apptainer with full unit
coverage even though no v1 caller invokes it (ADR-019 amendment 2026-05-17
carved cluster dispatch out of v1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wfc.container_runner import build_apptainer_command, build_docker_command


IMAGE_REF = "ghcr.io/dante/image-io@sha256:" + ("a" * 64)
RUN_STEP_ARGV = [
    "python", "-m", "wfc", "run-step",
    "--node-id", "n1",
    "--sample", "s1",
    "--variant", "default",
]


def _expected_posix(p: str) -> str:
    """Mirror the helper's path-normalisation so tests pass on Windows
    (where ``Path('/proj').resolve()`` prepends the current drive letter)."""
    return Path(p).resolve().as_posix()


@pytest.mark.parametrize(
    "gpus,use_windows_paths",
    [
        (False, False),  # default POSIX shape, no GPU
        (True,  False),  # --gpus all is injected ahead of the image ref
        (False, True),   # Windows-style host paths normalised to POSIX in bind specs
    ],
    ids=["shape_no_gpu", "with_gpus", "normalises_windows_paths"],
)
def test_build_docker_command(tmp_path, gpus, use_windows_paths):
    """Verbatim argv shape: --rm, --user, two binds, -w /work, image, inner argv.

    Parametrized over (gpus, windows-paths). Cases:

      - ``shape_no_gpu``: asserts the full argv shape (binds, --user, -w),
        no --gpus, and the inner argv is appended verbatim after the image ref.
      - ``with_gpus``: asserts ``--gpus all`` is present and appears before
        the image ref (docker arg-ordering requirement).
      - ``normalises_windows_paths``: passes Windows-style host paths and
        asserts every bind spec is POSIX (no backslashes).
    """
    if use_windows_paths:
        project_root = "C:\\Users\\d\\proj"
        dvc_cache_dir = "C:\\Users\\d\\proj\\.dvc\\cache"
        uid, gid = 0, 0
    else:
        proj = tmp_path / "proj"
        proj.mkdir()
        cache = proj / ".dvc" / "cache"
        cache.mkdir(parents=True)
        project_root = str(proj)
        dvc_cache_dir = str(cache)
        uid, gid = 1000, 1000

    cmd = build_docker_command(
        image_ref=IMAGE_REF,
        project_root=project_root,
        dvc_cache_dir=dvc_cache_dir,
        run_step_argv=RUN_STEP_ARGV,
        uid=uid,
        gid=gid,
        gpus=gpus,
    )

    # Always-true invariants (shape).
    assert cmd[0] == "docker"
    assert cmd[1] == "run"
    assert "--rm" in cmd
    assert "--user" in cmd
    user_idx = cmd.index("--user")
    assert cmd[user_idx + 1] == f"{uid}:{gid}"

    # Two bind-mounts, always POSIX form.
    v_positions = [i for i, tok in enumerate(cmd) if tok == "-v"]
    assert len(v_positions) == 2
    binds = [cmd[i + 1] for i in v_positions]
    for bind in binds:
        assert "\\" not in bind, f"bind spec must be POSIX, got {bind!r}"

    # Working directory inside the container.
    w_idx = cmd.index("-w")
    assert cmd[w_idx + 1] == "/work"

    # GPU-flag presence/absence + ordering.
    if gpus:
        assert "--gpus" in cmd
        gpus_idx = cmd.index("--gpus")
        assert cmd[gpus_idx + 1] == "all"
        # --gpus must appear before the image ref (docker arg ordering).
        assert gpus_idx < cmd.index(IMAGE_REF)
    else:
        assert "--gpus" not in cmd

    # Bind targets match the expected POSIX-normalised host paths (only
    # check on the non-Windows-fixture cases where we know the resolved
    # paths exist on disk).
    if not use_windows_paths:
        binds_set = set(binds)
        assert f"{_expected_posix(project_root)}:/work" in binds_set
        assert f"{_expected_posix(dvc_cache_dir)}:/dvc-cache" in binds_set

    # Image ref precedes the inner argv; inner argv preserved verbatim.
    image_idx = cmd.index(IMAGE_REF)
    assert cmd[image_idx + 1:] == RUN_STEP_ARGV


@pytest.mark.parametrize("gpus", [False, True], ids=["no_gpu", "with_gpus"])
def test_build_apptainer_command(tmp_path, gpus):
    """Apptainer argv: ``apptainer exec``, two ``--bind`` flags, ``--pwd /work``,
    image, then the inner argv. No ``--user`` flag in either case (user
    namespaces own UID/GID on cluster nodes). ``--nv`` is present iff
    ``gpus=True``.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    cache = proj / ".dvc" / "cache"
    cache.mkdir(parents=True)

    cmd = build_apptainer_command(
        image_ref=IMAGE_REF,
        project_root=str(proj),
        dvc_cache_dir=str(cache),
        run_step_argv=RUN_STEP_ARGV,
        gpus=gpus,
    )

    # Shape invariants.
    assert cmd[0] == "apptainer"
    assert cmd[1] == "exec"
    # No --user, ever (user-namespaces own UID/GID on cluster nodes).
    assert "--user" not in cmd

    # GPU-flag presence/absence.
    if gpus:
        assert "--nv" in cmd
    else:
        assert "--nv" not in cmd

    # Two --bind flags pointing at the expected POSIX targets.
    bind_positions = [i for i, tok in enumerate(cmd) if tok == "--bind"]
    assert len(bind_positions) == 2
    binds = {cmd[i + 1] for i in bind_positions}
    assert f"{_expected_posix(str(proj))}:/work" in binds
    assert f"{_expected_posix(str(cache))}:/dvc-cache" in binds

    # Working directory inside the container.
    assert "--pwd" in cmd
    pwd_idx = cmd.index("--pwd")
    assert cmd[pwd_idx + 1] == "/work"

    # Image ref present (helper prefixes with docker://; accept either form).
    found_image = False
    for token in cmd:
        if token.endswith(IMAGE_REF):
            found_image = True
            break
    assert found_image, f"image ref not found in {cmd}"

    # Inner argv appended verbatim at the tail.
    assert cmd[-len(RUN_STEP_ARGV):] == RUN_STEP_ARGV
