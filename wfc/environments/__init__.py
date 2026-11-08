"""Environments & containers: a project's container envs and the images they run.

An env is a record in ``.wfc/envs.json`` naming a digest-pinned image.
This package builds env images, keeps the records, and assembles the
container command lines that run from them.

- ``manifest``: :class:`EnvRecord` and the record API (:func:`load_manifest`,
  :func:`save_manifest`, :func:`list_envs`, :func:`get`, :func:`delete`),
  plus :func:`env_record_for_spec`, which resolves a ``Method.env`` spec
  to its record, and :func:`load_manifest_for_register`, the register
  path's read, which also opens an older (version 1 or 2) manifest to
  convert it.
- ``build``: :func:`register`, the workflow that renders a backend's
  Dockerfile and builds the image (or resolves a byo image's digest), then
  writes the captures folder and the record, pushes the image when the
  project sets a registry, and commits.
- ``captures``: each image's captures folder,
  ``.wfc/captures/<env>/<env_fingerprint>/``, written once by register
  (``packages.lock``, the Dockerfile, ``image.json``), and the reads
  callers ask by ``(env, fingerprint)``: :func:`image_info` (the image's
  backend and interpreter), :func:`capture_exists`, :func:`has_package_list`,
  and :func:`verified_package_list` (the env's current package list, refused
  with a :class:`CaptureError` when it no longer matches the record), and
  :func:`capture_remedy` (the way on from a folder changed outside wfc:
  register the env again under a new name).
- ``runnable``: the run-time rule (:func:`ensure_version_runnable`, and
  :func:`ensure_runnable` for an env's current record): an image on this
  machine runs as it is; a missing one is pulled by its pushed or byo
  registry digest and its content checked against the record; otherwise it
  is refused with :class:`EnvNotRunnableError`. Nothing is built.
  :func:`version_run_ref` is the no-network half dispatch hands Docker.
- ``registry``: the registry side of env images: the base a GitHub remote
  proposes and the source-label URL (:func:`github_repository`), the push
  tag ``fp-<12 hex>`` and the names a pushed image carries
  (:func:`push_target`, :func:`registry_ref`, :func:`pullable_ref`),
  :func:`probe_registry`, and
  :func:`image_state` (local, pushed, byo registry, missing or unknown,
  read from the captures folder and the daemon, never the registry), the
  canvas's Registry chip (:func:`registry_chip`, said in the chip's words
  by :func:`chip_label` and :func:`chip_notes` for ``wfc list-envs`` and
  ``show-env``), the one push :func:`push_image`, and ``wfc push-env``'s
  body
  (:func:`plan_push`, then :func:`apply_push`).
- ``gh``: the ``gh`` CLI reads ``wfc share`` makes: the user ``gh`` is
  logged in as (:func:`gh_login`), the scopes its login holds
  (:func:`gh_scopes`) and the repository a pushed image's GHCR package is
  connected to (:func:`package_connection`).
- ``dockerfiles``: the per-backend Dockerfile generators and their
  digest-pinned base images.
- ``docker``: the ``docker build``, ``docker image inspect`` and
  ``docker pull`` wrappers, among them ``image_content``, the inspect the
  env fingerprint is rendered from, and :func:`save_image` (``docker save``,
  the image copy a rerun bundle carries) with :func:`local_image_ref` (the
  first of several references the daemon holds).
- ``argv``: the ``docker run`` and ``apptainer exec`` argv builders
  (:func:`build_docker_command`, :func:`build_apptainer_command`),
  :func:`accepts_read_only_binds` (each builder's read-only-bind
  declaration), :func:`daemon_ref` (the image reference Docker is handed for a record)
  and :func:`strip_docker_scheme`.
- ``bind_check``: the launch's container bind check (:func:`check_binds`,
  over the host side :func:`host_bind_expectations` measures): one short
  container from the launch's image, with a step's binds, says whether it
  sees the project's config file and the cache (:class:`BindCheck`:
  :data:`BIND_VISIBLE`, :data:`BIND_EMPTY` naming the bind, or
  :data:`BIND_NOT_RUN`).
- ``containers``: the containers wfc starts: their labels
  (:func:`container_labels`: :data:`PROJECT_LABEL`, a short hash of the
  project root from :func:`project_label`, then :data:`PIPELINE_LABEL` and
  :data:`RUN_LABEL` for a step; the copy-out's project and creator labels,
  :func:`wfc.environments.containers.copy_out_labels`), and listing and stopping them by label
  (:func:`list_containers`, :func:`stop_containers`, bounded and
  best-effort: a :class:`ContainerStop`).
- ``build_clients``: the env builds whose client still runs with no wfc
  process behind it, found from the process table (an
  :class:`OrphanedBuild`), and ending one: an interrupt to its client, a
  bounded wait, then its process tree (a :class:`BuildStop`).
- ``leftovers``: what an earlier crash left, for this project only:
  ``wfc prune-envs``'s :func:`plan_prune` (a :class:`PrunePlan` of
  :class:`LeftoverContainer` and :class:`OrphanedBuild` items to stop, the
  containers wfc made that never finished to remove, plus
  the env images nothing uses and the build cache's size, counted and kept;
  it words itself and writes nothing) and :func:`apply_prune` (each item
  re-checked, then stopped: a container by a kill, an unfinished one by a
  plain remove, never forced, an orphaned build
  through ``build_clients``; a :class:`PruneResult`), and
  :func:`leftover_counts` (the same numbers, a :class:`LeftoverCounts`;
  stop-only, for ``list-envs`` and ``wfc doctor``, it counts just what a
  prune would stop or remove, with no image listing and no disk-usage query;
  :func:`format_size` writes the cache size). The caller passes the run liveness, the project's pipeline ids and
  the method versions recording each env.
- ``interpreter``: the container-side interpreter a method script runs
  under: a version's own image's (:func:`version_interpreter`, asked by
  ``(env, fingerprint)``) and each backend's default
  (:func:`default_python_for_backend`).
- ``fingerprint``: an env's content blob, rendered from the image's layers,
  ``Env``, ``User`` and ``WorkingDir`` (:func:`capture_env_content`), and
  the fingerprint a run records for its env (:func:`resolve_env_fingerprint`).
- ``introspect``: live capture's ``conda list --explicit`` and ``pip freeze``.
- ``packages``: parsing a captures folder's ``packages.lock`` into its
  packages (:func:`parse_packages`, split on :data:`PIP_FREEZE_DELIMITER`).
- ``host``: the host resolvers live capture uses (``resolve_python_for_env``,
  ``resolve_conda_env_dir``).
- ``check``: the registration-time env check (:func:`check_method_env`).
- ``dev_loop``: ``wfc shell``, ``wfc exec`` and ``wfc jupyter`` in an env's
  container.
- ``verbs``: the ``wfc list-envs``, ``show-env``, ``delete-env`` and
  ``register-env`` bodies.

Every path comes from ``wfc.layout``; the env-spec grammar and the
container-ref shape are ``wfc.contracts``'.
"""

from .argv import (
    accepts_read_only_binds,
    build_apptainer_command,
    build_docker_command,
    daemon_ref,
    strip_docker_scheme,
)
from .bind_check import (
    BIND_EMPTY,
    BIND_NOT_RUN,
    BIND_VISIBLE,
    BindCheck,
    BindExpectations,
    check_binds,
    host_bind_expectations,
)
from .build import register
from .build_clients import BuildStop, OrphanedBuild
from .captures import (
    CaptureError,
    CaptureMismatchError,
    CaptureMissingError,
    ImageInfo,
    capture_exists,
    capture_files,
    capture_remedy,
    has_package_list,
    image_info,
    verified_package_list,
)
from .check import check_method_env
from .containers import (
    PIPELINE_LABEL,
    PROJECT_LABEL,
    RUN_LABEL,
    ContainerQueryError,
    ContainerStop,
    container_labels,
    list_containers,
    project_label,
    stop_containers,
)
from .docker import local_image_ref, save_image
from .fingerprint import capture_env_content, resolve_env_fingerprint
from .gh import (
    CONNECT_REPOSITORY_STEPS,
    MANAGE_ACCESS_SECTION,
    PACKAGE_CONNECTED,
    PACKAGE_NOT_FOUND,
    PACKAGE_OTHER_REPOSITORY,
    PACKAGE_UNCONNECTED,
    PACKAGE_UNKNOWN,
    GhLogin,
    GhScopes,
    PackageConnection,
    gh_login,
    gh_scopes,
    package_connection,
    package_name,
    package_page_url,
    package_settings_url,
)
from .interpreter import default_python_for_backend, version_bin_dir, version_interpreter
from .leftovers import (
    LeftoverContainer,
    LeftoverCounts,
    PrunePlan,
    PruneResult,
    apply_prune,
    format_size,
    leftover_counts,
    plan_prune,
)
from .manifest import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    EnvRecord,
    delete,
    env_record_for_spec,
    get,
    list_envs,
    load_manifest,
    load_manifest_for_register,
    recreate_command,
    save_manifest,
)
from .packages import PIP_FREEZE_DELIMITER, parse_packages
from .registry import (
    IMAGE_BYO_REGISTRY,
    IMAGE_ELSEWHERE,
    IMAGE_LOCAL,
    IMAGE_MISSING,
    IMAGE_PUSHED,
    IMAGE_UNKNOWN,
    GitHubRepository,
    ImageState,
    PushItem,
    PushPlan,
    PushRefused,
    PushResult,
    apply_push,
    check_base,
    chip_label,
    chip_notes,
    github_repository,
    image_repository,
    image_state,
    plan_push,
    probe_registry,
    project_github_repository,
    pullable_ref,
    push_image,
    push_name_problem,
    push_tag,
    push_target,
    pushable_envs,
    registry_base,
    registry_chip,
    registry_host,
    registry_ref,
    unpushable_lines,
)
from .runnable import (
    EnvNotRunnableError,
    ensure_runnable,
    ensure_version_runnable,
    version_run_ref,
)

__all__ = [
    "BIND_EMPTY",
    "BIND_NOT_RUN",
    "BIND_VISIBLE",
    "CONNECT_REPOSITORY_STEPS",
    "IMAGE_BYO_REGISTRY",
    "IMAGE_ELSEWHERE",
    "IMAGE_LOCAL",
    "IMAGE_MISSING",
    "IMAGE_PUSHED",
    "IMAGE_UNKNOWN",
    "MANAGE_ACCESS_SECTION",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "PACKAGE_CONNECTED",
    "PACKAGE_NOT_FOUND",
    "PACKAGE_OTHER_REPOSITORY",
    "PACKAGE_UNCONNECTED",
    "PACKAGE_UNKNOWN",
    "PIPELINE_LABEL",
    "PIP_FREEZE_DELIMITER",
    "PROJECT_LABEL",
    "RUN_LABEL",
    "BindCheck",
    "BindExpectations",
    "BuildStop",
    "CaptureError",
    "CaptureMismatchError",
    "CaptureMissingError",
    "ContainerQueryError",
    "ContainerStop",
    "EnvNotRunnableError",
    "EnvRecord",
    "GhLogin",
    "GhScopes",
    "GitHubRepository",
    "ImageInfo",
    "ImageState",
    "LeftoverContainer",
    "LeftoverCounts",
    "OrphanedBuild",
    "PackageConnection",
    "PrunePlan",
    "PruneResult",
    "PushItem",
    "PushPlan",
    "PushRefused",
    "PushResult",
    "accepts_read_only_binds",
    "apply_prune",
    "apply_push",
    "build_apptainer_command",
    "build_docker_command",
    "capture_env_content",
    "capture_exists",
    "capture_files",
    "capture_remedy",
    "check_base",
    "check_binds",
    "check_method_env",
    "chip_label",
    "chip_notes",
    "container_labels",
    "daemon_ref",
    "default_python_for_backend",
    "delete",
    "ensure_runnable",
    "ensure_version_runnable",
    "env_record_for_spec",
    "format_size",
    "get",
    "gh_login",
    "gh_scopes",
    "github_repository",
    "has_package_list",
    "host_bind_expectations",
    "image_info",
    "image_repository",
    "image_state",
    "leftover_counts",
    "list_containers",
    "list_envs",
    "load_manifest",
    "load_manifest_for_register",
    "local_image_ref",
    "package_connection",
    "package_name",
    "package_page_url",
    "package_settings_url",
    "parse_packages",
    "plan_prune",
    "plan_push",
    "probe_registry",
    "project_github_repository",
    "project_label",
    "pullable_ref",
    "push_image",
    "push_name_problem",
    "push_tag",
    "push_target",
    "pushable_envs",
    "recreate_command",
    "register",
    "registry_base",
    "registry_chip",
    "registry_host",
    "registry_ref",
    "resolve_env_fingerprint",
    "save_image",
    "save_manifest",
    "stop_containers",
    "strip_docker_scheme",
    "unpushable_lines",
    "verified_package_list",
    "version_bin_dir",
    "version_interpreter",
    "version_run_ref",
]
