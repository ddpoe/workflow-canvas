"""Build a container env and record it: the ``register`` workflow.

``wfc register-env`` and the demo scaffold call
:func:`register`. It renders the backend's Dockerfile and builds the image,
or resolves a byo image's digest, then precomputes ``env_fingerprint`` and
writes the env's record to ``.wfc/envs.json``. Each phase is a step function:
:func:`_validate_request`, then :func:`_build_image` for pixi, conda and uv or
:func:`_resolve_byo_image` for byo, then :func:`_write_record`, then
:func:`_push_image`, which pushes the image to the project's registry when
one is set, then :func:`_commit_manifest`, which commits ``.wfc/envs.json``, the image's
captures folder and ``.gitattributes`` by pathspec, and puts them back as
they were when git refuses the commit.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path
from typing import NamedTuple

from axiom_annotations import AutoStep, Step, task, workflow

from .. import layout
from ..contracts import parse_byo_ref, validate_container_ref
from .argv import _LOCAL_REPOSITORY_PREFIX, strip_docker_scheme
from .backends import BACKENDS, STAGED_PIP_FREEZE, backend_row
from .captures import check_capture_committable
from .interpreter import default_python_for_backend
from .introspect import HOST_CAPTURE_SOURCE_KEY, SOLVE_SOURCE_KEY
from .manifest import (
    EnvRecord,
    load_manifest_for_register,
    put_record,
    save_manifest,
)
from .uv import EXTRAS_SOURCE_KEY as UV_EXTRAS_SOURCE_KEY
from .uv import GROUPS_SOURCE_KEY as UV_GROUPS_SOURCE_KEY
from .uv import PYTHON_MINOR_SOURCE_KEY as UV_PYTHON_MINOR_SOURCE_KEY


class _ResolvedImage(NamedTuple):
    """A digest-pinned image, with the record fields its backend decides.

    Attributes:
        container_ref: The ``docker://<host>/<path>@sha256:<hex>`` ref that is
            recorded as the env's ``container`` field.
        digest_hex: The image digest's hex, without the ``sha256:`` prefix.
        source_field: The value recorded as the env's ``source`` field.
        built_from_lock: The value recorded as ``built_from_lock``; ``None``
            for byo.
        explicit_list: For a conda env solved for linux-64, the image's own
            explicit list, read back after the build; ``None`` otherwise.
        solved_for: The platform such an env was solved for; ``None``
            otherwise.
    """

    container_ref: str
    digest_hex: str
    source_field: str | None
    built_from_lock: str | None
    explicit_list: str | None = None
    solved_for: str | None = None


#: The ``source`` fields that are staged as text into the build context:
#: every backend's lock and extra files (the backend table's staged files),
#: and the pip freeze.
_STAGED_TEXT_FIELDS = frozenset(
    staged.source_key
    for row in BACKENDS.values()
    for staged in (row.staged_lock, *row.staged_extras)
    if staged is not None
) | {STAGED_PIP_FREEZE.source_key}


def _lf_newlines(text: str) -> str:
    """Return *text* with every CRLF line ending turned into LF."""
    return text.replace("\r\n", "\n")


@workflow(purpose="Register a container env: build the image, resolve its digest, "
                  "write the manifest entry, push the image to the project's "
                  "registry, and commit")
def register(
    name: str,
    backend: str,
    source: dict,
    base_image: str | None = None,
    force: bool = False,
    project_dir: Path | None = None,
    *,
    allow_reserved: bool = False,
    python_override: str | None = None,
    recording_versions: Sequence[tuple[str, str]] = (),
    recording_read_error: str | None = None,
    push: bool = True,
) -> EnvRecord:
    """Register a container env.

    Builds the image, resolves its digest, and writes the manifest entry.
    Orchestration entry point for ``wfc register-env``. Dispatch by backend:

      - **pixi / conda / uv:** assemble per-backend generator kwargs
        from *source*, render the Dockerfile via
        :func:`wfc.environments.dockerfiles.generate_for_backend`, write it to
        ``.wfc/build/<name>/Dockerfile``, stage any source-content blobs
        from ``source`` into the build context under the generator's
        expected filenames, run ``docker build`` via :mod:`wfc.environments.docker`,
        resolve the digest, and store a local-only ref
        ``docker://local/<name>@sha256:<hex>`` as the manifest's
        ``container`` field.

      - **byo:** validate the user-supplied ``docker://`` ref, pull if not
        already local, resolve the digest, and store
        ``docker://<original-prefix>@sha256:<hex>``.

    The phases are step functions, run in this order: validate
    (:func:`_validate_request`); refuse a captures path git would ignore,
    naming the rule, before any build
    (:func:`~wfc.environments.captures.check_capture_committable`); for
    pixi, conda and uv, build (:func:`_build_image`); for byo, resolve by
    probe-then-pull (:func:`_resolve_byo_image`); record
    (:func:`_write_record`); push (:func:`_push_image`); commit
    (:func:`_commit_manifest`).

    The push step runs only when the project's ``[registry] url`` is set
    and *push* is true. It tags the image (built, or a byo ``local/``
    image) ``<base>/<name>:fp-<first 12 hex of env_fingerprint>``, pushes
    it, and writes the digest the registry reported into the image's
    captures folder as ``pushed.json``, committed with the record. A byo
    image already in a registry is never copied, and an image whose
    folder already holds ``pushed.json`` is not pushed again. A failed
    push undoes the record step as a refused commit does and raises
    naming the registry and ``--no-push``. With no base, or with *push*
    false, nothing is pushed and the output says the image stays on this
    machine. A project whose git remote is on GitHub builds its images
    with the repository's source label
    (:func:`wfc.environments.registry.source_labels`), which moves no
    fingerprint.

    The commit is one pathspec commit of ``.wfc/envs.json``, the new
    captures folder and ``.gitattributes`` (its ``.wfc/captures/** -text``
    line is added when missing). If git refuses it, the new folder is
    removed (or a ``pushed.json`` the push step added to an existing one)
    and ``envs.json`` and ``.gitattributes`` get their prior bytes back, so
    a refused registration leaves nothing behind. Outside a git repository
    the folder and record are written and nothing is committed.

    The env fingerprint (``env_fingerprint``) is precomputed at
    registration time from the image's content: the record step inspects
    the image Docker starts the env from
    (:func:`wfc.environments.docker.image_content`), renders its layers,
    ``Env``, ``User`` and ``WorkingDir`` with
    :func:`wfc.environments.fingerprint.capture_env_content`, and stores the
    blob with :func:`wfc.storage.store_env_content`. The image's name, tag,
    registry and labels do not move it.

    Every backend gets a captures folder,
    ``.wfc/captures/<name>/<env_fingerprint>/``
    (:mod:`wfc.environments.captures`), committed with the record: its
    ``image.json``, and for pixi, conda and uv the rendered Dockerfile, the
    staged extras (``pixi.toml``) and ``packages.lock``, the package list
    assembled from *source* (the FULL lock / explicit-list content, the
    pip-freeze delimiter, the pip freeze). The md5 of ``packages.lock`` is
    the record's ``source_fingerprint``; the package list is not stored in
    the DVC cache. It is separate from ``env_fingerprint`` (the image's
    content fingerprint) and does not affect it.

    Registering an image whose folder is already there keeps the folder as
    it is when the interpreter is the one its ``image.json`` records. With
    another interpreter, only ``image.json``'s interpreter is rewritten,
    and committed (or restored on a refused commit) with ``envs.json``,
    while no method version records the image; once one does (or when the
    method version lists cannot be read), the image's interpreter is fixed
    and the registration is refused before anything is written, naming
    those versions, ``--interpreter <recorded>`` to keep the image's
    interpreter, the new-env-name route, and, when no ``--interpreter`` was
    given, that the requested interpreter is only the backend's default. A
    folder changed outside wfc (``image.json`` missing or unreadable, or
    ``packages.lock`` missing), or one whose ``packages.lock`` is not the
    package list this registration produced (the folder is never
    rewritten, so the record would name a list the folder lacks), is
    refused the same way, whatever the interpreter, naming
    :func:`~wfc.environments.captures.capture_remedy`: register the env
    again under a new name.

    Args:
        name: Env name (KEY in ``.wfc/envs.json::envs``; not stored inside the
            record).
        backend: One of ``"pixi"``, ``"conda"``, ``"uv"``, ``"byo"``.
        source: Per-backend payload dict. The text fields that are staged
            (lock, explicit list, pip freeze, ``pixi.toml``) have their line
            endings turned to LF before staging and fingerprinting.
            Supported keys (all optional):
            * ``"pip_freeze_content"``: pip freeze output, fed to
              the pixi/conda/uv Dockerfile generators and staged as
              ``pip-freeze.txt`` in the build context.
            * ``"explicit_list_content"``: conda explicit-list contents
              (output of ``conda list --explicit --md5``). Staged as
              ``explicit-list.txt``. Conda backend only.
            * ``"pixi_lock_content"``: contents of a ``pixi.lock`` file.
              Staged as ``pixi.lock``. Pixi backend only.
            * ``"pixi_toml_content"``: contents of a ``pixi.toml`` file.
              Staged as ``pixi.toml``. Pixi backend only.
            * ``"requirements_content"``, ``"uv_python"``, ``"uv_groups"``,
              ``"uv_extras"``: a uv project's exported requirements (staged
              as ``requirements.txt``), its Python minor (picks the base)
              and the groups and extras recorded in ``image.json``. Uv
              backend only (:func:`wfc.environments.uv.capture_project`).
            * ``"image"``: ``docker://<host>/<path>[:<tag>][@sha256:<hex>]``.
              Byo backend only.
        base_image: Optional base-image override (pixi, conda and uv).
            Rejected for byo because there is no Dockerfile to override.
        force: When ``True``, overwrite an existing entry for *name*.
            When ``False`` (default), an existing entry raises
            :class:`FileExistsError`.
        project_dir: Project root (containing ``.wfc/``). When ``None``,
            the resolved project root is used.
        allow_reserved: Permit a reserved ``__demo__*`` env name; only
            ``wfc demo`` passes ``True``.
        python_override: Optional container-side interpreter path recorded
            verbatim as the env's ``python`` field and ``image.json``'s
            interpreter. Intended for byo images whose Python is not on
            PATH (``wfc register-env --python``); accepted for any backend
            since an explicit record always wins at dispatch. When ``None``,
            the per-backend default from :func:`default_python_for_backend`
            is recorded.
        recording_versions: ``(module/method vN, env_fingerprint)`` for
            every method version that records *name*, read by the caller
            from the committed version lists (this package never imports
            Registration). Empty when none does.
        recording_read_error: The caller's error when a version list could
            not be read; an interpreter change is then refused, since a
            version could record the image.
        push: Push the image when the project has a registry base
            (``wfc register-env --no-push`` passes ``False``).

    Returns:
        The persisted :class:`EnvRecord`.

    Raises:
        FileExistsError: If *name* already exists and ``force=False``.
        ValueError: On invalid ref, missing required source field,
            ``base_image`` on byo, unknown backend, a captures path git
            would ignore, another interpreter for an image a method
            version records (or when the version lists cannot be read), or
            a ``[registry]`` table that cannot be read.
        RuntimeError: If a docker subprocess fails (build / pull / inspect),
            the push fails, or git refuses the register commit (nothing is
            left behind).
        FileNotFoundError: If ``.wfc/`` does not exist.
    """
    口 = AutoStep(step_num=1, name="Validate request and select backend")
    project_dir, manifest = _validate_request(
        name, backend, base_image, force, project_dir, allow_reserved
    )
    # LF is the canonical form: the Dockerfile, the staged files and the
    # source_fingerprint blob all see the same text, whatever line endings
    # the given files carry.
    source = {key: _lf_newlines(value)
              if key in _STAGED_TEXT_FIELDS and isinstance(value, str)
              else value
              for key, value in source.items()}

    # The backend table names the backends; an unknown one is refused here.
    row = backend_row(backend)
    solving = SOLVE_SOURCE_KEY in source
    filenames = _capture_filenames(backend, solved=solving)
    口 = AutoStep(step_num=2, name="Refuse a captures path git would ignore")
    check_capture_committable(project_dir, name, filenames)
    # Read before any build, so a [registry] table that cannot be read is
    # refused before Docker is asked to do anything.
    base = _registry_base(project_dir)
    if base is not None and push:
        # A name no registry accepts is refused here, before any Docker
        # work, when the image would be pushed: a built image, or a byo
        # local/ one. A byo registry image is never pushed, so never refused.
        from .registry import push_name_problem

        byo_image = strip_docker_scheme(str(source.get("image") or ""))
        if row.builds_image or byo_image.startswith(_LOCAL_REPOSITORY_PREFIX):
            name_problem = push_name_problem(
                name, registering=True,
                registered=name in manifest.get("envs", {}))
            if name_problem is not None:
                raise ValueError(name_problem)

    if row.builds_image:
        口 = AutoStep(step_num=3, name="Build the image from a staged source")
        image = _build_image(name, backend, source, base_image, project_dir)
        if image.explicit_list is not None and row.staged_lock is not None:
            # A solved env records the image's own list, not the host's (a
            # solved conda env: its row always stages the list).
            source = {**source, row.staged_lock.source_key: image.explicit_list}
    else:
        口 = AutoStep(step_num=4, name="Resolve the byo image by probe, then pull")
        image = _resolve_byo_image(source)

    # The manifest's bytes as they were, restored if the commit is refused.
    prior_manifest = _read_bytes(layout.env_manifest_path(project_dir))
    口 = AutoStep(step_num=5, name="Reconcile the interpreter, write the captures "
                                   "folder and the manifest record")
    record, capture_created, prior_image_json = _write_record(
        name, backend, source, image, manifest, project_dir, python_override,
        base_image,
        recording_versions=recording_versions,
        recording_read_error=recording_read_error,
    )

    口 = AutoStep(step_num=6, name="Push the image to the project's registry "
                                   "and record pushed.json, before the commit")
    pushed_created = _push_image(name, image, record.env_fingerprint,
                                 project_dir, base=base, push=push,
                                 prior_manifest=prior_manifest,
                                 capture_created=capture_created,
                                 prior_image_json=prior_image_json)

    口 = AutoStep(step_num=7, name="Commit the manifest, captures folder and "
                                   ".gitattributes by pathspec")
    _commit_manifest(name, project_dir, record.env_fingerprint,
                     prior_manifest=prior_manifest,
                     capture_created=capture_created,
                     prior_image_json=prior_image_json,
                     pushed_created=pushed_created)
    if image.solved_for is not None:
        print(solved_env_warning(
            name, source[SOLVE_SOURCE_KEY],
            layout.env_capture_dir(Path(), name, record.env_fingerprint)),
            file=sys.stderr)
    return record


def solved_env_warning(name: str, spec, capture_dir: Path) -> str:
    """The warning a conda env solved for linux-64 is registered with.

    Args:
        name: The env name.
        spec: The :class:`~wfc.environments.introspect.SolveSpec` it was
            solved from.
        capture_dir: The image's captures folder, relative to the project
            root.

    Returns:
        The warning: what was solved and from what, that builds may
        differ, where the image's and the host's package lists are, and a
        note when the spec names Anaconda's ``defaults`` channel.
    """
    folder = capture_dir.as_posix()
    lines = [
        f"WARNING: env {name!r} was solved for {spec.platform} from the "
        f"package versions installed in your conda env. The image's builds "
        f"and dependencies can differ from yours.",
        f"  What the image installed: {folder}/{layout.CAPTURE_PACKAGES_FILENAME}",
        f"  What your env had: {folder}/{layout.CAPTURE_HOST_DIR_NAME}/",
    ]
    if "defaults" in spec.channels:
        lines.append(
            "  Its channels include Anaconda's `defaults`; the image installs "
            "from it as your env did, under Anaconda's terms of service.")
    return "\n".join(lines)


def _capture_filenames(backend: str, *, solved: bool = False) -> list[str]:
    """Return the files a *backend* image's captures folder can hold.

    Args:
        backend: A backend the table names.
        solved: Whether the env is a conda env solved for linux-64, whose
            folder also holds the host's capture under ``host/``.

    Returns:
        ``image.json``; for a backend that builds, the ``Dockerfile``; for
        one that stages a lock, ``packages.lock``; its other staged files
        (pixi's ``pixi.toml``); when *solved*, the ``host/`` files; and,
        for every backend, ``pushed.json``, the record a push adds later.
    """
    row = backend_row(backend)
    names = [layout.CAPTURE_IMAGE_FILENAME, layout.CAPTURE_PUSHED_FILENAME]
    if row.builds_image:
        names.append(layout.CAPTURE_DOCKERFILE_FILENAME)
    if row.staged_lock is not None:
        names.append(layout.CAPTURE_PACKAGES_FILENAME)
    names += [staged.filename for staged in row.staged_extras]
    if solved:
        names += [f"{layout.CAPTURE_HOST_DIR_NAME}/{filename}"
                  for filename in _HOST_CAPTURE_FILENAMES]
    return names


#: The files of a solved conda env's ``host/`` folder.
_HOST_CAPTURE_FILENAMES = (
    layout.CAPTURE_HOST_EXPLICIT_LIST_FILENAME,
    layout.CAPTURE_HOST_FROM_HISTORY_FILENAME,
    layout.CAPTURE_HOST_PINNED_SPEC_FILENAME,
    layout.CAPTURE_HOST_PIP_FREEZE_FILENAME,
    layout.CAPTURE_HOST_INFO_FILENAME,
)


def _read_bytes(path: Path) -> bytes | None:
    """Return *path*'s bytes, or ``None`` when it does not exist."""
    return path.read_bytes() if path.is_file() else None


def _restore_bytes(path: Path, data: bytes | None) -> None:
    """Put back *path*'s prior bytes, or remove it when it did not exist."""
    if data is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(data)


def _image_json_path(project_dir: Path, name: str, fingerprint: str) -> Path:
    """Return the ``image.json`` path of *name*'s captures folder at *fingerprint*."""
    return (layout.env_capture_dir(project_dir, name, fingerprint)
            / layout.CAPTURE_IMAGE_FILENAME)


@task(purpose="When an image's captures folder is already there with another "
              "interpreter: refuse if a method version records the image (or "
              "the version lists cannot be read), naming the versions, "
              "--interpreter with the recorded interpreter, the new-env-name "
              "route, and when the requested interpreter is only the "
              "backend's default; otherwise rewrite only image.json's "
              "interpreter and return its prior bytes. A folder changed "
              "outside wfc (image.json missing or unreadable, packages.lock "
              "missing or not the recorded one), or whose packages.lock is "
              "not this registration's list, is refused first, saying what "
              "was seen and naming the new-env-name remedy",
      inputs="env name, backend, env fingerprint, project root, the "
             "interpreter to record and whether it is the backend default, "
             "the versions recording the env with their fingerprints, the "
             "caller's read error, the md5 packages.lock must have, whether "
             "the folder needs one and whether that md5 is this "
             "registration's list",
      outputs="image.json's prior bytes when rewritten, else None; "
              "ValueError when refused")
def _reconcile_interpreter(
    name: str,
    backend: str,
    fingerprint: str,
    project_dir: Path,
    interpreter: str,
    interpreter_is_default: bool,
    recording_versions: Sequence[tuple[str, str]],
    recording_read_error: str | None,
    lock_md5: str | None = None,
    needs_lock: bool = False,
    lock_is_this_list: bool = False,
) -> bytes | None:
    """Make an existing captures folder's interpreter the one being registered.

    An image's interpreter is fixed once a method version runs it: such a
    version keeps running the interpreter it recorded, so the change is
    refused and nothing is written. While no version records the image,
    nothing ever ran under the old interpreter, and only ``image.json``'s
    interpreter is rewritten; ``packages.lock``, the Dockerfile and every
    other file stay byte-identical. A refusal offers both ways on: re-run
    with ``--interpreter`` naming the interpreter the image records, or
    register the image under a new env name. A folder wfc can't register
    over is refused first, whatever the interpreter, before anything is
    written: an ``image.json`` that is missing or unreadable (it no longer
    says which interpreter the image runs), or a ``packages.lock`` that is
    missing or does not hash to *lock_md5*
    (:func:`~wfc.environments.captures.intact_image_info`, the readers'
    checks). The refusal says what was seen -- files changed outside wfc,
    or, when *lock_md5* is this registration's list, a ``packages.lock``
    that differs from it -- and names the remedy the package view's 409s
    name (:func:`~wfc.environments.captures.capture_remedy`): register the
    env again under a new name.

    Args:
        name: The env name.
        backend: The env's backend, named when *interpreter* is its default.
        fingerprint: The image's ``env_fingerprint``.
        project_dir: The project root.
        interpreter: The interpreter this registration records.
        interpreter_is_default: Whether *interpreter* is the backend's
            default because no ``--interpreter`` was given; a refusal says so.
        recording_versions: ``(module/method vN, env_fingerprint)`` for
            every version recording *name*.
        recording_read_error: The caller's error when a version list could
            not be read, or ``None``.
        lock_md5: The md5 the folder's ``packages.lock`` must have: this
            registration's ``source_fingerprint`` when it stages a list,
            else the one the env's current record names for this image, or
            ``None`` when neither applies.
        needs_lock: Whether the folder must hold a ``packages.lock``.
        lock_is_this_list: Whether *lock_md5* is this registration's list;
            a mismatch is then refused as a differing list
            (``capture_remedy(..., lock_differs=True)``).

    Returns:
        ``image.json``'s prior bytes when its interpreter was rewritten;
        ``None`` when there is no folder or it already records
        *interpreter*.

    Raises:
        ValueError: The interpreter differs and a version records the image,
            or the version lists cannot be read; or the folder was changed
            outside wfc or holds another package list (the refusal names
            :func:`~wfc.environments.captures.capture_remedy`).
    """
    from dataclasses import replace

    from .captures import (
        CaptureError,
        CaptureMismatchError,
        capture_remedy,
        intact_image_info,
    )

    if not layout.env_capture_dir(project_dir, name, fingerprint).is_dir():
        return None
    try:
        existing = intact_image_info(name, fingerprint, project_dir,
                                     lock_md5=lock_md5, needs_lock=needs_lock)
    except CaptureMismatchError as exc:
        if not lock_is_this_list:
            raise ValueError(
                f"{exc}, so wfc will not register over it. "
                f"{capture_remedy(name, fingerprint)}\n"
                f"Nothing was recorded for env {name!r}.") from exc
        # The folder is kept as it is, so registering would record a list
        # the folder lacks: its lock is the image's earlier list, or was
        # edited since.
        raise ValueError(
            f"{capture_remedy(name, fingerprint, lock_differs=True)}\n"
            f"Nothing was recorded for env {name!r}.") from exc
    except CaptureError as exc:
        # A folder changed outside wfc: an image.json that no longer says
        # which interpreter the image runs, or a package list that is not
        # the one recorded. Committing it as it is would record a broken
        # folder; writing over it could change files a method version ran.
        raise ValueError(
            f"{exc}, so wfc will not register over it. "
            f"{capture_remedy(name, fingerprint)}\n"
            f"Nothing was recorded for env {name!r}.") from exc
    if existing.interpreter == interpreter:
        return None
    requested = (f"{interpreter!r} is only the {backend} backend's default, "
                 f"because --interpreter was not given.\n"
                 if interpreter_is_default else "")
    fix = (f"{requested}To keep the image's interpreter, re-run with "
           f"`--interpreter {existing.interpreter}`. To run this image under "
           f"{interpreter!r}, register it under a new env name: "
           f"`wfc register-env <new-name> ... --interpreter {interpreter}`, "
           f"set `env: <new-name>` in the method's method.yaml, then run "
           f"`wfc register-method`.")
    if recording_read_error is not None:
        raise ValueError(
            f"env {name!r}'s image {fingerprint} runs under "
            f"{existing.interpreter!r}, and the method version lists cannot be "
            f"read, so wfc cannot tell whether a method version runs it; its "
            f"interpreter is kept.\n{recording_read_error}\n{fix}\n"
            f"Nothing was recorded for env {name!r}.")
    versions = [ref for ref, recorded in recording_versions
                if recorded == fingerprint]
    if versions:
        raise ValueError(
            f"env {name!r}'s image {fingerprint} runs under "
            f"{existing.interpreter!r} in {len(versions)} method version(s): "
            f"{', '.join(versions)}. An image's interpreter is fixed once a "
            f"method version runs it, so those versions keep running "
            f"{existing.interpreter!r}.\n{fix}\n"
            f"Nothing was recorded for env {name!r}.")
    path = _image_json_path(project_dir, name, fingerprint)
    prior = path.read_bytes()
    path.write_bytes(replace(existing, interpreter=interpreter)
                     .to_json().encode("utf-8"))
    return prior


@task(purpose="Commit .wfc/envs.json, the image's captures folder and "
              ".gitattributes (adding its captures line when missing) in one "
              "pathspec commit; on a refused commit remove the new folder and "
              "restore envs.json and .gitattributes byte for byte",
      inputs="env name, resolved project root, the image's env fingerprint, "
             "envs.json's prior bytes, whether the folder is new",
      outputs="the new commit's SHA, or None (nothing new, or no git repository)")
def _commit_manifest(name: str, project_dir: Path, fingerprint: str, *,
                     prior_manifest: bytes | None, capture_created: bool,
                     action: str = "Register",
                     prior_image_json: bytes | None = None,
                     pushed_created: bool = False) -> str | None:
    """Commit the env manifest and captures folder a registration wrote.

    ``.gitattributes`` gets the ``.wfc/captures/** -text`` line when it
    lacks it, and is committed alongside, so a clone checks every capture
    out byte for byte. Outside a git repository nothing is committed and
    ``.gitattributes`` is left alone. The Dockerfile and build context
    under ``.wfc/build/`` are ignored, not committed.

    If git refuses the commit (a hook, a timeout), nothing is left behind:
    a folder this call's registration created is removed, an existing one
    is kept (it is committed, or was there before) with its ``image.json``
    given its prior bytes back when the registration rewrote its
    interpreter and a ``pushed.json`` the push step added removed, and
    ``envs.json`` and ``.gitattributes`` get their prior bytes back
    (removed when they did not exist).
    :func:`wfc.version.commit_paths` has already unstaged the paths.

    Args:
        name: The env name (used in the commit message).
        project_dir: The resolved project root.
        fingerprint: The image's ``env_fingerprint``, naming its captures
            folder.
        prior_manifest: ``envs.json``'s bytes before the record was
            written, or ``None`` when there was no file.
        capture_created: Whether the record step created the captures
            folder (``False`` when the same image's folder was there).
        action: The commit message's verb (``"Register"``).
        prior_image_json: The existing folder's ``image.json`` bytes
            before the record step rewrote its interpreter, or ``None``
            when it did not.
        pushed_created: Whether the push step wrote ``pushed.json`` into
            the folder.

    Returns:
        The new commit's SHA, or ``None`` when nothing was committed.

    Raises:
        RuntimeError: git refused the commit; the tree is as it was.
    """
    from ..version import commit_paths, git_toplevel
    from .captures import add_captures_attributes

    if git_toplevel(project_dir) is None:
        return None
    manifest_path = layout.env_manifest_path(project_dir)
    attributes = layout.gitattributes_path(project_dir)
    prior_attributes = _read_bytes(attributes)
    add_captures_attributes(project_dir)
    try:
        sha = commit_paths(
            project_dir,
            [manifest_path,
             layout.env_capture_dir(project_dir, name, fingerprint),
             attributes],
            f"{action} env {name}", require_repo=False,
        )
    except RuntimeError as exc:
        _undo_record(name, project_dir, fingerprint,
                     prior_manifest=prior_manifest,
                     capture_created=capture_created,
                     prior_image_json=prior_image_json,
                     pushed_created=pushed_created)
        _restore_bytes(attributes, prior_attributes)
        raise RuntimeError(f"{exc}\n{_nothing_recorded(name)}") from exc
    if sha is not None:
        print(f"  git: committed {layout.STATE_DIR_NAME}/"
              f"{layout.ENV_MANIFEST_FILENAME} and {layout.STATE_DIR_NAME}/"
              f"{layout.CAPTURES_DIR_NAME}/{name}/{fingerprint}/ "
              f"as {sha[:12]}")
    return sha


def _nothing_recorded(name: str) -> str:
    """The line a refused registration ends with: the files are as they were."""
    return (f"Nothing was recorded for env {name!r}: "
            f"{layout.STATE_DIR_NAME}/{layout.ENV_MANIFEST_FILENAME} and "
            f"{layout.STATE_DIR_NAME}/{layout.CAPTURES_DIR_NAME}/ are as they "
            f"were.")


def _undo_record(name: str, project_dir: Path, fingerprint: str, *,
                 prior_manifest: bytes | None, capture_created: bool,
                 prior_image_json: bytes | None,
                 pushed_created: bool = False) -> None:
    """Put back what the record and push steps wrote, as a refused commit does.

    A captures folder the record step created is removed. An existing
    folder is kept: its ``image.json`` gets its prior bytes back when the
    record step rewrote it, and a ``pushed.json`` the push step added is
    removed. ``envs.json`` gets its prior bytes back (removed when it did
    not exist).

    Args:
        name: The env name.
        project_dir: The resolved project root.
        fingerprint: The image's ``env_fingerprint``.
        prior_manifest: ``envs.json``'s bytes before the record step, or
            ``None`` when there was no file.
        capture_created: Whether the record step created the folder.
        prior_image_json: The folder's ``image.json`` bytes before the
            record step rewrote them, or ``None``.
        pushed_created: Whether the push step wrote ``pushed.json``.
    """
    from .captures import discard_capture

    if capture_created:
        discard_capture(project_dir, name, fingerprint)
    else:
        if prior_image_json is not None:
            _restore_bytes(_image_json_path(project_dir, name, fingerprint),
                           prior_image_json)
        if pushed_created:
            _restore_bytes(layout.env_capture_dir(project_dir, name, fingerprint)
                           / layout.CAPTURE_PUSHED_FILENAME, None)
    _restore_bytes(layout.env_manifest_path(project_dir), prior_manifest)


def _registry_base(project_dir: Path) -> str | None:
    """Return the project's registry base, ``[registry] url``, or ``None``.

    Args:
        project_dir: The resolved project root.

    Returns:
        The base, or ``None`` when it is unset or the project has no
        ``wf-canvas.toml``.

    Raises:
        ValueError: The config file cannot be read as wfc config (a
            ``[registry]`` table that is not one, a ``url`` that is not a
            string).
    """
    from .registry import registry_base

    try:
        return registry_base(project_dir)
    except FileNotFoundError:
        return None


@task(purpose="Push a registered image to the project's registry before the "
              "commit: tag the image's daemon ref <base>/<env>:fp-<12 hex>, "
              "push it, read back the digest the registry reported and write "
              "pushed.json into the captures folder. Nothing is pushed with no "
              "base, with --no-push, for a byo registry image, or when "
              "pushed.json is already there; a failed push undoes the record "
              "step and refuses naming the registry and --no-push",
      inputs="env name, the resolved image, its env fingerprint, project root, "
             "the registry base, whether to push, what the record step changed",
      outputs="whether this call wrote pushed.json")
def _push_image(name: str, image: _ResolvedImage, fingerprint: str,
                project_dir: Path, *, base: str | None, push: bool,
                prior_manifest: bytes | None, capture_created: bool,
                prior_image_json: bytes | None) -> bool:
    """Push the image a registration recorded, and record the push.

    Prints where the image went: pushed to the base, already there, in its
    own registry (a byo registry image), or staying on this machine.

    Args:
        name: The env name.
        image: The image the build or resolve step produced.
        fingerprint: The image's ``env_fingerprint``.
        project_dir: The resolved project root.
        base: The project's registry base, or ``None``.
        push: ``False`` for ``--no-push``.
        prior_manifest: ``envs.json``'s bytes before the record step.
        capture_created: Whether the record step created the folder.
        prior_image_json: The folder's ``image.json`` bytes before the
            record step rewrote them, or ``None``.

    Returns:
        Whether this call wrote ``pushed.json``.

    Raises:
        RuntimeError: The tag, the push or the digest read failed; the
            record step has been undone and nothing is committed.
    """
    from .captures import pushed_record
    from .registry import push_image, push_name_problem, registry_host

    bare = strip_docker_scheme(image.container_ref)
    if not bare.startswith(_LOCAL_REPOSITORY_PREFIX):
        # A byo image already in a registry is pulled from there; it is
        # never copied to the project's registry.
        repository = bare.split("@", 1)[0]
        print(f"  registry: {repository} is pulled from its own registry"
              + (f"; it is not copied to {base}" if base else ""))
        return False
    name_problem = push_name_problem(name)
    if name_problem is not None:
        later = (f"push-env cannot push it under this name: {name_problem}")
    else:
        later = f"`wfc push-env {name}` pushes it" + (f" to {base}" if base else "")
    if base is None:
        print(f"  registry: no [registry] url is set, so the image stays on "
              f"this machine; {later}")
        return False
    if not push:
        print(f"  registry: --no-push, so the image stays on this machine; {later}")
        return False
    from . import docker as docker_runner

    reading = True
    try:
        existing = pushed_record(name, fingerprint, project_dir)
        reading = False
        if existing is None:
            口 = AutoStep(step_num=1, name="Tag, push and record the push")
            record, created = push_image(project_dir, name, fingerprint,
                                         image.container_ref, base)
    except BaseException as exc:
        _undo_record(name, project_dir, fingerprint,
                     prior_manifest=prior_manifest,
                     capture_created=capture_created,
                     prior_image_json=prior_image_json)
        if not isinstance(exc, Exception):
            raise
        if reading:
            raise RuntimeError(
                f"env {name!r}: the image's push record cannot be read: {exc}\n"
                f"{_nothing_recorded(name)}\nRemove the broken "
                f"{layout.CAPTURE_PUSHED_FILENAME} and register again."
            ) from exc
        if isinstance(exc, docker_runner.DockerTimeoutError):
            remedy = ("Docker did not answer; check that Docker is running and "
                      "register again")
        else:
            remedy = f"Run `docker login {registry_host(base)}` and register again"
        raise RuntimeError(
            f"Could not push env {name!r}'s image to the registry {base}: "
            f"{exc}\n{_nothing_recorded(name)}\n{remedy}, or pass --no-push to "
            f"keep the image on this machine (`wfc push-env {name}` pushes it "
            f"later)."
        ) from exc
    if existing is not None:
        print(f"  registry: already pushed to {base} as {name}:{existing.tag}")
        return False
    print(f"  registry: pushed to {base} as {name}:{record.tag} "
          f"({record.digest})")
    return created


def refuse_existing_name(manifest: dict, name: str, force: bool) -> None:
    """Refuse a name the manifest already records, unless ``force`` is set.

    Args:
        manifest: The manifest as loaded for a register.
        name: The env name being registered.
        force: Whether an existing record may be replaced.

    Raises:
        FileExistsError: *name* is recorded and ``force`` is ``False``.
    """
    if name in manifest.get("envs", {}) and not force:
        raise FileExistsError(
            f"Env {name!r} already exists in .wfc/envs.json. "
            f"Re-run with `--force` to overwrite."
        )


@task(purpose="Refuse a reserved-namespace name and a name outside the env-name "
              "rule before any docker interaction or manifest write, resolve "
              "project_dir, reject a duplicate env name without force, and "
              "reject base_image on the byo backend")
def _validate_request(
    name: str,
    backend: str,
    base_image: str | None,
    force: bool,
    project_dir: Path | None,
    allow_reserved: bool,
) -> tuple[Path, dict]:
    """Refuse a bad request before any Docker call or manifest write.

    Args:
        name: The env name to register.
        backend: The requested backend. Only ``"byo"`` is checked here, for
            its base-image refusal; :func:`register` refuses an unknown
            backend after this step.
        base_image: The optional base-image override.
        force: Whether an existing entry for *name* may be overwritten.
        project_dir: The project root, or ``None`` for the resolved project root.
        allow_reserved: Whether a reserved ``__demo__`` name is allowed.

    Returns:
        The resolved project root, and the manifest as loaded by
        :func:`~wfc.environments.manifest.load_manifest_for_register` (an
        older version-1 or version-2 file is accepted), which :func:`_write_record` updates and
        writes back as the current version.

    Raises:
        ValueError: On a reserved name without the opt-in, a name outside
            the env-name rule, or ``base_image`` on byo.
        FileNotFoundError: If ``.wfc/`` does not exist.
        FileExistsError: If *name* already exists and ``force=False``.
    """
    from ..contracts import validate_env_name
    from ..reserved import check_reserved_name

    # Reserved-namespace guard: refuse a __demo__* env name before any
    # docker interaction or manifest write. `wfc demo` opts in.
    check_reserved_name(name, "env", allow_reserved)
    # The env-name rule, owned by Contracts — the same predicate method
    # registration applies to a method.yaml's env value.
    validate_env_name(name)
    if project_dir is None:
        from ..persistence import project_root as get_project_root
        project_dir = get_project_root()
    project_dir = Path(project_dir).resolve()
    if not layout.state_dir(project_dir).is_dir():
        raise FileNotFoundError(
            f"No .wfc/ directory at {project_dir} — run `wfc init` first"
        )

    # Existence check up front so we can fail BEFORE running docker.
    manifest = load_manifest_for_register(project_dir)
    refuse_existing_name(manifest, name, force)

    if backend == "byo" and base_image is not None:
        raise ValueError(
            "--base-image is not valid for the byo backend — there is no "
            "Dockerfile to override. The upstream image is used as-is."
        )

    return project_dir, manifest


@task(purpose="Render the backend's Dockerfile, stage the build context, build "
              "the image and resolve its digest into a local docker:// ref")
def _build_image(
    name: str,
    backend: str,
    source: dict,
    base_image: str | None,
    project_dir: Path,
) -> _ResolvedImage:
    """Build a pixi, conda or uv env's image and resolve its digest.

    Args:
        name: The env name; the image is tagged ``local/<name>``.
        backend: ``"pixi"`` or ``"conda"``.
        source: The per-backend payload; see :func:`register`.
        base_image: The optional base-image override.
        project_dir: The resolved project root.

    Returns:
        The ``docker://local/<name>@sha256:<hex>`` image, with the record's
        ``source`` and ``built_from_lock`` values for the backend; for a
        conda payload solved for linux-64, also the image's read-back
        explicit list.

    Raises:
        ValueError: If a staged pixi lock cannot build *name*, a staged
            conda explicit list targets a platform other than linux-64, or
            a pinned version has no linux-64 build.
        RuntimeError: If the build or the inspect fails, or the inspect
            returns no digest.
    """
    from . import dockerfiles as df_pkg

    口 = Step(step_num=1, name="Check the source",
             purpose="Run the backend row's source check (a staged "
                     "pixi.lock against the requested env name, lock-format "
                     "version and linux-64 platform; a staged conda explicit "
                     "list against linux-64) and gather the generator's "
                     "arguments")
    gen_kwargs: dict = {"env_name": name}
    if base_image is not None:
        gen_kwargs["base_image"] = base_image

    row = backend_row(backend)
    if row.validate_source is not None:
        # Fail here — before any Dockerfile render, staging, or docker
        # work — when the staged lock or list cannot build the requested
        # env (a pixi lock without the env or written by a newer pixi than
        # the pinned in-container build tool; a non-linux-64 conda list).
        row.validate_source(source, name)
    gen_kwargs["pip_freeze_content"] = source.get(
        STAGED_PIP_FREEZE.source_key, ""
    )
    if row.generator_args is not None:
        # What else the generator reads from the payload (uv's Python minor).
        gen_kwargs.update(row.generator_args(source))
    source_field = row.recorded_source
    built_from_lock = row.recorded_lock
    # A project whose git remote is on GitHub labels its images with the
    # repository; the label moves no fingerprint and changes no Dockerfile.
    from .registry import source_labels
    labels = source_labels(project_dir)

    if backend == "conda" and SOLVE_SOURCE_KEY in source:
        # A conda env from a host that is not linux-64: solved once, then
        # recorded and staged as the image's own explicit list.
        口 = AutoStep(step_num=2, name="Solve the pinned spec for linux-64, "
                                       "build, and read the image's list back")
        solved = _solve_build(name, source[SOLVE_SOURCE_KEY], gen_kwargs,
                              project_dir, labels)
        return solved

    口 = Step(step_num=3, name="Render Dockerfile",
             purpose="Render the backend's Dockerfile via "
                     "dockerfiles.generate_for_backend")
    dockerfile = df_pkg.generate_for_backend(backend, **gen_kwargs)
    if dockerfile is None:  # defensive — only byo returns None
        raise RuntimeError(
            f"Generator for backend {backend!r} returned no Dockerfile."
        )

    口 = Step(step_num=4, name="Write Dockerfile and stage build context",
             purpose="Write the Dockerfile to .wfc/build/<name>/ and stage the "
                     "pip-freeze, pixi, and conda source blobs into the build context")
    build_dir = _stage_build_context(project_dir, name, dockerfile, row, source)

    口 = AutoStep(step_num=5, name="Build the image and resolve its digest")
    image = _build_and_resolve(name, build_dir, source_field, built_from_lock,
                               labels)
    return image


def _stage_build_context(project_dir: Path, name: str, dockerfile: str,
                         row, source: dict) -> Path:
    """Write the Dockerfile and the staged source files to ``.wfc/build/<name>/``.

    The Dockerfile generators are pure (string in, string out); the register
    workflow is the one place that knows about disk, so file staging lives
    here, under the file names the generators COPY.

    Args:
        project_dir: The resolved project root.
        name: The env name.
        dockerfile: The rendered Dockerfile.
        row: The backend's table row (its staged lock and extra files).
        source: The register source payload holding the files' text.

    Returns:
        The build context directory.
    """
    build_dir = layout.env_build_dir(project_dir, name)
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    (build_dir / STAGED_PIP_FREEZE.filename).write_text(
        source.get(STAGED_PIP_FREEZE.source_key, ""), encoding="utf-8"
    )
    for staged in (row.staged_lock, *row.staged_extras):
        text = source.get(staged.source_key) if staged is not None else None
        if text is not None:
            (build_dir / staged.filename).write_text(text, encoding="utf-8")
    return build_dir


@task(purpose="Build a conda env from a host that is not linux-64: solve its "
              "pinned spec for linux-64 in a throwaway build context, refuse a "
              "pin with no linux-64 build by name, copy the image's own "
              "explicit list out of a never-started container, and stage "
              ".wfc/build/<name>/ as the explicit-list recipe over that list",
      inputs="env name, the pinned SolveSpec, the generator arguments, project root",
      outputs="the built image with its read-back explicit list")
def _solve_build(name: str, spec, gen_kwargs: dict,
                 project_dir: Path,
                 labels: dict[str, str] | None = None) -> _ResolvedImage:
    """Solve a pinned conda spec for linux-64, build it, and read its list back.

    The solve recipe (:func:`wfc.environments.dockerfiles.conda.generate_solve`)
    is built from a temporary context beside ``.wfc/build/<name>/``. After
    the build, the image's explicit list is copied out of the image by its
    image ID (``sha256:<hex>``, which the classic and the containerd image
    stores both resolve), with :func:`wfc.environments.docker.copy_out`;
    nothing runs in the image. Only then is ``.wfc/build/<name>/`` written:
    the explicit-list recipe, that list as ``explicit-list.txt`` and the pip
    set as ``pip-freeze.txt``, so the context installs exactly what the image
    installed and never solves again.

    Args:
        name: The env name; the image is tagged ``local/<name>``.
        spec: The :class:`~wfc.environments.introspect.SolveSpec` to solve.
        gen_kwargs: The conda generator's arguments (``env_name``,
            ``pip_freeze_content``, optional ``base_image``).
        project_dir: The resolved project root.
        labels: Image labels to build with, or ``None``.

    Returns:
        The image, with ``explicit_list`` the read-back list (LF) and
        ``solved_for`` the spec's platform.

    Raises:
        ValueError: A pinned version has no linux-64 build in the spec's
            channels; the message names it and suggests pixi.
        RuntimeError: The build fails otherwise, the inspect fails, or the
            read-back file is not an explicit list.
    """
    import tempfile

    from . import docker as docker_runner
    from .argv import daemon_ref
    from .dockerfiles import conda as conda_df

    row = backend_row("conda")
    build_dir = layout.env_build_dir(project_dir, name)
    build_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{name}-solve-",
                                     dir=build_dir.parent) as tmp:
        solve_dir = Path(tmp)
        口 = Step(step_num=1, name="Stage the solve context",
                 purpose="Write the solve recipe, the pinned spec and the pip "
                         "set into a temporary build context")
        (solve_dir / "Dockerfile").write_text(
            conda_df.generate_solve(**gen_kwargs), encoding="utf-8")
        (solve_dir / conda_df.SOLVE_SPEC_FILENAME).write_text(
            spec.to_yaml(), encoding="utf-8")
        (solve_dir / STAGED_PIP_FREEZE.filename).write_text(
            gen_kwargs["pip_freeze_content"], encoding="utf-8")

        口 = Step(step_num=2, name="Build, refusing a pin with no linux-64 build",
                 purpose="Build the solve recipe; a solver report naming "
                         "requests that do not exist becomes a refusal naming "
                         "them and suggesting pixi")
        try:
            image = _build_and_resolve(name, solve_dir, row.recorded_source,
                                       row.recorded_lock, labels)
        except RuntimeError as exc:
            missing = conda_df.unsolvable_requests(str(exc))
            if missing:
                raise ValueError(_no_linux_build_message(name, spec, missing)) from exc
            raise

        口 = Step(step_num=3, name="Read the image's explicit list back",
                 purpose="Copy the list the recipe wrote out of a never-started "
                         "hardened container, by the image ID")
        readback = solve_dir / "readback"
        readback.mkdir()
        copied = docker_runner.copy_out(daemon_ref(image.container_ref),
                                        conda_df.IMAGE_EXPLICIT_LIST_PATH,
                                        dest_dir=readback)
        explicit_list = _lf_newlines(copied.read_bytes().decode("utf-8"))
    if "@EXPLICIT" not in explicit_list:
        raise RuntimeError(
            f"The image built for env {name!r} wrote no explicit list to "
            f"{conda_df.IMAGE_EXPLICIT_LIST_PATH}; nothing was recorded.")

    口 = Step(step_num=4, name="Stage the explicit-list build context",
             purpose="Write .wfc/build/<name>/ as the explicit-list recipe "
                     "over the read-back list and the pip set, the context "
                     "the image is built from")
    lock = row.staged_lock
    dockerfile = row.generate(**gen_kwargs)
    if lock is None or dockerfile is None:
        # The conda row stages an explicit list and renders a Dockerfile.
        raise RuntimeError("the conda backend row stages no explicit list or "
                           "renders no Dockerfile")
    staged = {STAGED_PIP_FREEZE.source_key: gen_kwargs["pip_freeze_content"],
              lock.source_key: explicit_list}
    _stage_build_context(project_dir, name, dockerfile, row, staged)
    return image._replace(explicit_list=explicit_list, solved_for=spec.platform)


def _no_linux_build_message(name: str, spec, missing: list[str]) -> str:
    """The refusal for pinned versions the spec's channels have no linux-64 build of.

    Args:
        name: The env name.
        spec: The :class:`~wfc.environments.introspect.SolveSpec` that was solved.
        missing: The unsatisfiable requests, ``name=version``.

    Returns:
        The message: the packages, why, and the pixi route.
    """
    shown = ", ".join(missing)
    channels = ", ".join(spec.channels) or "the default channels"
    return (
        f"Cannot register env {name!r}: there is no {spec.platform} build of "
        f"{shown} in {channels}. wfc solves a conda env from Windows or macOS "
        f"for {spec.platform} with the versions you have installed, and "
        f"{'this package has' if len(missing) == 1 else 'these packages have'} "
        f"no Linux build at that version (it may be Windows- or macOS-only). "
        f"Remove it from the env or change its version, or use a pixi "
        f"environment, which locks for linux-64 from any machine "
        f"(`pixi init`, `pixi add` your packages, then "
        f"`wfc register-env {name} pixi:<project>:<env>`)."
    )


@task(purpose="Build the image from a staged build context, tagged under "
              "local/<name>, and resolve its image ID into the digest-pinned "
              "docker://local/<name>@sha256:<hex> ref")
def _build_and_resolve(
    name: str,
    build_dir: Path,
    source_field: str | None,
    built_from_lock: str | None,
    labels: dict[str, str] | None = None,
) -> _ResolvedImage:
    """Build a staged build context and resolve the image's digest.

    Registration's builds (:func:`_build_image`, :func:`_solve_build`)
    end here. Nothing is pulled.

    Args:
        name: The env name; the image is tagged ``local/<name>``.
        build_dir: The staged build context, ``.wfc/build/<name>/``.
        source_field: The value recorded as the env's ``source`` field.
        built_from_lock: The value recorded as ``built_from_lock``.
        labels: Image labels to build with (the source label,
            :func:`wfc.environments.registry.source_labels`); ``None`` or
            empty builds with none.

    Returns:
        The ``docker://local/<name>@sha256:<hex>`` image.

    Raises:
        RuntimeError: If the build or the inspect fails, or the inspect
            returns no digest.
    """
    from . import docker as docker_runner

    # Local build tagged under the `local/` namespace so the recorded ref
    # matches the digest-pinned docker://<host>/<path>@sha256 shape that
    # validate_container_ref enforces — parity with the byo branch and the
    # integration-test fixtures, no real registry involved. Docker is
    # handed the record's daemon ref (the image ID), never this tag.
    image_name = f"{_LOCAL_REPOSITORY_PREFIX}{name}"
    build_tag = f"{image_name}:_wfc-build"
    build_labels = {"labels": labels} if labels else {}
    口 = AutoStep(step_num=1, name="Build the image")
    docker_runner.build(build_dir, build_tag, **build_labels)
    口 = Step(step_num=2, name="Resolve the image digest",
             purpose="Inspect the built image, strip the sha256: prefix, and form "
                     "the digest-pinned docker:// ref")
    raw_digest = docker_runner.image_inspect(build_tag)
    digest_hex = raw_digest.removeprefix("sha256:").strip()
    if not digest_hex:
        raise RuntimeError(
            f"docker image inspect returned no digest for {build_tag!r}"
        )
    container_ref = f"docker://{image_name}@sha256:{digest_hex}"
    # Final form must pass the strict shape check (parity with byo).
    validate_container_ref(container_ref)
    return _ResolvedImage(
        container_ref=container_ref,
        digest_hex=digest_hex,
        source_field=source_field,
        built_from_lock=built_from_lock,
    )


@task(purpose="Resolve a byo image's digest by probe-then-pull (a local/ "
              "image is never pulled), record the registry digest for a "
              "registry image and the image ID for a local/ one, refuse a "
              "supplied digest that differs, and form the digest-pinned "
              "docker:// ref")
def _resolve_byo_image(source: dict) -> _ResolvedImage:
    """Resolve a byo image to a digest-pinned ref, pulling only on a miss.

    A registry image is recorded by its registry digest (its
    ``RepoDigests`` entry), which every image store resolves. A ``local/``
    image was built on this machine and has no registry digest, so it is
    recorded by its image ID, and it is never pulled.

    Args:
        source: The byo payload; ``source["image"]`` is the
            ``docker://<host>/<path>[:<tag>][@sha256:<hex>]`` reference.

    Returns:
        The ``docker://<original-prefix>@sha256:<hex>`` image. Its
        ``source`` value is the reference as supplied, and it has no
        ``built_from_lock``.

    Raises:
        ValueError: If ``source["image"]`` is missing or not a valid
            reference.
        RuntimeError: If the pull or the inspect fails (a probe Docker does
            not answer is a ``DockerTimeoutError``, never read as a miss
            that pulls), a ``local/`` image is not in the daemon, a registry image lists no registry digest
            for its repository, the inspect returns no digest, or a supplied
            digest differs from the recorded one.
    """
    from . import docker as docker_runner

    口 = Step(step_num=1, name="Parse the reference",
             purpose="Require source['image'], split the docker:// reference "
                     "into its image prefix, tag and any supplied digest, and "
                     "strip the scheme for the daemon")
    ref = source.get("image")
    if not ref:
        raise ValueError(
            "BYO backend requires source['image'] = 'docker://...'"
        )
    # Parse the user-supplied ref. Floating tags are allowed at input
    # (we resolve them); the post-resolution manifest value is always
    # digest-pinned.
    image_prefix, _tag, user_digest = parse_byo_ref(ref)

    # Daemon-side ref strips the docker:// scheme.
    daemon_ref = strip_docker_scheme(ref)

    口 = Step(step_num=2, name="Probe, then pull on a miss",
             purpose="Inspect the image in the local daemon, and pull it and "
                     "inspect again only when the first inspect misses; a "
                     "local/ image that misses is refused, never pulled")
    is_local = image_prefix.startswith(_LOCAL_REPOSITORY_PREFIX)
    # Probe-then-pull: only pull if not already local. A probe Docker did
    # not answer is no miss: the registration is refused naming the timeout.
    try:
        image_id = docker_runner.image_inspect(daemon_ref)
    except docker_runner.DockerTimeoutError:
        raise
    except RuntimeError as exc:
        if is_local:
            raise RuntimeError(
                f"image {daemon_ref!r} is not in the local Docker daemon. "
                f"A {_LOCAL_REPOSITORY_PREFIX} image is built on this "
                f"machine and never pulled; build it, then register it."
            ) from exc
        docker_runner.pull(daemon_ref)
        image_id = docker_runner.image_inspect(daemon_ref)

    口 = Step(step_num=3, name="Read the recorded digest",
             purpose="Take the registry digest for the image's repository, "
                     "or the image ID for a local/ image, which has none")
    raw_digest = (
        image_id if is_local
        else docker_runner.repo_digest(daemon_ref, image_prefix)
    )

    口 = Step(step_num=4, name="Check the digest",
             purpose="Refuse an empty digest and a supplied digest that "
                     "differs from the recorded one, then form and "
                     "shape-check the digest-pinned docker:// ref")
    digest_hex = raw_digest.removeprefix("sha256:").strip()
    if not digest_hex:
        raise RuntimeError(
            f"docker image inspect returned no digest for {daemon_ref!r}"
        )
    # If the user supplied a digest, confirm it matches the one recorded —
    # silently swapping in a different digest would defeat the whole point
    # of digest pinning.
    if user_digest is not None and user_digest != digest_hex:
        recorded = "its image ID" if is_local else "its registry digest"
        raise RuntimeError(
            f"Digest mismatch for {ref!r}: user supplied "
            f"sha256:{user_digest}, but {recorded} in the local daemon is "
            f"sha256:{digest_hex}."
        )
    container_ref = f"docker://{image_prefix}@sha256:{digest_hex}"
    # Validate the final form passes the strict shape check.
    validate_container_ref(container_ref)
    return _ResolvedImage(
        container_ref=container_ref,
        digest_hex=digest_hex,
        source_field=ref,
        built_from_lock=None,
    )


@task(purpose="Precompute env_fingerprint (and source_fingerprint, the md5 of "
              "the package list, for any pixi, conda or uv registration that staged "
              "source content); when the image's captures folder is already "
              "there, reconcile its image.json interpreter (rewritten, or "
              "refused once a method version records the image); otherwise "
              "write the folder; write the EnvRecord into .wfc/envs.json, "
              "restoring the folder or image.json if that write fails")
def _write_record(
    name: str,
    backend: str,
    source: dict,
    image: _ResolvedImage,
    manifest: dict,
    project_dir: Path,
    python_override: str | None,
    base_image: str | None = None,
    *,
    recording_versions: Sequence[tuple[str, str]] = (),
    recording_read_error: str | None = None,
) -> tuple[EnvRecord, bool, bytes | None]:
    """Precompute the env's fingerprints, write its captures folder and its record.

    Args:
        name: The env name, the record's key in ``.wfc/envs.json``.
        backend: ``"pixi"``, ``"conda"``, ``"uv"`` or ``"byo"``.
        source: The per-backend payload; see :func:`register`.
        image: The image the build or resolve step produced.
        manifest: The manifest as :func:`_validate_request` loaded it. An
            older-version manifest is written back as the current version
            with every other env's ``env_fingerprint`` cleared, so each of
            them is refused at run time until it is re-registered.
        project_dir: The resolved project root.
        python_override: An explicit container-side interpreter path, or
            ``None`` for the backend's default.
        base_image: The base-image override the Dockerfile was rendered
            with, or ``None`` for the backend's pinned base; recorded in
            ``image.json`` for a backend that builds.
        recording_versions: For a register, ``(module/method vN,
            env_fingerprint)`` for every version recording *name*; see
            :func:`_reconcile_interpreter`.
        recording_read_error: The caller's error when a version list could
            not be read, or ``None``.

    Returns:
        The persisted :class:`EnvRecord`; whether this call created the
        captures folder (``False`` when the same image's folder was already
        there and was kept); and, when a register rewrote that folder's
        ``image.json`` interpreter, its prior bytes (else ``None``).

    Raises:
        RuntimeError: If the content inspect fails (an
            :class:`~wfc.environments.docker.ImageNotFoundError` when the
            daemon has no such image).
        ValueError: If the inspect reports no layers, or a register names
            another interpreter for an image a method version records.
    """
    from ..storage import store_env_content
    from . import docker as docker_runner
    from .argv import daemon_ref
    from .captures import (
        ImageInfo,
        discard_capture,
        package_list_md5,
        package_list_text,
        write_capture,
    )
    from .fingerprint import capture_env_content

    # -------------------------------------------------------------------------
    # env_fingerprint precompute (read back at run time by
    # resolve_env_fingerprint): the content of the image Docker starts the
    # env from -- its layers, Env, User and WorkingDir -- never its name.
    # The content blob still goes to the DVC cache.
    # -------------------------------------------------------------------------
    content = docker_runner.image_content(daemon_ref(image.container_ref))
    blob = capture_env_content(content)
    env_fingerprint = store_env_content(blob, project_dir, kind="env-fingerprint")

    # -------------------------------------------------------------------------
    # source_fingerprint: the md5 of the captures folder's packages.lock, for
    # EVERY pixi, conda or uv registration that staged source content (live-spec
    # capture AND --from file mode). The package list is assembled from the
    # *source* dict already staged into the build context — the FULL lock /
    # explicit-list content, the delimiter, the pip freeze — and lives in git
    # only. It never feeds env_fingerprint or wfc.identity.build_cache_key.
    # Stays None when no lock or explicit list was staged, and for byo.
    # -------------------------------------------------------------------------
    row = backend_row(backend)
    package_list: str | None = None
    if row.staged_lock is not None:
        lock_content = source.get(row.staged_lock.source_key)
        if lock_content is not None:
            package_list = package_list_text(
                lock_content, source.get(STAGED_PIP_FREEZE.source_key, ""))
    source_fingerprint = (package_list_md5(package_list)
                          if package_list is not None else None)

    # Per-env interpreter path (thin-container dispatch): recorded truth so
    # run-step launches the method script under the env's actual Python.
    # An explicit --python override wins; otherwise the per-backend default
    # (which mirrors what the generator recipe materialized).
    env_python = python_override or default_python_for_backend(backend, name)

    # -------------------------------------------------------------------------
    # Captures folder: written before the record names its fingerprint, so a
    # schema-3 record always has one. An existing folder for this
    # fingerprint (the same image again) is kept as it is, except that a
    # register with another interpreter rewrites image.json's interpreter
    # while no method version records the image, and is refused, before
    # anything is written, once one does (or when the folder is not as
    # wfc wrote it or not this registration's: image.json missing or
    # unreadable, packages.lock missing or not this registration's list).
    # -------------------------------------------------------------------------
    prior_image_json: bytes | None = None
    # The md5 an existing folder's packages.lock must have. A folder is
    # kept as it is, so when this registration stages a list the folder
    # must already hold that list, whichever record (if any) names the
    # folder; otherwise the md5 the env's current record names, when
    # that record is this image's.
    if source_fingerprint is not None:
        lock_md5: str | None = source_fingerprint
    else:
        current = (manifest.get("envs") or {}).get(name) or {}
        lock_md5 = (current.get("source_fingerprint") or None
                    if current.get("env_fingerprint") == env_fingerprint
                    else None)
    口 = AutoStep(step_num=1, name="Reconcile an existing folder's interpreter")
    prior_image_json = _reconcile_interpreter(
        name, backend, env_fingerprint, project_dir, env_python,
        python_override is None, recording_versions, recording_read_error,
        lock_md5=lock_md5,
        needs_lock=lock_md5 is not None,
        lock_is_this_list=source_fingerprint is not None)
    dockerfile: str | None = None
    if row.builds_image:
        staged_dockerfile = layout.env_build_dir(project_dir, name) / "Dockerfile"
        if staged_dockerfile.is_file():
            dockerfile = _lf_newlines(
                staged_dockerfile.read_bytes().decode("utf-8"))
    extras = {staged.filename: source[staged.source_key]
              for staged in row.staged_extras
              if isinstance(source.get(staged.source_key), str)}
    # A solved conda env keeps the host's own capture, as given, under host/.
    extras.update({f"{layout.CAPTURE_HOST_DIR_NAME}/{filename}": text
                   for filename, text
                   in (source.get(HOST_CAPTURE_SOURCE_KEY) or {}).items()})
    _, capture_created = write_capture(
        project_dir, name, env_fingerprint,
        ImageInfo(
            backend=backend,
            interpreter=env_python,
            image=image.container_ref,
            base_image=(base_image or row.default_base(source)
                        if row.builds_image else None),
            solved_for=image.solved_for,
            groups=tuple(source.get(UV_GROUPS_SOURCE_KEY) or ()),
            extras=tuple(source.get(UV_EXTRAS_SOURCE_KEY) or ()),
            python_minor=source.get(UV_PYTHON_MINOR_SOURCE_KEY),
        ),
        package_list=package_list,
        dockerfile=dockerfile,
        files=extras,
    )

    # -------------------------------------------------------------------------
    # Manifest write
    # -------------------------------------------------------------------------
    from datetime import datetime
    built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    record = EnvRecord(
        backend=backend,
        source=image.source_field,
        container=image.container_ref,
        env_fingerprint=env_fingerprint,
        built_from_lock=image.built_from_lock,
        built_at=built_at,
        source_fingerprint=source_fingerprint,
        python=env_python,
    )
    put_record(manifest, name, record.to_dict())
    try:
        save_manifest(project_dir, manifest)
    except BaseException:
        # The replace is atomic, so envs.json is as it was; a folder this
        # call created goes too, as on a refused commit (.gitattributes is
        # only touched by the commit step, which is not reached).
        if capture_created:
            discard_capture(project_dir, name, env_fingerprint)
        elif prior_image_json is not None:
            _restore_bytes(_image_json_path(project_dir, name, env_fingerprint),
                           prior_image_json)
        raise
    return record, capture_created, prior_image_json
