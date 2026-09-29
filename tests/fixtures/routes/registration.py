"""Registration routes: a method, a sample and an env record through production.

Pins / does not prove lines are on each builder's docstring.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from .roots import init_test_project, sample_source_dir


def register_test_method(
    project_dir: Path,
    *,
    module_name: str,
    method_dir: Path,
    method_name: str | None = None,
    module_contracts: list[dict] | None = None,
    module_description: str | None = None,
    allow_reserved: bool = False,
) -> None:
    """Register a method into a tmp wfc project using production registration APIs.

    Runs the same code path a real ``wfc register`` CLI invocation does:
    ``wfc.init.init_project`` (idempotent), ``wfc.persistence.reset_engine``,
    ``wfc.registration.register_module``, ``wfc.registration.register_method``. No DB
    hand-crafting, no stub registration.

    The helper temporarily ``chdir``\\ s into ``project_dir`` for the
    registration call because ``register_method`` and ``_git_commit_registration``
    use ``Path.cwd()`` to resolve relative script paths and the git repo root.
    Original cwd is restored on return.

    Caller responsibility: ``WFC_PROJECT_ROOT`` and ``DATABASE_URL`` must be set
    in ``os.environ`` BEFORE invoking (typically via ``monkeypatch.setenv`` in
    the calling test). The project directory must be a git repo (run ``git init``
    plus user.email/user.name config before calling).

    Pins: cwd chdir'd into the project for the registration call; init
    re-run idempotently first; the module row registered (upserted) with
    the given contracts.
    Does not prove: that the method's env image exists -- registration
    checks the container ref's shape only; ``wfc register``'s argument
    parsing.

    Args:
        project_dir: Project root directory. Will be initialized (idempotent)
            if not already.
        module_name: Module name to register (or upsert) into the database.
        method_dir: Directory containing the method's ``{method_name}.py`` and
            ``method.yaml``. Must already exist with the method source files.
        method_name: Method name (defaults to ``method_dir.name``).
        module_contracts: Optional list of module contract dicts (passed to
            :func:`wfc.registration.register_module`). Defaults to empty list.
        module_description: Optional module description, passed through to
            ``register_module``; ``None`` leaves an existing row's
            description alone.
        allow_reserved: Opt in to the reserved ``__demo__`` prefix for the
            module and method names, as ``wfc demo`` does for its own
            registrations. Defaults to the refusal every other caller gets.
    """
    from wfc.registration import register_module, register_method
    from wfc.persistence import reset_engine

    project_dir = Path(project_dir).resolve()
    method_dir = Path(method_dir).resolve()

    # init_project is idempotent on a project_dir that already has .wfc/ —
    # the existing scaffold is left in place; only missing pieces are filled.
    init_test_project(project_dir)
    reset_engine()

    register_module(
        name=module_name,
        contracts=module_contracts if module_contracts is not None else [],
        description=module_description,
        allow_reserved=allow_reserved,
    )

    # register_method uses Path.cwd() to compute the relative script_path and
    # to resolve the git repo root for the commit step. chdir for the duration
    # of the call so the caller doesn't have to manage it.
    prev_cwd = os.getcwd()
    try:
        os.chdir(project_dir)
        register_method(
            method_dir=method_dir,
            module_name=module_name,
            method_name=method_name,
            allow_reserved=allow_reserved,
        )
    finally:
        os.chdir(prev_cwd)


def register_sample_row(project_dir: Path, sample_name: str, data_file: Path) -> None:
    """Register a sample through production ``register_sample``.

    A step's cache key carries the content of the sample it reads, so the
    claim refuses a sample name that has no registered row. A fixture that
    only stages a file under ``data/samples/`` therefore models a project a
    user cannot have: data ``wfc`` never registered.

    Registration caches the source's bytes and writes the row; it copies
    nothing into ``data/samples/``. ``registered_path`` records where the
    ``restore_sample`` rule will materialize the file later. ``data_file``
    must therefore be the user's own copy, somewhere the project's ``data/``
    is not — register a file that already sits at its own
    ``registered_path`` and every restore takes ``restore_from_cache``'s
    dest-already-valid skip, so the real-copy path is never exercised.
    :func:`sample_source_dir` is where a fixture stages one.

    Idempotent by name: registration refuses a duplicate, and a fixture
    that re-stages a sample is re-staging the same declared content.

    Pins: the source staged outside the project's ``data/`` tree; a second
    registration of the same name is skipped rather than refused.
    Does not prove: that the bytes reach ``data/samples/`` -- only a run's
    ``restore_sample`` rule puts them there.

    Args:
        project_dir: Project root directory.
        sample_name: Sample identifier.
        data_file: The source file to register. Must be outside the
            project's ``data/`` tree.

    Raises:
        AssertionError: If ``data_file`` is inside the project's ``data/``
            tree.
    """
    from sqlmodel import select

    from wfc.persistence import get_session, Sample
    from wfc.registration import register_sample

    project_dir = Path(project_dir).resolve()
    data_file = Path(data_file).resolve()
    data_root = project_dir / "data"
    if data_root == data_file or data_root in data_file.parents:
        raise AssertionError(
            f"register_sample_row source {data_file} is inside {data_root}. "
            "Registration materializes nothing under data/samples/ — a source "
            "staged there makes registered_path == source_path and every "
            "restore the dest-already-valid skip. Stage it under "
            "sample_source_dir(project_dir) and let restore_sample be the only "
            "writer under data/samples/."
        )

    with get_session() as session:
        already = session.exec(
            select(Sample).where(Sample.name == sample_name)
        ).first()
    if already is not None:
        return

    register_sample(
        name=sample_name,
        source_path=data_file,
        project_root=project_dir,
    )


def create_sample_csv(project_dir: Path, sample_name: str, num_rows: int = 3) -> Path:
    """Write a sample CSV outside the project and register it.

    Staging the file and recording the ``samples`` row are one act here:
    every caller runs a real pipeline or step over the sample, and a
    pipeline's claim refuses an unregistered sample name.

    The CSV is the user's own file, so it is written under
    :func:`sample_source_dir` — beside the project, not inside it. Nothing
    appears under ``data/samples/{sample_name}/`` until a run's
    ``restore_sample`` rule puts it there, which is the only way a user's
    project gets it.

    Pins: the content (an ``id,value`` header plus ``num_rows`` rows) and
    the source location under :func:`sample_source_dir`.
    Does not prove: the same as :func:`register_sample_row` -- nothing is
    materialized under ``data/samples/``.

    Args:
        project_dir: Project root directory.
        sample_name: Sample identifier.
        num_rows: Number of data rows to generate.

    Returns:
        Path to the created CSV file — the registration *source*, outside the
        project tree. It is NOT the sample's ``registered_path``.
    """
    source_dir = sample_source_dir(project_dir) / sample_name
    source_dir.mkdir(parents=True, exist_ok=True)
    csv_path = source_dir / "data.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "value"])
        for i in range(num_rows):
            writer.writerow([i, i * 10])
    register_sample_row(project_dir, sample_name, csv_path)
    return csv_path


def write_env_record(
    project_dir: Path,
    name: str,
    *,
    backend: str = "byo",
    digest: str = "a" * 64,
    image: str | None = None,
    python: str | None = None,
    legacy_no_python: bool = False,
    lock_content: str | None = None,
    pip_freeze_content: str = "",
    built_at: str = "2026-06-23T00:00:00Z",
) -> dict:
    """Write an env record into ``.wfc/envs.json`` via production serialization.

    Test-fixture counterpart of the manifest-write tail of
    :func:`wfc.environments.register`: the record is built through ``EnvRecord`` /
    ``to_dict`` and written with ``save_manifest``, the container ref passes
    ``validate_container_ref``, and ``env_fingerprint`` / ``source_fingerprint``
    are computed through the production precompute path (canonical blob ->
    ``store_env_content`` -> 32-char md5, blob landing in the project's DVC
    cache). Any drift between what registration writes and what a fixture
    stages therefore fails here, at fixture time.

    Pins: the image digest (a placeholder unless the caller passes a built
    image's), ``built_at`` and the ``local/<name>`` repo; the record is
    written without a build or a pull.
    Does not prove: that the env image exists; registration and dispatch
    check the container ref's shape only.

    Args:
        project_dir: Project root. ``.wfc/`` is created when missing.
        name: Env name (the key in ``envs.json::envs``).
        backend: ``"byo"`` (default), ``"pixi"``, or ``"conda"``.
        digest: Bare 64-hex image digest for the container ref.
        image: Image repo part of the ref. Defaults to ``local/<name>`` — the
            exact repo production's local-build path records.
        python: Container-side interpreter override. Defaults to the
            production per-backend default (``"python"`` for byo).
        legacy_no_python: When True, the ``python`` key is removed entirely —
            the shape of records written before the field existed, which
            dispatch must resolve via the per-backend default.
        lock_content: Pixi-lock / conda explicit-list content. When given for
            a pixi/conda backend, ``source_fingerprint`` is computed and its
            blob cached exactly as registration does.
        pip_freeze_content: Pip-freeze section of the source blob.
        built_at: Timestamp value (synthetic fixture data).

    Returns:
        The record dict as written into the manifest.
    """
    from wfc.environments import (
        MANIFEST_SCHEMA_VERSION,
        EnvRecord,
        default_python_for_backend,
        load_manifest,
        save_manifest,
    )
    from wfc.contracts import validate_container_ref
    from wfc.storage import store_env_content
    from wfc.environments.fingerprint import capture_env_content
    project_dir = Path(project_dir)
    (project_dir / ".wfc").mkdir(parents=True, exist_ok=True)

    if image is None:
        image = f"local/{name}"
    container_ref = f"docker://{image}@sha256:{digest}"
    # Production self-validation: register() refuses to write any other shape.
    validate_container_ref(container_ref)

    # env_fingerprint through the production precompute path (canonical
    # container blob -> md5 + DVC cache blob). 32-char md5 — NOT the 64-hex
    # image digest.
    blob = capture_env_content(f"container:{image}@sha256:{digest}")
    env_fingerprint = store_env_content(blob, project_dir)

    # source_fingerprint through the production package-list formula.
    source_fingerprint = None
    if lock_content is not None and backend in ("pixi", "conda"):
        from wfc.environments.packages import PIP_FREEZE_DELIMITER
        sf_blob = f"{lock_content}{PIP_FREEZE_DELIMITER}{pip_freeze_content}"
        source_fingerprint = store_env_content(sf_blob, project_dir)

    if backend == "pixi":
        source, built_from_lock = "pixi.toml", "pixi.lock"
    elif backend == "conda":
        source, built_from_lock = "environment.yml", "conda-lock.yml"
    else:
        # byo: production records the user-supplied ref as the source field.
        source, built_from_lock = container_ref, None

    record = EnvRecord(
        backend=backend,
        source=source,
        container=container_ref,
        env_fingerprint=env_fingerprint,
        built_at=built_at,
        built_from_lock=built_from_lock,
        source_fingerprint=source_fingerprint,
        python=python or default_python_for_backend(backend, name),
    )
    record_dict = record.to_dict()
    if legacy_no_python:
        del record_dict["python"]

    manifest = load_manifest(project_dir)
    manifest["envs"][name] = record_dict
    manifest.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
    save_manifest(project_dir, manifest)
    return record_dict
