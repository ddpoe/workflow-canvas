"""The env verbs: ``wfc list-envs``, ``show-env``, ``delete-env`` and ``register-env``.

Each body takes plain values, prints what the verb prints and returns its
exit code; ``wfc/cli.py`` parses the arguments and calls it.

``register-env`` runs in four parts: the argument checks
(:func:`_check_register_args`), the existing-name refusal and the caller's
Docker gate, the staging by mode (:func:`_stage_source`), and the call to
:func:`wfc.environments.register` (:func:`_register_record`).  Like
``delete-env``'s references, the Docker probe arrives as a value (a callable),
so this package never imports Execution.

``delete-env`` takes the methods that reference the env as a value.  The
caller reads them from the registry, so this package never imports
Registration.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from axiom_annotations import task

from .. import layout
from ..persistence import project_root as get_project_root


class _Refused(Exception):
    """A ``register-env`` refusal: the verb prints ``ERROR: <message>`` and exits 1."""


# =============================================================================
# list-envs, show-env, delete-env
# =============================================================================


def list_envs() -> int:
    """``wfc list-envs``: print every env in ``.wfc/envs.json`` as a table.

    Returns:
        0 on success; 1 when no project is found or the manifest is unreadable.
    """
    from .. import environments as envs_mod

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
    # Fixed-width table: name | backend | container | built_at
    headers = ("NAME", "BACKEND", "CONTAINER", "BUILT AT")
    rows = [
        (name, r.backend, r.container, r.built_at or "")
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


def show_env(name: str) -> int:
    """``wfc show-env <name>``: print the full record as key/value lines.

    Args:
        name: The env to show.

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
    width = max(len(k) for k in keys)
    for key in keys:
        value: object
        if key == "name":
            value = name
        else:
            value = data.get(key)
        print(f"{key:<{width}} : {value if value is not None else ''}")
    return 0


def delete_env(name: str, *, references: list[str], force: bool = False) -> int:
    """``wfc delete-env <name>``: remove an env, warning when methods reference it.

    Methods are not deleted with the env; the user is
    responsible for retargeting them.  The verb warns and prompts; it never
    refuses because of a reference.

    Args:
        name: The env to remove.
        references: ``module/method`` for every method whose env names
            *name*, read by the caller from the registry.  An empty list
            prints no warning.
        force: Skip the confirmation prompt.

    Returns:
        0 when the env was deleted; 1 when no project is found, the manifest
        is unreadable, no env has that name, or the user aborts.
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

    if references:
        print(f"WARNING: {len(references)} method(s) reference env {name!r}:")
        for ref in references:
            print(f"  - {ref}")
        print("  Method rows will NOT be auto-deleted. Re-register them "
              "with a different env or they will fail at run time.")

    if not force:
        try:
            answer = input(f"Delete env {name!r}? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 1

    envs_mod.delete(name, project_dir)
    print(f"Deleted env {name!r} from .wfc/envs.json. "
          f"Registry tag (if any) was NOT removed.")
    return 0


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

    3. **BYO image mode** (``--backend byo --image docker://...``). No
       Dockerfile is generated; the upstream image is digest-resolved and
       recorded as-is.

    For pixi/conda, a typed spec or ``--from`` is required;
    ``--backend pixi|conda`` alone is refused.

    Capturing from a live env records the env's current state, including
    any ad-hoc ``pip install`` mutations on top of the conda/pixi env.
    Inspect the captured package list (md5 = ``source_fingerprint``) via
    ``GET /api/registry/envs/blob/<md5>`` before relying on the image
    for downstream runs.

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

    The checks run cheapest first, so each refusal the user can fix on the
    command line is reported even on a host with no Docker: the argument
    checks, then (after ``--dry-run``'s early return, which never touches
    Docker) the existing-name refusal, then ``docker_gate``, then staging
    and the build.

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
        return _register_env_dry_run(
            name=name,
            backend=resolved_backend,
            base_image=base_image,
            image=image,
            project_dir=project_dir,
        )

    # ---- Existing name, then Docker: both before any staging ----
    try:
        _refuse_existing_name(name=name, force=force, project_dir=project_dir)
    except _Refused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    docker_message = docker_gate()
    if docker_message is not None:
        print(docker_message, file=sys.stderr)
        return 1

    # ---- Full build path ----
    try:
        source = _stage_source(
            live=live,
            spec=spec,
            backend=resolved_backend,
            from_path=from_path,
            image=image,
            project_dir=project_dir,
        )
        record = _register_record(
            name=name,
            backend=resolved_backend,
            source=source,
            base_image=base_image,
            force=force,
            project_dir=project_dir,
            python_override=interpreter,
        )
    except _Refused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(record.container)
    return 0


def _check_register_args(
    *,
    spec: str | None,
    backend: str | None,
    from_path: str | None,
    dry_run: bool,
    interpreter_path: str | None,
    python_path: str | None,
) -> tuple[str, bool, str | None]:
    """The argument checks of ``register-env``, run before any staging or Docker work.

    Args:
        spec: The positional spec, or ``None``.
        backend: ``--backend``, or ``None``.
        from_path: ``--from``, or ``None``.
        dry_run: Whether ``--dry-run`` was passed.
        interpreter_path: ``--interpreter``, or ``None``.
        python_path: ``--python``, or ``None``.

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
        raise _Refused("--from <path> requires --backend pixi|conda.")
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
            "pixi|conda, or --backend byo --image docker://..."
        )

    resolved_backend = inferred_backend or backend
    # The refusals above leave a typed spec or --backend to name it.
    assert resolved_backend is not None

    # pixi/conda registration must name its source explicitly (file mode
    # or live-spec capture).
    if (
        resolved_backend in ("pixi", "conda")
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
            "--from <path> --backend pixi|conda --dry-run."
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
) -> dict:
    """The staging by mode: shape the *source* payload :func:`wfc.environments.register` takes.

    Args:
        live: Whether *spec* names a live env to capture (mode 1).
        spec: The typed spec, for mode 1.
        backend: The resolved backend.
        from_path: ``--from``, for mode 2.
        image: ``--image``, for mode 3 (byo).
        project_dir: The wfc project root.

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
    from .manifest import load_manifest

    try:
        refuse_existing_name(load_manifest(project_dir), name, force)
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

    Returns:
        The persisted :class:`wfc.environments.EnvRecord`.

    Raises:
        _Refused: ``register`` refused the env or its Docker work failed.
    """
    from .. import environments as envs_mod

    try:
        return envs_mod.register(
            name=name,
            backend=backend,
            source=source,
            base_image=base_image,
            force=force,
            project_dir=project_dir,
            python_override=python_override,
        )
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError) as exc:
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
    if ":" not in spec:
        return None
    head = spec.split(":", 1)[0]
    if head == "conda":
        return "conda"
    if head == "pixi":
        return "pixi"
    return None


@task(purpose="Capture a live conda or pixi env's lock, explicit list and pip "
              "freeze into the source payload register-env builds from")
def _stage_live_env_source(live_spec: str, project_dir: Path) -> dict:
    """Capture introspection blobs from the live env named by *live_spec*.

    Returns a *source* payload dict shaped for :func:`wfc.environments.register`
    so the resolved env's pixi.lock / pixi.toml / conda explicit-list /
    pip-freeze land in the build context.

    Raises:
        ValueError: If the env spec is unknown, the env cannot be
            resolved, or the captured-content read fails.
        FileNotFoundError: If a required source file (pixi.lock,
            pixi.toml) is missing from the resolved env's project dir.
    """
    from ..persistence import read_config
    from .host import (
        _find_python_in_env,
        resolve_conda_env_dir,
        resolve_python_for_env,
    )
    from .introspect import (
        PIP_MISSING_SENTINEL,
        conda_list_explicit,
        pip_freeze_best_effort,
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
        source["explicit_list_content"] = conda_list_explicit(env_prefix)
        try:
            env_python = _find_python_in_env(env_prefix)
        except ValueError:
            # No python in the env — nothing pip could have installed.
            # Record the sentinel; the conda Dockerfile generator omits the
            # pip layers for it.
            source["pip_freeze_content"] = PIP_MISSING_SENTINEL
            return source
        source["pip_freeze_content"] = pip_freeze_best_effort(env_python)
        return source

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
            f"--from is only valid for --backend pixi or conda, not {backend!r}."
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
) -> int:
    """``wfc register-env <name> --from <path> --backend X --dry-run``.

    Render the Dockerfile and exit before any docker subprocess fires.
    Manifest is NOT mutated, and nothing is staged — the generators are
    pure string functions; the lock/list content is only read by docker
    at build time.

    Args:
        name: The env name.
        backend: ``pixi``, ``conda`` or ``byo``.
        base_image: The base-image override, or ``None``.
        image: The upstream image, for byo.
        project_dir: The wfc project root.

    Returns:
        0 when the Dockerfile was written (its path is printed) or byo has
        none; 1 when the generator refuses.
    """
    from . import dockerfiles as df_pkg

    gen_kwargs: dict = {"env_name": name}
    if base_image is not None:
        gen_kwargs["base_image"] = base_image

    if backend == "pixi":
        gen_kwargs["pip_freeze_content"] = ""
    elif backend == "conda":
        gen_kwargs["pip_freeze_content"] = ""
    elif backend == "byo":
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
