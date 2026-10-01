"""Environments & containers: a project's container envs and the images they run.

An env is a record in ``.wfc/envs.json`` naming a digest-pinned image.
This package builds env images, keeps the records, and assembles the
container command lines that run from them.

- ``manifest``: :class:`EnvRecord` and the record API (:func:`load_manifest`,
  :func:`save_manifest`, :func:`list_envs`, :func:`get`, :func:`delete`),
  plus :func:`env_record_for_spec`, which resolves a ``Method.env`` spec
  to its record.
- ``build``: :func:`register`, the workflow that renders a backend's
  Dockerfile and builds the image (or resolves a byo image's digest), then
  writes the record; and the rebuild of a missing image from its staged
  build context.
- ``runnable``: :func:`ensure_runnable`, the image reference Docker is
  handed for an env once the daemon is known to hold it (a missing local
  image is rebuilt, or refused with :class:`EnvNotRunnableError`).
- ``dockerfiles``: the per-backend Dockerfile generators and their
  digest-pinned base images.
- ``docker``: the ``docker build``, ``docker image inspect`` and
  ``docker pull`` wrappers.
- ``argv``: the ``docker run`` and ``apptainer exec`` argv builders
  (:func:`build_docker_command`, :func:`build_apptainer_command`),
  :func:`daemon_ref` (the image reference Docker is handed for a record)
  and :func:`strip_docker_scheme`.
- ``interpreter``: the container-side interpreter a method script runs
  under (:func:`resolve_env_python`, :func:`default_python_for_backend`).
- ``fingerprint``: an env's content blob (:func:`capture_env_content`) and
  the fingerprint a run records for its env (:func:`resolve_env_fingerprint`).
- ``introspect``: live capture's ``conda list --explicit`` and ``pip freeze``.
- ``packages``: the source blob's package list (:func:`parse_packages`,
  split on :data:`PIP_FREEZE_DELIMITER`).
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
    build_apptainer_command,
    build_docker_command,
    daemon_ref,
    strip_docker_scheme,
)
from .build import register
from .check import check_method_env
from .fingerprint import capture_env_content, resolve_env_fingerprint
from .interpreter import default_python_for_backend, resolve_env_python
from .manifest import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    EnvRecord,
    delete,
    env_record_for_spec,
    get,
    list_envs,
    load_manifest,
    save_manifest,
)
from .packages import PIP_FREEZE_DELIMITER, parse_packages
from .runnable import EnvNotRunnableError, ensure_runnable

__all__ = [
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "PIP_FREEZE_DELIMITER",
    "EnvNotRunnableError",
    "EnvRecord",
    "build_apptainer_command",
    "build_docker_command",
    "capture_env_content",
    "check_method_env",
    "daemon_ref",
    "default_python_for_backend",
    "delete",
    "ensure_runnable",
    "env_record_for_spec",
    "get",
    "list_envs",
    "load_manifest",
    "parse_packages",
    "register",
    "resolve_env_fingerprint",
    "resolve_env_python",
    "save_manifest",
    "strip_docker_scheme",
]
