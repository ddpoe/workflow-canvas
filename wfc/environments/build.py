"""Build a container env and record it: the ``register`` workflow.

``wfc register-env`` and the demo scaffold call
:func:`register`. It renders the backend's Dockerfile and builds the image,
or resolves a byo image's digest, then precomputes ``env_fingerprint`` and
writes the env's record to ``.wfc/envs.json``. Each phase is a step function:
:func:`_validate_request`, then :func:`_build_image` for pixi and conda or
:func:`_resolve_byo_image` for byo, then :func:`_write_record`, then
:func:`_commit_manifest`, which commits ``.wfc/envs.json`` by pathspec.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from typing import NamedTuple

from axiom_annotations import AutoStep, Step, task, workflow

from .. import layout
from ..contracts import parse_byo_ref, validate_container_ref
from .argv import _LOCAL_REPOSITORY_PREFIX, strip_docker_scheme
from .interpreter import default_python_for_backend
from .manifest import MANIFEST_SCHEMA_VERSION, EnvRecord, load_manifest, save_manifest


class _ResolvedImage(NamedTuple):
    """A digest-pinned image, with the record fields its backend decides.

    Attributes:
        container_ref: The ``docker://<host>/<path>@sha256:<hex>`` ref that is
            recorded as the env's ``container`` field.
        fingerprint_image: The image part, without the scheme, that
            ``env_fingerprint`` is computed over.
        digest_hex: The image digest's hex, without the ``sha256:`` prefix.
        source_field: The value recorded as the env's ``source`` field.
        built_from_lock: The value recorded as ``built_from_lock``; ``None``
            for byo.
    """

    container_ref: str
    fingerprint_image: str
    digest_hex: str
    source_field: str | None
    built_from_lock: str | None


#: Build-context filenames the source content is staged under, the names the
#: Dockerfile generators COPY. A rebuild reads them back from the context.
_STAGED_PIP_FREEZE = "pip-freeze.txt"
_STAGED_PIXI_MANIFEST = "pixi.toml"
_STAGED_LOCK = {"pixi": "pixi.lock", "conda": "explicit-list.txt"}

@workflow(purpose="Register a container env: build the image, resolve its digest, "
                  "and write the manifest entry")
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
) -> EnvRecord:
    """Register a container env.

    Builds the image, resolves its digest, and writes the manifest entry.
    Orchestration entry point for ``wfc register-env``. Dispatch by backend:

      - **pixi / conda:** assemble per-backend generator kwargs
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
    (:func:`_validate_request`); for pixi and conda, build
    (:func:`_build_image`); for byo, resolve by probe-then-pull
    (:func:`_resolve_byo_image`); record (:func:`_write_record`).

    The image fingerprint (``env_fingerprint``) is precomputed at
    registration time by feeding a canonical
    ``container:<image>@sha256:<hex>`` spec through
    :func:`wfc.environments.fingerprint.capture_env_content` and :func:`wfc.storage.store_env_content`.

    For every pixi/conda registration that stages source content (both
    live-spec capture and ``--from`` file mode), a package-list blob is
    assembled from *source* — the FULL lock / explicit-list content plus
    pip-freeze — and stored as ``source_fingerprint`` on the returned
    :class:`EnvRecord`, retrievable via the canvas
    ``GET /api/registry/envs/blob/<md5>`` endpoint and the ``/packages``
    parser. This is a separate blob from ``env_fingerprint`` (the container
    image-digest fingerprint) and does not affect it.

    Args:
        name: Env name (KEY in ``.wfc/envs.json::envs``; not stored inside the
            record).
        backend: One of ``"pixi"``, ``"conda"``, ``"byo"``.
        source: Per-backend payload dict. Supported keys (all optional):
            * ``"pip_freeze_content"``: verbatim pip freeze output, fed to
              the pixi/conda Dockerfile generators and staged as
              ``pip-freeze.txt`` in the build context.
            * ``"explicit_list_content"``: conda explicit-list contents
              (output of ``conda list --explicit --md5``). Staged as
              ``explicit-list.txt``. Conda backend only.
            * ``"pixi_lock_content"``: contents of a ``pixi.lock`` file.
              Staged as ``pixi.lock``. Pixi backend only.
            * ``"pixi_toml_content"``: contents of a ``pixi.toml`` file.
              Staged as ``pixi.toml``. Pixi backend only.
            * ``"image"``: ``docker://<host>/<path>[:<tag>][@sha256:<hex>]``.
              Byo backend only.
        base_image: Optional base-image override (pixi/conda only).
            Rejected for byo because there is no Dockerfile to override.
        force: When ``True``, overwrite an existing entry for *name*.
            When ``False`` (default), an existing entry raises
            :class:`FileExistsError`.
        project_dir: Project root (containing ``.wfc/``). When ``None``,
            the resolved project root is used.
        allow_reserved: Permit a reserved ``__demo__*`` env name; only
            ``wfc demo`` passes ``True``.
        python_override: Optional container-side interpreter path recorded
            verbatim as the env's ``python`` field. Intended for byo images
            whose Python is not on PATH (``wfc register-env --python``);
            accepted for any backend since an explicit record always wins
            at dispatch. When ``None``, the per-backend default from
            :func:`default_python_for_backend` is recorded.

    Returns:
        The persisted :class:`EnvRecord`.

    Raises:
        FileExistsError: If *name* already exists and ``force=False``.
        ValueError: On invalid ref, missing required source field,
            ``base_image`` on byo, or unknown backend.
        RuntimeError: If a docker subprocess fails (build / pull / inspect).
        FileNotFoundError: If ``.wfc/`` does not exist.
    """
    口 = AutoStep(step_num=1, name="Validate request and select backend")
    project_dir, manifest = _validate_request(
        name, backend, base_image, force, project_dir, allow_reserved
    )

    if backend in ("pixi", "conda"):
        口 = AutoStep(step_num=2, name="Build the image from a pixi or conda source")
        image = _build_image(name, backend, source, base_image, project_dir)
    elif backend == "byo":
        口 = AutoStep(step_num=3, name="Resolve the byo image by probe, then pull")
        image = _resolve_byo_image(source)
    else:
        raise ValueError(
            f"Unknown backend {backend!r}. "
            f"Supported: 'pixi', 'conda', 'byo'."
        )

    口 = AutoStep(step_num=4, name="Write the manifest record")
    record = _write_record(
        name, backend, source, image, manifest, project_dir, python_override
    )

    口 = AutoStep(step_num=5, name="Commit the manifest to git by pathspec")
    _commit_manifest(name, project_dir)
    return record


@task(purpose="Read a pixi or conda env's staged build context back into the "
              "register source payload, only when its lock/list plus "
              "pip-freeze reproduce the record's source_fingerprint",
      inputs="env name, its record, project root",
      outputs="(source payload, None) when the context matches, else "
              "(None, the reason it cannot be rebuilt)")
def load_build_context(
    name: str, record: EnvRecord, project_dir: Path,
) -> tuple[dict | None, str | None]:
    """Read the build context ``register-env`` staged for *name*.

    The context ``.wfc/build/<name>/`` holds the Dockerfile as rendered
    (with any base-image override) and the staged source files. It is
    usable only when its lock or explicit list, the pip-freeze delimiter
    and its pip freeze hash to the record's ``source_fingerprint``: a
    context a later failed ``register-env --force`` overwrote does not.

    Args:
        name: The env name.
        record: The env's record in ``.wfc/envs.json``.
        project_dir: The project root.

    Returns:
        ``(source, None)`` with the register ``source`` payload
        (``pixi_lock_content`` / ``explicit_list_content``,
        ``pixi_toml_content``, ``pip_freeze_content``) when the context
        matches; otherwise ``(None, reason)``.
    """
    import hashlib

    from .packages import PIP_FREEZE_DELIMITER

    lock_file = _STAGED_LOCK.get(record.backend)
    if lock_file is None:
        return None, f"a {record.backend} env has no build context"
    if not record.source_fingerprint:
        return None, "its record has no source_fingerprint to check it against"

    build_dir = layout.env_build_dir(project_dir, name)
    required = ["Dockerfile", lock_file, _STAGED_PIP_FREEZE]
    if record.backend == "pixi":
        required.append(_STAGED_PIXI_MANIFEST)
    missing = [f for f in required if not (build_dir / f).is_file()]
    if missing:
        return None, (f"its build context {build_dir} is missing "
                      f"{', '.join(missing)}")

    # Read back as register staged them: text mode undoes the platform
    # newline translation write_text applied.
    lock_content = (build_dir / lock_file).read_text(encoding="utf-8")
    pip_freeze = (build_dir / _STAGED_PIP_FREEZE).read_text(encoding="utf-8")
    # The same blob and the same md5 _write_record's store_env_content took.
    blob = f"{lock_content}{PIP_FREEZE_DELIMITER}{pip_freeze}"
    if hashlib.md5(blob.encode("utf-8")).hexdigest() != record.source_fingerprint:
        return None, (f"its build context {build_dir} no longer matches the "
                      f"record's source_fingerprint")

    lock_key = ("pixi_lock_content" if record.backend == "pixi"
                else "explicit_list_content")
    source = {lock_key: lock_content, "pip_freeze_content": pip_freeze}
    if record.backend == "pixi":
        source["pixi_toml_content"] = (
            build_dir / _STAGED_PIXI_MANIFEST).read_text(encoding="utf-8")
    return source, None


@workflow(purpose="Rebuild a pixi or conda env's missing image from its "
                  "verified build context, record the new digest with the "
                  "record's python kept, and commit .wfc/envs.json by "
                  "pathspec; never pulls")
def rebuild(
    name: str, record: EnvRecord, source: dict, project_dir: Path,
) -> EnvRecord:
    """Rebuild *name*'s image from the build context ``register-env`` staged.

    The caller has checked the context with :func:`load_build_context`,
    which also returned *source*. The build reruns the staged Dockerfile
    as it was rendered, so a base-image override carries over. The new
    image gets a new digest and ``env_fingerprint``; the record keeps its
    ``python``, ``source`` and ``built_from_lock``.

    Args:
        name: The env name.
        record: The env's current record.
        source: The register ``source`` payload read from the context.
        project_dir: The project root.

    Returns:
        The rewritten :class:`EnvRecord`.

    Raises:
        RuntimeError: If the build or the inspect fails.
    """
    project_dir = Path(project_dir).resolve()
    build_dir = layout.env_build_dir(project_dir, name)
    print(f"  env {name}: image missing from Docker; rebuilding from "
          f"{layout.STATE_DIR_NAME}/build/{name}/")

    口 = AutoStep(step_num=1, name="Build the image and resolve its digest")
    image = _build_and_resolve(name, build_dir, record.source,
                               record.built_from_lock)

    口 = AutoStep(step_num=2, name="Write the manifest record")
    new_record = _write_record(
        name, record.backend, source, image, load_manifest(project_dir),
        project_dir, record.python,
    )

    口 = AutoStep(step_num=3, name="Commit the manifest to git by pathspec")
    _commit_manifest(name, project_dir, action="Rebuild")
    print(f"  env {name}: rebuilt as {new_record.container}")
    return new_record


@task(purpose="Commit .wfc/envs.json by pathspec, so the tree is clean after "
              "registration and nothing else the user staged is swept in",
      inputs="env name, resolved project root",
      outputs="the new commit's SHA, or None (nothing new, or no git repository)")
def _commit_manifest(name: str, project_dir: Path,
                     action: str = "Register") -> str | None:
    """Commit the env manifest a registration or a rebuild just wrote.

    Outside a git repository nothing is committed. The Dockerfile and build
    context under ``.wfc/build/`` are ignored, not committed.

    Args:
        name: The env name (used in the commit message).
        project_dir: The resolved project root.
        action: The commit message's verb, ``"Register"`` or ``"Rebuild"``.

    Returns:
        The new commit's SHA, or ``None`` when nothing was committed.
    """
    from ..version import commit_paths
    sha = commit_paths(
        project_dir, [layout.env_manifest_path(project_dir)],
        f"{action} env {name}", require_repo=False,
    )
    if sha is not None:
        print(f"  git: committed {layout.STATE_DIR_NAME}/"
              f"{layout.ENV_MANIFEST_FILENAME} as {sha[:12]}")
    return sha


def refuse_existing_name(manifest: dict, name: str, force: bool) -> None:
    """Refuse a name the manifest already records, unless ``force`` is set.

    Args:
        manifest: The manifest as loaded by :func:`load_manifest`.
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
        The resolved project root, and the manifest as loaded, which
        :func:`_write_record` updates and writes back.

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
    manifest = load_manifest(project_dir)
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
    """Build a pixi or conda env's image and resolve its digest.

    Args:
        name: The env name; the image is tagged ``local/<name>``.
        backend: ``"pixi"`` or ``"conda"``.
        source: The per-backend payload; see :func:`register`.
        base_image: The optional base-image override.
        project_dir: The resolved project root.

    Returns:
        The ``docker://local/<name>@sha256:<hex>`` image, with the record's
        ``source`` and ``built_from_lock`` values for the backend.

    Raises:
        ValueError: If a staged pixi lock cannot build *name*, or a staged
            conda explicit list targets a platform other than linux-64.
        RuntimeError: If the build or the inspect fails, or the inspect
            returns no digest.
    """
    from . import dockerfiles as df_pkg

    口 = Step(step_num=1, name="Render Dockerfile",
             purpose="Validate a staged pixi.lock against the requested env "
                     "name, lock-format version and linux-64 platform, or a "
                     "staged conda explicit list against linux-64, then "
                     "assemble the "
                     "per-backend generator kwargs and render the Dockerfile "
                     "via dockerfiles.generate_for_backend")
    gen_kwargs: dict = {"env_name": name}
    if base_image is not None:
        gen_kwargs["base_image"] = base_image

    source_field: str | None
    built_from_lock: str | None
    if backend == "pixi":
        # Fail here — before any Dockerfile render, staging, or docker
        # work — when the staged lock cannot build the requested env
        # (name not in the lock's environments, or lock written by a
        # newer pixi than the pinned in-container build tool).
        staged_lock = source.get("pixi_lock_content")
        if staged_lock is not None:
            from .dockerfiles.pixi import validate_lock_for_env
            validate_lock_for_env(staged_lock, name)
        gen_kwargs["pip_freeze_content"] = source.get(
            "pip_freeze_content", ""
        )
        source_field = "pixi.toml"
        built_from_lock = "pixi.lock"
    elif backend == "conda":
        # Fail here, like the pixi lock check, when the staged explicit
        # list was captured on a non-Linux host.
        staged_list = source.get("explicit_list_content")
        if staged_list is not None:
            from .dockerfiles.conda import validate_explicit_list_platform
            validate_explicit_list_platform(staged_list, name)
        gen_kwargs["pip_freeze_content"] = source.get(
            "pip_freeze_content", ""
        )
        source_field = "environment.yml"
        built_from_lock = "conda-lock.yml"

    dockerfile = df_pkg.generate_for_backend(backend, **gen_kwargs)
    if dockerfile is None:  # defensive — only byo returns None
        raise RuntimeError(
            f"Generator for backend {backend!r} returned no Dockerfile."
        )

    口 = Step(step_num=2, name="Write Dockerfile and stage build context",
             purpose="Write the Dockerfile to .wfc/build/<name>/ and stage the "
                     "pip-freeze, pixi, and conda source blobs into the build context")
    build_dir = layout.env_build_dir(project_dir, name)
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

    # Stage source-content blobs into the build context under the
    # filenames the generators emit COPY for. The Dockerfile generators
    # are pure (string in, string out); the register workflow is the one
    # place that knows about disk, so file staging lives here.
    pip_freeze_text = source.get("pip_freeze_content", "")
    if backend in ("pixi", "conda"):
        (build_dir / _STAGED_PIP_FREEZE).write_text(
            pip_freeze_text, encoding="utf-8"
        )
    if backend == "pixi":
        pixi_lock_text = source.get("pixi_lock_content")
        if pixi_lock_text is not None:
            (build_dir / _STAGED_LOCK["pixi"]).write_text(
                pixi_lock_text, encoding="utf-8"
            )
        pixi_toml_text = source.get("pixi_toml_content")
        if pixi_toml_text is not None:
            (build_dir / _STAGED_PIXI_MANIFEST).write_text(
                pixi_toml_text, encoding="utf-8"
            )
    if backend == "conda":
        explicit_list_text = source.get("explicit_list_content")
        if explicit_list_text is not None:
            (build_dir / _STAGED_LOCK["conda"]).write_text(
                explicit_list_text, encoding="utf-8"
            )

    口 = AutoStep(step_num=3, name="Build the image and resolve its digest")
    return _build_and_resolve(name, build_dir, source_field, built_from_lock)


@task(purpose="Build the image from a staged build context, tagged under "
              "local/<name>, and resolve its image ID into the digest-pinned "
              "docker://local/<name>@sha256:<hex> ref")
def _build_and_resolve(
    name: str,
    build_dir: Path,
    source_field: str | None,
    built_from_lock: str | None,
) -> _ResolvedImage:
    """Build a staged build context and resolve the image's digest.

    Registration (:func:`_build_image`) and a rebuild (:func:`rebuild`)
    both end here. Nothing is pulled.

    Args:
        name: The env name; the image is tagged ``local/<name>``.
        build_dir: The staged build context, ``.wfc/build/<name>/``.
        source_field: The value recorded as the env's ``source`` field.
        built_from_lock: The value recorded as ``built_from_lock``.

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
    口 = AutoStep(step_num=1, name="Build the image")
    docker_runner.build(build_dir, build_tag)
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
    # Image part used for env_fingerprint (local namespace, no scheme).
    return _ResolvedImage(
        container_ref=container_ref,
        fingerprint_image=image_name,
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
        RuntimeError: If the pull or the inspect fails, a ``local/`` image
            is not in the daemon, a registry image lists no registry digest
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
    # Probe-then-pull: only pull if not already local.
    try:
        image_id = docker_runner.image_inspect(daemon_ref)
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
        fingerprint_image=image_prefix,
        digest_hex=digest_hex,
        source_field=ref,
        built_from_lock=None,
    )


@task(purpose="Precompute env_fingerprint (and source_fingerprint for any "
              "pixi/conda registration that staged source content) and write "
              "the EnvRecord into .wfc/envs.json")
def _write_record(
    name: str,
    backend: str,
    source: dict,
    image: _ResolvedImage,
    manifest: dict,
    project_dir: Path,
    python_override: str | None,
) -> EnvRecord:
    """Precompute the env's fingerprints and write its record.

    Args:
        name: The env name, the record's key in ``.wfc/envs.json``.
        backend: ``"pixi"``, ``"conda"`` or ``"byo"``.
        source: The per-backend payload; see :func:`register`.
        image: The image the build or resolve step produced.
        manifest: The manifest as :func:`_validate_request` loaded it.
        project_dir: The resolved project root.
        python_override: An explicit container-side interpreter path, or
            ``None`` for the backend's default.

    Returns:
        The persisted :class:`EnvRecord`.
    """
    from ..storage import store_env_content
    from .fingerprint import capture_env_content

    # -------------------------------------------------------------------------
    # env_fingerprint precompute (read back at run time by
    # resolve_env_fingerprint).
    # -------------------------------------------------------------------------
    fp_spec = f"container:{image.fingerprint_image}@sha256:{image.digest_hex}"
    blob = capture_env_content(fp_spec)
    env_fingerprint = store_env_content(blob, project_dir)

    # -------------------------------------------------------------------------
    # source_fingerprint: package-list md5 for EVERY pixi/conda registration
    # that staged source content (live-spec capture AND --from file mode).
    #
    # The blob is assembled directly from the *source* dict that was already
    # staged into the build context above — the FULL lock / explicit-list
    # content, an explicit delimiter, then the pip-freeze content. This is a
    # DISTINCT path from env_fingerprint (the container image-digest blob from
    # capture_env_content): we deliberately do NOT call capture_env_content
    # here, so env_fingerprint / wfc.identity.build_cache_key stay byte-for-byte
    # unchanged.
    #
    # The md5 lets the canvas point its existing
    # ``GET /api/registry/envs/blob/<md5>`` endpoint (and the /packages parser
    # in wfc.environments.packages) at exactly what went into the image. Stays
    # None when no lock or explicit-list was staged, and for byo.
    # -------------------------------------------------------------------------
    source_fingerprint: str | None = None
    if backend in ("pixi", "conda"):
        from .packages import PIP_FREEZE_DELIMITER

        lock_content = (
            source.get("pixi_lock_content")
            if backend == "pixi"
            else source.get("explicit_list_content")
        )
        if lock_content is not None:
            pip_freeze_content = source.get("pip_freeze_content", "")
            sf_blob = f"{lock_content}{PIP_FREEZE_DELIMITER}{pip_freeze_content}"
            source_fingerprint = store_env_content(sf_blob, project_dir)

    # -------------------------------------------------------------------------
    # Manifest write
    # -------------------------------------------------------------------------
    from datetime import datetime
    built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Per-env interpreter path (thin-container dispatch): recorded truth so
    # run-step launches the method script under the env's actual Python.
    # An explicit --python override wins; otherwise the per-backend default
    # (which mirrors what the generator recipe materialized).
    env_python = python_override or default_python_for_backend(backend, name)

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
    envs_block = manifest.get("envs", {})
    envs_block[name] = record.to_dict()
    manifest["envs"] = envs_block
    manifest.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
    save_manifest(project_dir, manifest)
    return record
