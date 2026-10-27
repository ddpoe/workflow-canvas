"""The env verbs: ``wfc list-envs``, ``show-env``, ``delete-env`` and ``register-env``.

Each body takes plain values, prints what the verb prints and returns its
exit code; ``wfc/cli.py`` parses the arguments and calls it.

``register-env`` runs in four parts: the argument checks
(:func:`_check_register_args`), the existing-name refusal and the caller's
Docker gate, the staging by mode (:func:`_stage_source`), and the call to
:func:`wfc.environments.register` (:func:`_register_record`).  Like
``delete-env``'s references, the Docker probe arrives as a value (a callable),
so this package never imports Execution.

``delete-env`` takes the method versions that record the env (or the error
reading them) as a value, and ``register-env`` the methods whose newest
version uses it (its dependants). The caller reads them from Registration,
so this package never imports Registration.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from axiom_annotations import task

from .. import layout
from ..persistence import project_root as get_project_root

if TYPE_CHECKING:
    from .manifest import EnvRecord
    from .registry import RegistryChip


class _Refused(Exception):
    """A ``register-env`` refusal: the verb prints ``ERROR: <message>`` and exits 1."""


# =============================================================================
# list-envs, show-env, delete-env
# =============================================================================


#: Reads the method versions recording an env: ``[(module/method vN,
#: env_fingerprint)]``, as Registration's ``version_fingerprints_recording_env``
#: does. ``ValueError`` for an unreadable version list.
VersionsOf = Callable[[str], Sequence[tuple[str, str | None]]]


def _registry_chip(name: str, record: EnvRecord, project_dir: Path,
                   versions_of: VersionsOf | None) -> RegistryChip:
    """The env's :func:`wfc.environments.registry_chip`, as the canvas row reads it.

    The versions are read through *versions_of*; an unreadable version list
    counts no older image, as the canvas does.
    """
    from .registry import registry_chip

    versions: Sequence[tuple[str, str | None]] = []
    if versions_of is not None:
        try:
            versions = versions_of(name)
        except (ValueError, RuntimeError):
            versions = []
    return registry_chip(name, project_dir, container=record.container,
                         fingerprint=record.env_fingerprint, versions=versions)


def _registry_lines(name: str, record: EnvRecord, project_dir: Path,
                    versions_of: VersionsOf | None) -> list[tuple[str, str]]:
    """show-env's registry lines: the chip's word, the base, the refs, the fixes.

    The pushed ref and digest come from the image's ``pushed.json``; a byo
    image's own registry ref and a name's reason come from the chip; the
    notes are the canvas tooltip's lines (:func:`wfc.environments.chip_notes`).
    """
    from .captures import CaptureError, pushed_record
    from .registry import CHIP_PUSHED, chip_label, chip_notes, registry_ref

    chip = _registry_chip(name, record, project_dir, versions_of)
    lines = [("registry_status", chip_label(chip)),
             ("registry_base", chip.base or "")]
    if chip.state == CHIP_PUSHED and chip.own_registry:
        lines.append(("own_registry", chip.own_registry))
    elif chip.state == CHIP_PUSHED:
        try:
            pushed = pushed_record(name, record.env_fingerprint, project_dir)
        except CaptureError:
            pushed = None
        if pushed is not None:
            if chip.base:
                lines.append(("pushed_ref", registry_ref(chip.base, name, pushed.digest)))
            lines.append(("pushed_digest", pushed.digest))
    lines.extend(("registry_note", note) for note in chip_notes(name, chip))
    return lines


def list_envs(*, versions_of: VersionsOf | None = None) -> int:
    """``wfc list-envs``: print every env in ``.wfc/envs.json`` as a table.

    The REGISTRY column says where each env's image is, in the canvas
    Registry chip's words (:func:`wfc.environments.chip_label` of the same
    :func:`wfc.environments.registry_chip`): read from the captures folders
    and the local daemon, never the registry; Docker not answering is
    ``unknown``.

    Args:
        versions_of: Reads the method versions recording an env (the caller
            reads Registration); ``None`` counts no older images.

    Returns:
        0 on success; 1 when no project is found or the manifest is unreadable.
    """
    from .. import environments as envs_mod
    from .registry import chip_label

    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1

    try:
        records = envs_mod.list_envs(project_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not records:
        print("No container envs registered. "
              "Use `wfc register-env` to add one.")
        return 0

    # records is list[tuple[str, EnvRecord]] (name is the manifest dict KEY,
    # not a record field).
    # Fixed-width table: name | backend | container | built_at | registry
    headers = ("NAME", "BACKEND", "CONTAINER", "BUILT AT", "REGISTRY")
    rows = [
        (name, r.backend, r.container, r.built_at or "",
         chip_label(_registry_chip(name, r, project_dir, versions_of)))
        for name, r in records
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))
    return 0


def show_env(name: str, *, versions_of: VersionsOf | None = None) -> int:
    """``wfc show-env <name>``: print the full record as key/value lines.

    Then the registry lines, from the same chip as ``wfc list-envs``'s
    REGISTRY column: ``registry_status`` (the chip's word and older counts),
    ``registry_base``, ``pushed_ref`` and ``pushed_digest`` for an image
    wfc pushed or ``own_registry`` for a byo registry image, and one
    ``registry_note`` per canvas-tooltip line, naming the fixing command.

    Args:
        name: The env to show.
        versions_of: Reads the method versions recording an env (the caller
            reads Registration); ``None`` counts no older images.

    Returns:
        0 on success; 1 when no project is found, the manifest is unreadable
        or no env has that name.
    """
    from .. import environments as envs_mod

    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1

    try:
        record = envs_mod.get(name, project_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if record is None:
        print(f"ERROR: env {name!r} not found in .wfc/envs.json", file=sys.stderr)
        return 1

    # The name is the manifest dict KEY, not a record field; print it
    # separately so users still see it on `wfc show-env <name>`.
    data = record.to_dict()
    keys = (
        "name", "backend", "source", "container", "python",
        "env_fingerprint", "source_fingerprint",
        "built_from_lock", "built_at",
    )
    registry = _registry_lines(name, record, project_dir, versions_of)
    width = max(len(k) for k in (*keys, *(key for key, _ in registry)))
    for key in keys:
        value: object
        if key == "name":
            value = name
        else:
            value = data.get(key)
        print(f"{key:<{width}} : {value if value is not None else ''}")
    for key, text in registry:
        print(f"{key:<{width}} : {text}")
    return 0


def delete_env(name: str, *, references: list[str], read_error: str | None = None,
               force: bool = False) -> int:
    """``wfc delete-env <name>``: remove an env no method version records.

    While any version of any method records the env, the verb refuses and
    names every such version, ``--force`` or not: removing the env would
    strand them. It refuses too when the caller could not read a version
    list, since an unreadable record is no permission. Otherwise it prompts
    (unless *force*), removes the env's record and its captures folder
    ``.wfc/captures/<env>/``, and commits both in one commit (no commit
    outside a git repository). Registry images are never touched.

    Args:
        name: The env to remove.
        references: ``module/method vN`` for every version that records
            *name*, read by the caller from the committed version lists.
        read_error: The caller's error when a version list could not be
            read (it names the file), or ``None``.
        force: Skip the confirmation prompt.

    Returns:
        0 when the env was deleted; 1 when no project is found, the manifest
        is unreadable, no env has that name, a version records it, a version
        list is unreadable, the user aborts, or git refuses the commit.
    """
    from .. import environments as envs_mod

    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1

    try:
        record = envs_mod.get(name, project_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if record is None:
        print(f"ERROR: env {name!r} not found in .wfc/envs.json", file=sys.stderr)
        return 1

    if read_error is not None:
        print(f"ERROR: env {name!r} was not deleted: the method versions that "
              f"record it cannot be read.\n{read_error}", file=sys.stderr)
        return 1
    if references:
        lines = "\n".join(f"  - {ref}" for ref in references)
        print(f"ERROR: env {name!r} was not deleted: {len(references)} method "
              f"version(s) record it and would no longer run:\n{lines}\n"
              f"Re-register those methods with another env, or keep it.",
              file=sys.stderr)
        return 1

    if not force:
        try:
            answer = input(f"Delete env {name!r}? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 1

    try:
        sha = _remove_env(name, project_dir)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    captures = f"{layout.STATE_DIR_NAME}/{layout.CAPTURES_DIR_NAME}/{name}/"
    print(f"Deleted env {name!r} from {layout.STATE_DIR_NAME}/"
          f"{layout.ENV_MANIFEST_FILENAME} and {captures}. "
          f"Registry images were NOT removed.")
    if sha is not None:
        print(f"  git: committed as {sha[:12]}")
    return 0


@task(purpose="Remove an env's record and its captures folder and commit both "
              "in one commit; a failed manifest write (the record deletion) "
              "or a refused commit puts both back",
      inputs="an env name, the project root",
      outputs="the commit's SHA, or None outside a git repository")
def _remove_env(name: str, project_dir: Path) -> str | None:
    """Remove *name*'s record and ``.wfc/captures/<name>/``, then commit both.

    The folder is moved aside first and the manifest's bytes kept, so when
    the record deletion fails to write the manifest or git refuses the
    commit (a hook, a timeout) both are put back and the tree is as it was.
    Outside a git repository nothing is committed.

    Args:
        name: The env to remove (its record exists).
        project_dir: The project root.

    Returns:
        The commit's SHA, or ``None`` when nothing was committed.

    Raises:
        RuntimeError: The manifest write failed or git refused the commit;
            envs.json and the captures folder are restored, nothing removed.
    """
    import shutil
    import tempfile

    from .. import environments as envs_mod
    from ..version import commit_paths

    manifest_path = layout.env_manifest_path(project_dir)
    prior_manifest = manifest_path.read_bytes()
    captures = layout.env_captures_dir(project_dir, name)
    aside: Path | None = None
    if captures.exists():
        # Aside in a sibling of the folder: a rename on the same drive, never
        # staged (the commit names only the record and the folder).
        aside = Path(tempfile.mkdtemp(prefix=".delete-env-",
                                      dir=captures.parent)) / name
        shutil.move(str(captures), str(aside))
    try:
        envs_mod.delete(name, project_dir)
        sha = commit_paths(project_dir, [manifest_path, captures],
                           f"Delete env {name}", require_repo=False)
    except (RuntimeError, OSError, ValueError) as exc:
        manifest_path.write_bytes(prior_manifest)
        if aside is not None:
            shutil.move(str(aside), str(captures))
            shutil.rmtree(aside.parent, ignore_errors=True)
        raise RuntimeError(
            f"{exc}\nEnv {name!r} was not deleted: {layout.STATE_DIR_NAME}/"
            f"{layout.ENV_MANIFEST_FILENAME} and its captures are as they were."
        ) from exc
    if aside is not None:
        shutil.rmtree(aside.parent, ignore_errors=True)
    return sha


# =============================================================================
# register-env
# =============================================================================


def register_env(
    name: str,
    *,
    spec: str | None = None,
    backend: str | None = None,
    from_path: str | None = None,
    image: str | None = None,
    base_image: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    interpreter_path: str | None = None,
    python_path: str | None = None,
    docker_gate: Callable[[], str | None],
    dependants: Sequence[str] = (),
    groups: Sequence[str] = (),
    extras: Sequence[str] = (),
    recording_versions: Sequence[tuple[str, str]] = (),
    recording_read_error: str | None = None,
    no_push: bool = False,
) -> int:
    """Build a container image for an env and register it in ``.wfc/envs.json``.

    Implements ``wfc register-env <name> [<spec>] [--backend X] [--from PATH]``.

    Three input modes are accepted:

    1. **Positional typed-spec** (``wfc register-env my conda:cell_pose`` /
       ``wfc register-env my pixi:wcia:hello``). The spec re-uses the
       vocabulary already parsed by ``method.yaml``'s ``env:`` field.
       The verb resolves the live env (a pixi env's interpreter via
       :func:`wfc.environments.host.resolve_python_for_env`, a conda env's
       prefix via :func:`wfc.environments.host.resolve_conda_env_dir`),
       captures the package list (conda explicit-list / full pixi.lock +
       pip freeze), and stages it into the build context.
       :func:`wfc.environments.register` then stores a content-addressed md5
       of the staged source blob as :attr:`EnvRecord.source_fingerprint` so
       a reviewer can inspect exactly what went into the image.

    2. **File mode** (``--from <path> --backend X``). Copies the file
       into the build context under the generator's expected filename
       (``explicit-list.txt`` for conda, ``pixi.lock`` for pixi). For
       pixi, an adjacent ``pixi.toml`` next to the lock file is also
       copied when present. ``--backend`` is required in this mode (no
       extension sniffing — keeps the contract simple).
       :attr:`EnvRecord.source_fingerprint` is recorded here too (from the
       staged lock / explicit-list, with an empty pip-freeze section), so
       the package contents are inspectable via ``GET .../packages``.
       For ``--backend uv``, ``--from`` names a uv project directory or its
       ``uv.lock``: :func:`wfc.environments.uv.capture_project` checks the
       project and exports its requirements, with ``--group``/``--extra``
       passed through.

    3. **BYO image mode** (``--backend byo --image docker://...``). No
       Dockerfile is generated; the upstream image is digest-resolved and
       recorded as-is.

    For pixi/conda, a typed spec or ``--from`` is required, and for uv
    ``--from``; ``--backend pixi|conda|uv`` alone is refused.

    Capturing from a live env records the env's current state, including
    any ad-hoc ``pip install`` mutations on top of the conda/pixi env.
    Inspect the captured package list (md5 = ``source_fingerprint``) in
    ``.wfc/captures/<name>/<env_fingerprint>/packages.lock`` before relying
    on the image for downstream runs.

    Args:
        name: The env name to register.
        spec: The positional typed spec (``conda:<env>``,
            ``pixi:<proj>:<env>``, ``pixi:<name>``), or ``None``.
        backend: ``--backend``: ``pixi``, ``conda`` or ``byo``.
        from_path: ``--from``: the lock or explicit-list file for file mode.
        image: ``--image``: the upstream image for byo mode.
        base_image: ``--base-image``: overrides the generator's base image.
        force: ``--force``: replace an existing record.
        dry_run: ``--dry-run``: render the Dockerfile and stop.
        interpreter_path: ``--interpreter``: the recorded interpreter.
        python_path: ``--python``: the legacy spelling of ``--interpreter``.
        docker_gate: The Docker readiness probe, passed in by the caller
            (this package never imports Execution). It returns the message to
            print when Docker cannot build, or ``None`` when it can.
        dependants: ``module/method`` for every method whose newest version
            records this env, read by the caller from the registry (this
            package never imports Registration). After a successful build
            they are listed with the instruction to register them again:
            a method version records the env's image as it was when the
            version was registered, so rebuilding the env changes no
            version and no method runs the new image until it is
            registered again. An empty sequence prints nothing.
        groups: ``--group``: a uv project's dependency groups (uv only).
        extras: ``--extra``: a uv project's extras (uv only).
        recording_versions: ``(module/method vN, env_fingerprint)`` for
            every method version that records the env, read by the caller
            from the committed version lists. When the built image's
            captures folder is already there with another interpreter, a
            version recording that image refuses the registration (exit 1,
            naming the versions and the new-env-name route); with none,
            only ``image.json``'s interpreter is rewritten.
        recording_read_error: The caller's error when a version list could
            not be read; such an interpreter change is then refused.
        no_push: ``--no-push``: keep the image on this machine even when
            the project has a registry base. Without it, and with a base
            set, the image is pushed before the register commit and a
            failed push records nothing (exit 1, naming the registry and
            ``--no-push``). The output says where the image went.

    The checks run cheapest first, so each refusal the user can fix on the
    command line is reported even on a host with no Docker: the argument
    checks, then (after ``--dry-run``'s early return, which never touches
    Docker) the existing-name refusal, then ``docker_gate``, then staging
    and the build. A uv project is staged before ``docker_gate``: its
    staging runs only on the host (the lock checks and ``uv export``), so
    its refusals come before any Docker work.

    Returns:
        0 on success (the image reference is printed); 1 on any refusal.
    """
    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1

    try:
        resolved_backend, live, interpreter = _check_register_args(
            spec=spec,
            backend=backend,
            from_path=from_path,
            dry_run=dry_run,
            interpreter_path=interpreter_path,
            python_path=python_path,
            groups=groups,
            extras=extras,
        )
    except _Refused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # ---- --dry-run early-exit ----
    # Render the Dockerfile only; no staging, no docker, no manifest write.
    # Rides file mode for pixi/conda (the generators are pure string
    # functions — the lock/list content is only read by docker at build
    # time, so nothing needs staging to render).
    if dry_run:
        from .uv import UV_BACKEND as _UV_BACKEND

        dry_source: dict = {}
        if resolved_backend == _UV_BACKEND:
            # The uv recipe's base is the project's Python minor, which the
            # host-only checks and export read; nothing is staged.
            try:
                if from_path is None:  # _check_register_args requires it
                    raise _Refused("--backend uv requires --from <project dir>")
                dry_source = _stage_uv_project(Path(from_path).resolve(),
                                               groups, extras)
            except _Refused as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
        return _register_env_dry_run(
            name=name,
            backend=resolved_backend,
            base_image=base_image,
            image=image,
            project_dir=project_dir,
            source=dry_source,
        )

    # ---- Existing name, then Docker: both before any staging but uv's ----
    try:
        _refuse_existing_name(name=name, force=force, project_dir=project_dir)
    except _Refused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    def stage() -> dict:
        return _stage_source(
            live=live,
            spec=spec,
            backend=resolved_backend,
            from_path=from_path,
            image=image,
            project_dir=project_dir,
            groups=groups,
            extras=extras,
        )

    # A uv project is staged on the host alone (its lock checks and
    # `uv export`), so its refusals come before Docker is asked.
    from .uv import UV_BACKEND

    source: dict | None = None
    if resolved_backend == UV_BACKEND:
        try:
            source = stage()
        except _Refused as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    docker_message = docker_gate()
    if docker_message is not None:
        print(docker_message, file=sys.stderr)
        return 1

    # ---- Full build path ----
    try:
        if source is None:
            source = stage()
        record = _register_record(
            name=name,
            backend=resolved_backend,
            source=source,
            base_image=base_image,
            force=force,
            project_dir=project_dir,
            python_override=interpreter,
            recording_versions=recording_versions,
            recording_read_error=recording_read_error,
            push=not no_push,
        )
    except _Refused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(record.container)
    if dependants:
        _print_dependants(name, dependants)
    return 0


def _print_dependants(name: str, dependants: Sequence[str]) -> None:
    """Print the methods whose newest version uses a rebuilt env.

    Args:
        name: The env that was built.
        dependants: ``module/method`` references, in the order to print.
    """
    print(f"{len(dependants)} method(s) run env {name!r} in their newest "
          f"version, which records the env's image from before this build:")
    for ref in dependants:
        print(f"  - {ref}")
    print("  They keep running that image until registered again. Register "
          "them again with `wfc register-method` to run the rebuilt env.")


def _check_register_args(
    *,
    spec: str | None,
    backend: str | None,
    from_path: str | None,
    dry_run: bool,
    interpreter_path: str | None,
    python_path: str | None,
    groups: Sequence[str] = (),
    extras: Sequence[str] = (),
) -> tuple[str, bool, str | None]:
    """The argument checks of ``register-env``, run before any staging or Docker work.

    Args:
        spec: The positional spec, or ``None``.
        backend: ``--backend``, or ``None``.
        from_path: ``--from``, or ``None``.
        dry_run: Whether ``--dry-run`` was passed.
        interpreter_path: ``--interpreter``, or ``None``.
        python_path: ``--python``, or ``None``.
        groups: ``--group`` values (valid with ``--backend uv`` only).
        extras: ``--extra`` values (valid with ``--backend uv`` only).

    Returns:
        ``(backend, live, interpreter)``: the backend (from the typed spec or
        ``--backend``), whether the spec names a live env to capture, and the
        ``--interpreter``/``--python`` value.

    Raises:
        _Refused: The arguments contradict each other or name no source.
    """
    # ---- Mutex enforcement: typed-spec ⨯ --backend ⨯ --from ----
    inferred_backend: str | None = (
        _typed_spec_backend(spec) if spec else None
    )

    if inferred_backend is not None and backend is not None:
        raise _Refused(
            f"positional typed-spec '{spec}' implies "
            f"backend '{inferred_backend}'; do not also pass --backend."
        )
    if inferred_backend is not None and from_path is not None:
        raise _Refused(
            f"positional typed-spec '{spec}' captures "
            f"from a live env; --from is for file-mode only."
        )
    if from_path is not None and backend is None:
        raise _Refused("--from <path> requires --backend pixi|conda|uv.")
    if (
        spec is not None
        and inferred_backend is None
        and backend is None
    ):
        raise _Refused(
            f"positional '{spec}' is not a typed env "
            f"spec (use conda:<env>, pixi:<proj>:<env>, or pixi:<name>) "
            f"and no --backend was provided."
        )
    if (
        spec is None
        and from_path is None
        and backend is None
    ):
        raise _Refused(
            "wfc register-env requires either a positional typed "
            "spec (conda:<env> / pixi:<...>), --from <path> --backend "
            "pixi|conda|uv, or --backend byo --image docker://..."
        )

    resolved_backend = inferred_backend or backend
    # The refusals above leave a typed spec or --backend to name it.
    assert resolved_backend is not None

    # --group / --extra choose what a uv project's export includes; no other
    # backend reads them.
    from .uv import UV_BACKEND

    if (groups or extras) and resolved_backend != UV_BACKEND:
        raise _Refused(
            "--group and --extra choose a uv project's dependency groups "
            f"and extras; they are valid with --backend {UV_BACKEND} only.")
    if resolved_backend == UV_BACKEND and from_path is None:
        raise _Refused(
            f"--backend {UV_BACKEND} requires --from <path>: a uv project "
            f"directory or its uv.lock.")
    if any(group.strip().lower().replace("_", "-") == "dev" for group in groups):
        raise _Refused(
            "--group dev cannot be installed: the uv export runs with "
            "--no-dev, which drops the dev group even when it is named. Move "
            "what the image needs into another dependency group and name "
            "that with --group.")

    # pixi/conda registration must name its source explicitly (file mode
    # or live-spec capture).
    from .backends import BACKENDS

    row = BACKENDS.get(resolved_backend)
    if (
        row is not None
        and row.builds_image
        and inferred_backend is None
        and from_path is None
    ):
        raise _Refused(
            f"--backend {resolved_backend} requires --from <path> "
            f"(file mode: a pixi.lock for pixi, an explicit-list for "
            f"conda) or a positional typed spec "
            f"(pixi:<proj>:<env> / conda:<env>) to capture from a live "
            f"env. Source files are never read implicitly from the "
            f"project root."
        )

    # ---- --interpreter / --python merge ----
    # Same recorded field (EnvRecord.python). Passing both is ambiguous —
    # reject up front, before any staging or docker work, so nothing is
    # recorded from a contradictory invocation.
    if interpreter_path is not None and python_path is not None:
        raise _Refused(
            "pass only one of --interpreter/--python (same option; "
            "--interpreter is the documented name)."
        )

    if dry_run and inferred_backend is not None:
        raise _Refused(
            "--dry-run does not support live-env capture "
            "(positional typed specs). Use "
            "--from <path> --backend pixi|conda|uv --dry-run."
        )

    return resolved_backend, inferred_backend is not None, interpreter_path or python_path


def _stage_source(
    *,
    live: bool,
    spec: str | None,
    backend: str,
    from_path: str | None,
    image: str | None,
    project_dir: Path,
    groups: Sequence[str] = (),
    extras: Sequence[str] = (),
) -> dict:
    """The staging by mode: shape the *source* payload :func:`wfc.environments.register` takes.

    Args:
        live: Whether *spec* names a live env to capture (mode 1).
        spec: The typed spec, for mode 1.
        backend: The resolved backend.
        from_path: ``--from``, for mode 2.
        image: ``--image``, for mode 3 (byo).
        project_dir: The wfc project root.
        groups: ``--group``, for a uv project.
        extras: ``--extra``, for a uv project.

    Returns:
        The source payload.

    Raises:
        _Refused: The capture or the file read failed, or byo has no image.
    """
    if live:
        # Mode 1: positional typed-spec → live-env capture.
        try:
            return _stage_live_env_source(
                live_spec=spec,
                project_dir=project_dir,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise _Refused(str(exc)) from exc
    from .uv import UV_BACKEND

    if from_path is not None and backend == UV_BACKEND:
        # Mode 2, a uv project: checked and exported on the host.
        return _stage_uv_project(Path(from_path).resolve(), groups, extras)
    if from_path is not None:
        # Mode 2: --from <path>.
        try:
            return _stage_from_path(
                backend=backend,
                from_path=Path(from_path).resolve(),
            )
        except (ValueError, FileNotFoundError) as exc:
            raise _Refused(str(exc)) from exc
    # Mode 3: byo image mode (pixi/conda without a spec or --from were
    # rejected by _check_register_args).
    if not image:
        raise _Refused("--backend byo requires --image docker://...")
    return {"image": image}


def _refuse_existing_name(*, name: str, force: bool, project_dir: Path) -> None:
    """Refuse a name ``.wfc/envs.json`` already records, before Docker is asked.

    :func:`wfc.environments.register` makes the same check as its own guard;
    making it here too lets the refusal reach a user whose host has no Docker.

    Args:
        name: The env name.
        force: ``--force``: replace an existing record.
        project_dir: The wfc project root.

    Raises:
        _Refused: *name* is recorded and ``force`` is ``False``, or the
            manifest cannot be read.
    """
    from .build import refuse_existing_name
    from .manifest import load_manifest_for_register

    try:
        refuse_existing_name(load_manifest_for_register(project_dir), name, force)
    except (FileExistsError, ValueError) as exc:
        raise _Refused(str(exc)) from exc


def _register_record(
    *,
    name: str,
    backend: str,
    source: dict,
    base_image: str | None,
    force: bool,
    project_dir: Path,
    python_override: str | None,
    recording_versions: Sequence[tuple[str, str]] = (),
    recording_read_error: str | None = None,
    push: bool = True,
):
    """The call to :func:`wfc.environments.register`.

    Args:
        name: The env name.
        backend: The resolved backend.
        source: The staged source payload.
        base_image: The base-image override, or ``None``.
        force: Replace an existing record.
        project_dir: The wfc project root.
        python_override: The recorded interpreter, or ``None``.
        recording_versions: The versions recording the env, with the image
            each recorded.
        recording_read_error: The caller's error reading the version
            lists, or ``None``.
        push: Push the image when the project has a registry base.

    Returns:
        The persisted :class:`wfc.environments.EnvRecord`.

    Raises:
        _Refused: ``register`` refused the env, its Docker work failed, or
            the image's captures folder could not be read.
    """
    from .. import environments as envs_mod
    from .captures import CaptureError

    try:
        return envs_mod.register(
            name=name,
            backend=backend,
            source=source,
            base_image=base_image,
            force=force,
            project_dir=project_dir,
            python_override=python_override,
            recording_versions=recording_versions,
            recording_read_error=recording_read_error,
            push=push,
        )
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError,
            CaptureError) as exc:
        raise _Refused(str(exc)) from exc


def _typed_spec_backend(spec: str) -> str | None:
    """Infer backend from a typed env spec, or ``None`` if not typed.

    Recognized prefixes:

    * ``conda:<env>``     -> ``"conda"``
    * ``pixi:<name>``     -> ``"pixi"``
    * ``pixi:<proj>:<env>`` -> ``"pixi"``

    Bare names (``"image-io"``, ``"my_env"``, ``"conda"``, ``"pixi"``,
    ``"byo"``) all have NO colon and return ``None`` —
    those are positional env-name slots or backend names, not typed
    specs.
    """
    from .backends import BACKENDS

    if ":" not in spec:
        return None
    head = spec.split(":", 1)[0]
    row = BACKENDS.get(head)
    return head if row is not None and row.live_spec else None


@task(purpose="Capture a live conda or pixi env's lock, explicit list and pip "
              "freeze into the source payload register-env builds from; a "
              "conda env on a host that is not linux-64 is captured as a "
              "pinned spec to solve for linux-64, with the host's capture kept")
def _stage_live_env_source(live_spec: str, project_dir: Path) -> dict:
    """Capture introspection blobs from the live env named by *live_spec*.

    Returns a *source* payload dict shaped for :func:`wfc.environments.register`
    so the resolved env's pixi.lock / pixi.toml / conda explicit-list /
    pip-freeze land in the build context.

    A conda env whose host is not linux-64 (Windows, macOS), or whose
    explicit list names another platform, cannot install its own list into
    the image. Its payload is instead the solve payload of
    :func:`wfc.environments.introspect.solve_capture_source`: each requested
    package pinned to its installed version, the ``pypi``-channel pip set,
    and the host's own capture for the captures folder's ``host/``.

    Raises:
        ValueError: If the env spec is unknown, the env cannot be
            resolved, or the captured-content read fails.
        FileNotFoundError: If a required source file (pixi.lock,
            pixi.toml) is missing from the resolved env's project dir.
    """
    from datetime import UTC, datetime

    from ..persistence import read_config
    from .dockerfiles.conda import explicit_list_foreign_platforms
    from .host import (
        IMAGE_CONDA_PLATFORM,
        _find_python_in_env,
        host_conda_platform,
        resolve_conda_env_dir,
        resolve_python_for_env,
    )
    from .introspect import (
        PIP_MISSING_SENTINEL,
        conda_env_export_from_history,
        conda_list_explicit,
        conda_list_json,
        conda_version,
        pip_freeze_best_effort,
        solve_capture_source,
    )

    config = read_config(project_dir)
    pixi_root = config.get("pixi_root") or None
    conda_root = config.get("conda_root") or None

    source: dict = {}
    head = live_spec.split(":", 1)[0]

    if head == "conda":
        # Resolve the env PREFIX directly (not via its python binary): a
        # python-free conda env (e.g. conda-forge r-base only) has no
        # interpreter to locate, but its explicit list still captures fine.
        env_name = live_spec.split(":", 1)[1]
        env_prefix = resolve_conda_env_dir(env_name, conda_root)
        explicit_list = conda_list_explicit(env_prefix)
        try:
            env_python = _find_python_in_env(env_prefix)
        except ValueError:
            # No python in the env — nothing pip could have installed.
            # Record the sentinel; the conda Dockerfile generator omits the
            # pip layers for it.
            pip_freeze = PIP_MISSING_SENTINEL
        else:
            pip_freeze = pip_freeze_best_effort(env_python)

        host_platform = host_conda_platform()
        if (host_platform == IMAGE_CONDA_PLATFORM
                and not explicit_list_foreign_platforms(explicit_list)):
            source["explicit_list_content"] = explicit_list
            source["pip_freeze_content"] = pip_freeze
            return source
        # The env's own packages cannot install into the linux-64 image:
        # solve for linux-64 from what the user asked for, pinned to the
        # versions installed here, and keep the host's capture for host/.
        return solve_capture_source(
            explicit_list=explicit_list,
            from_history=conda_env_export_from_history(env_prefix),
            installed=conda_list_json(env_prefix),
            pip_freeze=pip_freeze,
            host_platform=host_platform,
            conda_version=conda_version(),
            captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    env_python = resolve_python_for_env(
        live_spec,
        pixi_root=pixi_root,
        project_dir=project_dir,
    )

    if head == "pixi":
        # env_python is at <pixi-project>/envs/<env>/bin/python (posix)
        # or <pixi-project>/envs/<env>/python.exe (windows). Walk up to
        # the pixi-project directory and read pixi.lock + pixi.toml.
        env_dir = (
            env_python.parent.parent
            if env_python.name == "python"
            else env_python.parent
        )
        pixi_project_dir = env_dir.parent.parent
        lock = pixi_project_dir / "pixi.lock"
        toml = pixi_project_dir / "pixi.toml"
        if not lock.exists():
            raise FileNotFoundError(
                f"pixi.lock not found next to env at {pixi_project_dir} — "
                f"run `pixi install` to regenerate it."
            )
        source["pixi_lock_content"] = lock.read_text(encoding="utf-8")
        if toml.exists():
            source["pixi_toml_content"] = toml.read_text(encoding="utf-8")
    else:
        raise ValueError(
            f"Live-env capture not supported for spec {live_spec!r}."
        )

    source["pip_freeze_content"] = pip_freeze_best_effort(env_python)
    return source


def _stage_uv_project(from_path: Path, groups: Sequence[str],
                      extras: Sequence[str]) -> dict:
    """Check a uv project and export its requirements; print its warnings.

    Args:
        from_path: ``--from``: the project directory or its ``uv.lock``.
        groups: ``--group`` values.
        extras: ``--extra`` values.

    Returns:
        The source payload (see :func:`wfc.environments.uv.capture_project`).

    Raises:
        _Refused: The project cannot be built, or uv failed.
    """
    from .uv import capture_project

    try:
        capture = capture_project(from_path, groups=groups, extras=extras)
    except (ValueError, OSError, RuntimeError) as exc:
        raise _Refused(str(exc)) from exc
    for warning in capture.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return capture.source


def _stage_from_path(backend: str, from_path: Path) -> dict:
    """Read a user-supplied source file and shape it for ``wfc.environments.register``.

    For pixi, an adjacent ``pixi.toml`` next to the lock is also copied
    when present.

    Raises:
        FileNotFoundError: If *from_path* does not exist.
        ValueError: If *backend* is not pixi or conda.
    """
    if not from_path.exists():
        raise FileNotFoundError(f"--from path does not exist: {from_path}")

    source: dict = {}
    if backend == "conda":
        source["explicit_list_content"] = from_path.read_text(encoding="utf-8")
    elif backend == "pixi":
        source["pixi_lock_content"] = from_path.read_text(encoding="utf-8")
        sibling_toml = from_path.parent / "pixi.toml"
        if sibling_toml.exists():
            source["pixi_toml_content"] = sibling_toml.read_text(encoding="utf-8")
    else:
        raise ValueError(
            f"--from is only valid for --backend pixi, conda or uv, not {backend!r}."
        )
    # pip freeze is empty in file mode — no live env to introspect.
    source["pip_freeze_content"] = ""
    return source


def _register_env_dry_run(
    *,
    name: str,
    backend: str,
    base_image: str | None,
    image: str | None,
    project_dir: Path,
    source: dict | None = None,
) -> int:
    """``wfc register-env <name> --from <path> --backend X --dry-run``.

    Render the Dockerfile and exit before any docker subprocess fires.
    Manifest is NOT mutated, and nothing is staged — the generators are
    pure string functions; the lock/list content is only read by docker
    at build time.

    Args:
        name: The env name.
        backend: A backend the table names.
        base_image: The base-image override, or ``None``.
        image: The upstream image, for byo.
        project_dir: The wfc project root.
        source: The source payload the backend row's generator arguments
            are read from (a uv project's Python minor); empty otherwise.

    Returns:
        0 when the Dockerfile was written (its path is printed) or byo has
        none; 1 when the generator refuses.
    """
    from . import dockerfiles as df_pkg

    gen_kwargs: dict = {"env_name": name}
    if base_image is not None:
        gen_kwargs["base_image"] = base_image

    from .backends import BACKENDS

    row = BACKENDS.get(backend)
    if row is not None and row.builds_image:
        gen_kwargs["pip_freeze_content"] = ""
        if row.generator_args is not None:
            gen_kwargs.update(row.generator_args(source or {}))
    elif row is not None:
        gen_kwargs["image"] = image

    try:
        dockerfile = df_pkg.generate_for_backend(backend, **gen_kwargs)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if dockerfile is None:
        # BYO: no Dockerfile to write. Notice + exit 0 under --dry-run.
        print(
            f"No Dockerfile for BYO env {name!r} — the upstream "
            f"image is used as-is; digest resolution runs without "
            f"--dry-run."
        )
        return 0

    build_dir = layout.env_build_dir(project_dir, name)
    build_dir.mkdir(parents=True, exist_ok=True)
    dockerfile_path = build_dir / "Dockerfile"
    dockerfile_path.write_text(dockerfile, encoding="utf-8")
    print(str(dockerfile_path.resolve()))
    return 0
