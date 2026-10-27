"""The backend table: one row per env backend, read by every backend switch.

A backend is how wfc gets an env's image: ``pixi``, ``conda`` and ``uv``
render a Dockerfile from a staged lock, explicit list or exported
``requirements.txt`` and build it; ``byo`` takes an image someone else
built. Every place that behaves differently per
backend reads its row here instead of naming backends itself:

- the Dockerfile generator (:func:`wfc.environments.dockerfiles.generate_for_backend`)
  and the digest-pinned bases it builds on;
- the default container interpreter
  (:func:`wfc.environments.interpreter.default_python_for_backend`);
- the build-context files ``register`` stages and records in the image's
  captures folder;
- the lock-section package parser (:func:`wfc.environments.packages.parse_packages`);
- which typed spec prefixes capture a live env, and the ``--backend``
  choices of ``wfc register-env``.

Adding a backend is adding a row, plus the generator, parser and host
capture the row points at.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import uv as uv_source
from .dockerfiles import byo, conda, pixi, uv
from .dockerfiles.bases import MICROMAMBA_BASE, PIXI_BASE, PYTHON_BASES, UV_IMAGE


@dataclass(frozen=True)
class StagedFile:
    """A source file ``register`` writes into the build context.

    Attributes:
        filename: The file's name in ``.wfc/build/<env>/``, the name the
            backend's Dockerfile COPYs.
        source_key: The key of the register ``source`` payload that holds
            the file's text.
    """

    filename: str
    source_key: str


@dataclass(frozen=True)
class Backend:
    """One backend's answers to every per-backend question.

    Attributes:
        name: The backend name recorded in ``.wfc/envs.json``.
        generate: The Dockerfile generator; returns ``None`` when the
            backend has no Dockerfile.
        pinned_bases: The digest-pinned images its builds start from
            (empty when wfc builds nothing).
        default_python: Maps an env name to the container interpreter path
            recorded when no ``--interpreter`` is given.
        builds_image: Whether ``register`` builds the image from a staged
            build context (otherwise it resolves an existing image).
        live_spec: Whether a ``<name>:<env>`` typed spec captures a live
            env of this backend.
        staged_lock: The lock or explicit list staged into the build
            context, whose text heads the package list; ``None`` when
            nothing is staged.
        staged_extras: Other staged files the build needs, kept in the
            image's captures folder.
        recorded_source: The record's ``source`` field for a built image.
        recorded_lock: The record's ``built_from_lock`` field for a built
            image.
        validate_source: Refuses a source payload the build cannot use,
            before any Docker work; ``None`` when nothing is checked.
        parse_lock: Parses the lock section of the package list, given the
            image's Python minor (``None`` when ``image.json`` records
            none) and the env's name (``None`` when not known), into
            ``{"name", "version", "source"}`` dicts; ``None`` when the
            backend has no lock section.
        generator_args: Maps a source payload to the generator arguments
            it adds beyond the env name, pip freeze and base override (uv's
            Python minor); ``None`` when there are none.
        select_base: Maps a source payload to the pinned base its build
            starts from (uv's minor picks one); ``None`` means the first of
            ``pinned_bases``.
    """

    name: str
    generate: Callable[..., str | None]
    pinned_bases: tuple[str, ...]
    default_python: Callable[[str], str]
    builds_image: bool
    live_spec: bool
    staged_lock: StagedFile | None = None
    staged_extras: tuple[StagedFile, ...] = field(default_factory=tuple)
    recorded_source: str | None = None
    recorded_lock: str | None = None
    validate_source: Callable[[dict, str], None] | None = None
    parse_lock: Callable[[str, str | None, str | None], list[dict]] | None = None
    generator_args: Callable[[dict], dict] | None = None
    select_base: Callable[[dict], str] | None = None

    def default_base(self, source: dict) -> str | None:
        """Return the pinned base a build of *source* starts from.

        Args:
            source: The register source payload.

        Returns:
            The base :attr:`select_base` picks, else the first pinned base;
            ``None`` for a backend that builds nothing.
        """
        if self.select_base is not None:
            return self.select_base(source)
        return self.pinned_bases[0] if self.pinned_bases else None


def _validate_pixi_source(source: dict, env_name: str) -> None:
    """Refuse a staged pixi.lock that cannot build *env_name*."""
    lock = source.get("pixi_lock_content")
    if lock is not None:
        pixi.validate_lock_for_env(lock, env_name)


def _validate_conda_source(source: dict, env_name: str) -> None:
    """Refuse a staged explicit list captured for a platform other than linux-64.

    A live capture from a host that is not linux-64 is tagged to be solved
    for linux-64 (:data:`wfc.environments.introspect.SOLVE_SOURCE_KEY`) and
    is not checked: the image's own list is read back after the build. A
    list given with ``--from`` carries no tag and is still refused.
    """
    from .introspect import SOLVE_SOURCE_KEY

    if SOLVE_SOURCE_KEY in source:
        return
    explicit_list = source.get("explicit_list_content")
    if explicit_list is not None:
        conda.validate_explicit_list_platform(explicit_list, env_name)


def _parse_pixi(text: str, python_minor: str | None,
                env_name: str | None) -> list[dict]:
    """Parse a pixi.lock lock section for the env's pixi environment.

    See :mod:`wfc.environments.packages`.
    """
    from .packages import _parse_pixi_lock
    return _parse_pixi_lock(text, env_name)


def _parse_conda(text: str, python_minor: str | None,
                 env_name: str | None) -> list[dict]:
    """Parse a conda explicit-list lock section (see :mod:`wfc.environments.packages`)."""
    from .packages import _parse_conda_explicit
    return _parse_conda_explicit(text)


def _parse_uv(text: str, python_minor: str | None,
              env_name: str | None) -> list[dict]:
    """Parse a uv requirements-txt lock section for the image's minor.

    See :mod:`wfc.environments.packages`.
    """
    from .packages import _parse_uv_requirements
    return _parse_uv_requirements(text, python_minor)


def _uv_generator_args(source: dict) -> dict:
    """The uv generator's Python minor, from the source payload."""
    return {"python_minor": source[uv_source.PYTHON_MINOR_SOURCE_KEY]}


def _uv_base(source: dict) -> str:
    """The pinned Python base for the source payload's minor."""
    return uv.base_for_minor(source[uv_source.PYTHON_MINOR_SOURCE_KEY])


def _conda_python(env_name: str) -> str:
    """The micromamba base env's interpreter; the env name does not move it."""
    return conda.CONDA_ENV_PYTHON


def _bare_python(env_name: str) -> str:
    """Bare ``python``, resolved by the image's PATH."""
    return "python"


#: The pip freeze every built image stages beside its lock (empty when
#: nothing was pip-installed).
STAGED_PIP_FREEZE = StagedFile("pip-freeze.txt", "pip_freeze_content")

BACKENDS: dict[str, Backend] = {
    row.name: row
    for row in (
        Backend(
            name="pixi",
            generate=pixi.generate,
            pinned_bases=(PIXI_BASE,),
            default_python=pixi.env_python_path,
            builds_image=True,
            live_spec=True,
            staged_lock=StagedFile("pixi.lock", "pixi_lock_content"),
            staged_extras=(StagedFile("pixi.toml", "pixi_toml_content"),),
            recorded_source="pixi.toml",
            recorded_lock="pixi.lock",
            validate_source=_validate_pixi_source,
            parse_lock=_parse_pixi,
        ),
        Backend(
            name="conda",
            generate=conda.generate,
            pinned_bases=(MICROMAMBA_BASE,),
            default_python=_conda_python,
            builds_image=True,
            live_spec=True,
            staged_lock=StagedFile("explicit-list.txt", "explicit_list_content"),
            recorded_source="environment.yml",
            recorded_lock="conda-lock.yml",
            validate_source=_validate_conda_source,
            parse_lock=_parse_conda,
        ),
        Backend(
            name=uv_source.UV_BACKEND,
            generate=uv.generate,
            pinned_bases=(*PYTHON_BASES.values(), UV_IMAGE),
            default_python=uv.venv_python,
            builds_image=True,
            live_spec=False,
            staged_lock=StagedFile(uv.REQUIREMENTS_FILENAME,
                                   uv_source.REQUIREMENTS_SOURCE_KEY),
            recorded_source="pyproject.toml",
            recorded_lock="uv.lock",
            parse_lock=_parse_uv,
            generator_args=_uv_generator_args,
            select_base=_uv_base,
        ),
        Backend(
            name="byo",
            generate=byo.generate,
            pinned_bases=(),
            default_python=_bare_python,
            builds_image=False,
            live_spec=False,
        ),
    )
}

#: The backend names, in table order: ``wfc register-env --backend``'s choices.
BACKEND_NAMES: tuple[str, ...] = tuple(BACKENDS)


def backend_row(name: str) -> Backend:
    """Return the table row for backend *name*.

    Args:
        name: A backend name.

    Returns:
        The backend's :class:`Backend` row.

    Raises:
        ValueError: *name* is not in the table; the message lists the
            backends that are.
    """
    row = BACKENDS.get(name)
    if row is None:
        supported = ", ".join(repr(n) for n in BACKEND_NAMES)
        raise ValueError(f"Unknown backend {name!r}. Supported: {supported}.")
    return row
