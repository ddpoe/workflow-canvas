"""The methods that use an env.

``wfc delete-env``'s refusal reads every version that records the env
(:func:`versions_recording_env`), ``register-env --force``'s listing the
methods whose newest version uses it and its interpreter check every version
with the image it recorded (:func:`version_fingerprints_recording_env`), and
``wfc demo --remove``'s warning the method rows that name it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from axiom_annotations import task
from sqlmodel import col, select

from .. import layout
from ..contracts import parse_env_spec
from ..persistence import Method, Module, get_session
from ..persistence import project_root as get_project_root
from .method_version import newest_entry, read_version_list

logger = logging.getLogger(__name__)


@task(purpose="List every version of every method that records an env, read "
              "from the committed version lists, so delete-env refuses while "
              "any version still needs the env; an unreadable list raises",
      inputs="an env name, the project root",
      outputs="'module/method vN' for each version recording the env")
def versions_recording_env(env_name: str, root: Path | None = None) -> list[str]:
    """Return ``module/method vN`` for every version whose entry records *env_name*.

    The version lists (``.wfc/registry/methods/<module>/<method>.versions.json``)
    are enumerated from the tree, so no database is needed and a fresh clone
    answers the same. Every entry counts, not only the newest: an older
    version can still be pinned and run. An entry's env is read through the
    env-spec grammar's read side, so a legacy ``container:<name>`` spelling
    counts as ``<name>``. A value that is not an env name makes the list
    unreadable: it could name this env, so it fails closed.

    Args:
        env_name: The bare env name.
        root: The project root; defaults to the resolved project root.

    Returns:
        The references, sorted by module, method and version.

    Raises:
        ValueError: A version list cannot be read, or one of its entries
            records an env value that is not an env name; the message names
            its file.
    """
    found = sorted((module, method, entry.label)
                   for module, method, entry in _entries_recording_env(env_name, root))
    return [f"{module}/{method} v{label}" for module, method, label in found]


@task(purpose="List every version of every method that records an env, with "
              "the env fingerprint each recorded, read from the committed "
              "version lists, so register-env refuses to change the "
              "interpreter of an image a version runs; an unreadable list raises",
      inputs="an env name, the project root",
      outputs="('module/method vN', env_fingerprint) for each version recording the env")
def version_fingerprints_recording_env(
        env_name: str, root: Path | None = None) -> list[tuple[str, str]]:
    """Return ``(module/method vN, env_fingerprint)`` for every version recording *env_name*.

    The same versions as :func:`versions_recording_env`, read the same way
    and failing closed the same way, each with the image it recorded.

    Args:
        env_name: The bare env name.
        root: The project root; defaults to the resolved project root.

    Returns:
        The pairs, sorted by module, method and version.

    Raises:
        ValueError: A version list cannot be read, or one of its entries
            records an env value that is not an env name; the message names
            its file.
    """
    found = sorted((module, method, entry.label, entry.env_fingerprint)
                   for module, method, entry in _entries_recording_env(env_name, root))
    return [(f"{module}/{method} v{label}", fingerprint)
            for module, method, label, fingerprint in found]


def _entries_recording_env(env_name: str, root: Path | None):
    """Yield ``(module, method, entry)`` for every version entry recording *env_name*.

    Args:
        env_name: The bare env name.
        root: The project root; defaults to the resolved project root.

    Yields:
        The module name, the method name and the
        :class:`~wfc.registration.method_version.VersionEntry`.

    Raises:
        ValueError: A version list cannot be read, or an entry's env value
            is not an env name.
    """
    root = Path(root) if root is not None else get_project_root()
    methods_root = layout.methods_dir(root)
    if not methods_root.is_dir():
        return
    for path in sorted(methods_root.glob(f"*/*{layout.METHOD_VERSIONS_SUFFIX}")):
        module = path.parent.name
        method = path.name[: -len(layout.METHOD_VERSIONS_SUFFIX)]
        try:
            entries = read_version_list(root, module, method) or []
        except OSError as exc:
            raise ValueError(
                f"The version list of method '{module}/{method}' at {path} "
                f"cannot be read ({exc})."
            ) from exc
        for entry in entries:
            try:
                recorded = parse_env_spec(entry.env_name)
            except ValueError as exc:
                raise ValueError(
                    f"The version list of method '{module}/{method}' at {path} "
                    f"cannot be read: v{entry.label} records env "
                    f"{entry.env_name!r}, which is not an env name ({exc})."
                ) from exc
            if recorded == env_name:
                yield module, method, entry


def methods_referencing_env(env_name: str) -> list[str]:
    """Return ``module.name/method.name`` for every Method whose env names *env_name*.

    Stored rows are read through the env-spec grammar's read side, so a row
    stored under the bare name and one stored under the legacy prefixed
    spelling both count; a row whose value is not a name is skipped (logged
    at debug level). ``wfc demo --remove`` reads it for its shared-env
    warning.

    Args:
        env_name: The bare env name.

    Returns:
        The ``module/method`` references, in row order.

    Raises:
        Whatever the session raises when the registry cannot be read.
    """
    references: list[str] = []
    with get_session() as session:
        rows = session.exec(
            select(Method, Module).join(Module, col(Method.module_id) == col(Module.id))
        ).all()
        for method, module in rows:
            try:
                stored = parse_env_spec(method.env)
            except ValueError as exc:
                logger.debug(
                    "env reference query skips %s/%s: stored env %r is not an env spec (%s)",
                    module.name, method.name, method.env, exc,
                )
                continue
            if stored == env_name:
                references.append(f"{module.name}/{method.name}")
    return references


def methods_whose_newest_version_uses_env(env_name: str,
                                          root: Path | None = None) -> list[str]:
    """Return ``module/method`` for every method whose newest version records *env_name*.

    An env is used by the methods whose newest version records it: that is
    the version an unpinned node runs, so rebuilding the env changes what
    those methods run next. The answer is read from each registered
    method's version list (the newest entry's ``env_name``); a method with
    no readable list is skipped (logged at debug level). Environments
    receives the answer as a value; it never imports Registration.

    Args:
        env_name: The bare env name.
        root: The project root; defaults to the resolved project root.

    Returns:
        The ``module/method`` references, sorted.
    """
    root = Path(root) if root is not None else get_project_root()
    with get_session() as session:
        names = [(module.name, method.name) for method, module in session.exec(
            select(Method, Module).join(Module, col(Method.module_id) == col(Module.id))
        ).all()]
    references: list[str] = []
    for module, method in names:
        try:
            newest = newest_entry(read_version_list(root, module, method))
        except ValueError as exc:
            logger.debug("env reference query skips %s/%s: %s", module, method, exc)
            continue
        if newest is None:
            logger.debug("env reference query skips %s/%s: no version list",
                         module, method)
            continue
        if newest.env_name == env_name:
            references.append(f"{module}/{method}")
    return sorted(references)
