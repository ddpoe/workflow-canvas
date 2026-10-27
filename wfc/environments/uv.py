"""A uv project as an env source: the checks before Docker, and the export.

``wfc register-env <name> --backend uv --from <project dir or its uv.lock>``
builds an image from a uv project's lock. Everything here runs on the host,
before any Docker work:

- uv must be on PATH, and the project must pin its Python in
  ``.python-version`` to a minor wfc has a base image for (3.10 to 3.13);
- the lock is read for the packages the image would install on Linux x86_64
  with that CPython (the dependency graph from the project, its chosen
  extras and groups, with each marker evaluated for that one target), and
  refused when one of them cannot be installed by
  ``uv pip install --require-hashes --no-deps``: a local, editable or
  directory source, a git or URL source with no hash, or a package with
  neither a Linux wheel nor an sdist. A package with only an sdist is a
  warning: the slim base image has no compiler;
- ``uv export`` writes the requirements the image installs. Its stdout is
  the staged ``requirements.txt``, and the groups and extras go into the
  source payload for ``image.json``.

Capturing a live uv venv is not supported: the lock is the source.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import unquote, urlparse

from axiom_annotations import Step, task
from packaging import tags as wheel_tags
from packaging.markers import InvalidMarker, Marker, UndefinedEnvironmentName
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename

from .dockerfiles.bases import PYTHON_BASES

#: The backend name, as ``--backend`` takes it and ``.wfc/envs.json`` records it.
UV_BACKEND = "uv"
#: The Python minors with a digest-pinned ``python:3.X-slim-bookworm`` base.
SUPPORTED_MINORS: tuple[str, ...] = tuple(PYTHON_BASES)
#: The lock file ``--from`` names, or finds in the project directory.
LOCK_FILENAME = "uv.lock"
#: The file that pins the project's Python, written by ``uv python pin``.
PYTHON_VERSION_FILENAME = ".python-version"

#: Register source keys: the exported requirements (the staged
#: ``requirements.txt``), the Python minor, and the chosen groups and extras.
REQUIREMENTS_SOURCE_KEY = "requirements_content"
PYTHON_MINOR_SOURCE_KEY = "uv_python"
GROUPS_SOURCE_KEY = "uv_groups"
EXTRAS_SOURCE_KEY = "uv_extras"

#: Seconds ``uv export`` may take before it is stopped and the register refused.
EXPORT_TIMEOUT_S = 300.0

#: The glibc minor of the base images (Debian bookworm, glibc 2.36): the
#: newest ``manylinux_2_N`` tag they install.
_TARGET_GLIBC_MINOR = 36
#: Source kinds that point at the local filesystem.
_LOCAL_SOURCES = ("path", "directory", "editable", "virtual")
#: A Python version in ``.python-version``: ``3.12``, ``3.12.4``,
#: ``cpython-3.12.4-linux-x86_64-gnu``.
_PYTHON_VERSION = re.compile(r"(?<![\d.])(\d+)\.(\d+)")
#: A free-threaded build in ``.python-version``: ``3.13t``, ``3.13.1t``,
#: ``cpython-3.13.0+freethreaded-...``.
_FREE_THREADED = re.compile(r"\d+\.\d+(?:\.\d+)?t\b|freethreaded")


class UvRefusal(ValueError):
    """A uv project wfc cannot build an image from; the message says why."""


@dataclass(frozen=True)
class UvCapture:
    """What a uv project gives ``register``.

    Attributes:
        source: The register source payload: the exported requirements, an
            empty pip freeze, the Python minor, and the groups and extras.
        warnings: One line per package that has only an sdist for the
            target, and one per default dependency group (other than dev)
            the image will not install.
    """

    source: dict
    warnings: tuple[str, ...]


def export_args(groups: Sequence[str] = (), extras: Sequence[str] = ()) -> list[str]:
    """The ``uv export`` argv, run in the project directory.

    Args:
        groups: The dependency groups to include (``--group``), in order.
        extras: The project's extras to include (``--extra``), in order.

    Returns:
        ``uv export --frozen --format requirements-txt --no-emit-project
        --no-emit-workspace --no-dev --no-default-groups`` followed by one
        ``--group`` per group and one ``--extra`` per extra. With
        ``--no-default-groups`` a group is installed only when named, so the
        groups ``image.json`` records are the groups the image installs.
    """
    args = ["uv", "export", "--frozen", "--format", "requirements-txt",
            "--no-emit-project", "--no-emit-workspace", "--no-dev",
            "--no-default-groups"]
    for group in groups:
        args += ["--group", group]
    for extra in extras:
        args += ["--extra", extra]
    return args


def _read_text(path: Path) -> str:
    """Read a project file, or refuse naming it when it cannot be read.

    Raises:
        UvRefusal: *path* cannot be read (a permission error, a directory)
            or is not UTF-8 text.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise UvRefusal(f"cannot read {path}: {reason}") from exc


def unchosen_default_groups(project_dir: Path, groups: Sequence[str]) -> list[str]:
    """The default dependency groups, other than dev, the image will not install.

    ``[tool.uv] default-groups`` in ``pyproject.toml`` (a list, or ``"all"``
    for every group in ``[dependency-groups]``) names the groups ``uv sync``
    installs by default. The export runs with ``--no-default-groups``, so
    only the groups named with ``--group`` are installed. A missing or
    unreadable file or key counts as no default groups.

    Args:
        project_dir: The uv project directory.
        groups: The groups named with ``--group``.

    Returns:
        Each default group that is neither ``dev`` nor named, in file order.
    """
    try:
        data = tomllib.loads((project_dir / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tool_uv = (data.get("tool") or {}).get("uv") if isinstance(data.get("tool"), dict) else None
    defaults = tool_uv.get("default-groups") if isinstance(tool_uv, dict) else None
    if defaults == "all":
        declared = data.get("dependency-groups")
        defaults = list(declared) if isinstance(declared, dict) else []
    if not isinstance(defaults, list):
        return []
    named = {canonicalize_name(g) for g in groups} | {"dev"}
    return [g for g in defaults
            if isinstance(g, str) and canonicalize_name(g) not in named]


def project_paths(from_path: Path) -> tuple[Path, Path]:
    """Resolve ``--from`` to the project directory and its lock.

    Args:
        from_path: A uv project directory, or its ``uv.lock``.

    Returns:
        ``(project_dir, lock_path)``.

    Raises:
        FileNotFoundError: *from_path* does not exist.
        UvRefusal: *from_path* is another file, or the project has no
            ``uv.lock``.
    """
    if not from_path.exists():
        raise FileNotFoundError(f"--from path does not exist: {from_path}")
    if from_path.is_dir():
        project_dir = from_path
    elif from_path.name == LOCK_FILENAME:
        project_dir = from_path.parent
    else:
        raise UvRefusal(
            f"--from for --backend uv takes a uv project directory or its "
            f"{LOCK_FILENAME}, not {from_path}.")
    lock_path = project_dir / LOCK_FILENAME
    if not lock_path.exists():
        raise UvRefusal(
            f"{project_dir} has no {LOCK_FILENAME}; run `uv lock` there first.")
    return project_dir, lock_path


def python_minor(project_dir: Path) -> str:
    """The Python minor the project pins in ``.python-version``.

    Args:
        project_dir: The uv project directory.

    Returns:
        The minor, e.g. ``"3.12"``.

    Raises:
        UvRefusal: The file is missing or unreadable, names no Python 3
            version, names another implementation (PyPy) or a free-threaded
            build (``3.13t``), or names a minor outside
            :data:`SUPPORTED_MINORS`.
    """
    supported = ", ".join(SUPPORTED_MINORS)
    cpython = f"CPython {SUPPORTED_MINORS[0]}-{SUPPORTED_MINORS[-1]}"
    path = project_dir / PYTHON_VERSION_FILENAME
    if not path.exists():
        raise UvRefusal(
            f"{project_dir} has no {PYTHON_VERSION_FILENAME}: the image's "
            f"Python is the minor it pins. Run `uv python pin 3.X` there "
            f"(one of {supported}).")
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lowered = line.lower()
        # uv spells CPython ``cpython`` or ``cp`` (``cp3.12``,
        # ``cpython@3.12``); any other name is another implementation.
        implementation = re.match(r"[a-z]+", lowered)
        if ((implementation and implementation.group() not in ("cp", "cpython"))
                or _FREE_THREADED.search(lowered)):
            raise UvRefusal(
                f"{path} pins {line!r}; wfc's uv images run stock (GIL) "
                f"{cpython} on python:3.X-slim-bookworm. Run `uv python pin "
                f"3.X` with one of {supported}, then `uv lock`.")
        match = _PYTHON_VERSION.search(line)
        if match is None or match.group(1) != "3":
            break
        minor = f"3.{match.group(2)}"
        if minor not in SUPPORTED_MINORS:
            raise UvRefusal(
                f"{path} pins Python {minor}, which has no wfc base image "
                f"(supported: {supported}). Run `uv python pin 3.X` with one "
                f"of those, then `uv lock`.")
        return minor
    raise UvRefusal(
        f"{path} names no Python 3 version; run `uv python pin 3.X` "
        f"(one of {supported}).")


def target_environment(minor: str) -> dict[str, str]:
    """The marker environment of the image: Linux x86_64, CPython *minor*.

    Args:
        minor: The Python minor, e.g. ``"3.12"``.

    Returns:
        A full PEP 508 marker environment (``extra`` aside).
    """
    full = f"{minor}.0"
    return {
        "implementation_name": "cpython",
        "implementation_version": full,
        "os_name": "posix",
        "platform_machine": "x86_64",
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": "Linux",
        "platform_version": "",
        "python_full_version": full,
        "python_version": minor,
        "sys_platform": "linux",
    }


@cache
def _target_tags(minor: str) -> frozenset[wheel_tags.Tag]:
    """The wheel tags CPython *minor* on glibc 2.36 x86_64 installs."""
    major_s, minor_s = minor.split(".")
    version = (int(major_s), int(minor_s))
    interpreter = f"cp{major_s}{minor_s}"
    platforms = [f"manylinux_2_{glibc}_x86_64"
                 for glibc in range(_TARGET_GLIBC_MINOR, 4, -1)]
    platforms += ["manylinux2014_x86_64", "manylinux2010_x86_64",
                  "manylinux1_x86_64", "linux_x86_64"]
    supported = set(wheel_tags.cpython_tags(version, abis=[interpreter],
                                            platforms=platforms))
    supported |= set(wheel_tags.compatible_tags(version, interpreter, platforms))
    return frozenset(supported)


def marker_applies(marker: str | None, environment: dict[str, str]) -> bool:
    """Whether a dependency's environment marker holds in *environment*.

    A marker that cannot be read or evaluated counts as holding: the guard
    then checks the package rather than skipping it.

    Args:
        marker: The PEP 508 marker text, or None/empty for no marker.
        environment: The marker environment, as :func:`target_environment`
            builds it.

    Returns:
        True when there is no marker, it holds, or it cannot be evaluated.
    """
    if not marker:
        return True
    try:
        return Marker(marker).evaluate(environment)
    except (InvalidMarker, UndefinedEnvironmentName):
        return True


def _is_project_root(package: dict) -> bool:
    """Whether *package* is the project at the lock's own directory."""
    source = package.get("source") or {}
    return any(source.get(kind) == "." for kind in _LOCAL_SOURCES)


def _edge_targets(edge: dict, by_name: dict[str, list[dict]]) -> list[dict]:
    """The locked packages a dependency edge points at.

    A lock that forks by marker holds a name more than once; its edges then
    carry the version (and source) they mean.
    """
    candidates = by_name.get(canonicalize_name(edge["name"]), [])
    if "version" in edge:
        candidates = [p for p in candidates if p.get("version") == edge["version"]]
    if "source" in edge:
        candidates = [p for p in candidates if p.get("source") == edge["source"]]
    return candidates


def applicable_packages(lock: dict, minor: str, *, groups: Sequence[str] = (),
                        extras: Sequence[str] = ()) -> list[dict]:
    """The locked packages the image installs: the dependency graph for one target.

    The walk starts at the project (the package whose source is ``.``; in a
    virtual workspace, at every member), takes its dependencies plus the
    chosen *extras* (``optional-dependencies``) and *groups*
    (``dev-dependencies``; only the named ones, as the export runs with
    ``--no-default-groups``; ``--group dev`` is refused before this, since
    ``--no-dev`` drops it), and follows each edge whose marker holds on Linux x86_64 with
    CPython *minor*, activating the extras an edge names. Workspace members
    and the project itself are walked through but not returned:
    ``--no-emit-project --no-emit-workspace`` leaves them out.

    Args:
        lock: The parsed ``uv.lock``.
        minor: The image's Python minor.
        groups: The chosen dependency groups.
        extras: The project's chosen extras.

    Returns:
        The reachable packages, each once, in walk order.

    Raises:
        UvRefusal: The lock names no project to start from.
    """
    packages: list[dict] = list(lock.get("package", []))
    by_name: dict[str, list[dict]] = {}
    for package in packages:
        by_name.setdefault(canonicalize_name(package["name"]), []).append(package)
    members = {canonicalize_name(m)
               for m in (lock.get("manifest") or {}).get("members", [])}
    roots = [p for p in packages if _is_project_root(p)]
    if not roots:
        roots = [p for p in packages if canonicalize_name(p["name"]) in members]
    if not roots:
        raise UvRefusal(
            f"{LOCK_FILENAME} names no project at its own directory "
            f"(a package with source `.`); is --from the project's root?")
    workspace = members | {canonicalize_name(p["name"]) for p in roots}
    environment = target_environment(minor)

    reached: list[dict] = []
    seen: set[int] = set()
    activated: set[tuple[int, str]] = set()
    pending: list[dict] = []
    for root in roots:
        seen.add(id(root))
        pending.extend(root.get("dependencies", []))
        for extra in extras:
            activated.add((id(root), extra))
            pending.extend((root.get("optional-dependencies") or {}).get(extra, []))
        for group in groups:
            pending.extend((root.get("dev-dependencies") or {}).get(group, []))
    while pending:
        edge = pending.pop(0)
        if not marker_applies(edge.get("marker"), environment):
            continue
        for target in _edge_targets(edge, by_name):
            if id(target) not in seen:
                seen.add(id(target))
                if canonicalize_name(target["name"]) not in workspace:
                    reached.append(target)
                pending.extend(target.get("dependencies", []))
            for extra in edge.get("extra", []):
                if (id(target), extra) not in activated:
                    activated.add((id(target), extra))
                    pending.extend(
                        (target.get("optional-dependencies") or {}).get(extra, []))
    return reached


def _has_hash(package: dict) -> bool:
    """Whether any artifact of *package* (its sdist or a wheel) has a hash."""
    sdist = package.get("sdist") or {}
    if sdist.get("hash"):
        return True
    return any(wheel.get("hash") for wheel in package.get("wheels", []))


def _wheel_filename(wheel: dict, source: dict) -> str:
    """A locked wheel's file name: its URL's, its path's, or its source URL's."""
    for location in (wheel.get("url"), wheel.get("path"), wheel.get("filename"),
                     source.get("url")):
        if location:
            return unquote(Path(urlparse(location).path).name)
    return ""


def _has_linux_wheel(package: dict, minor: str) -> bool:
    """Whether a wheel of *package* installs on Linux x86_64 with CPython *minor*."""
    source = package.get("source") or {}
    supported = _target_tags(minor)
    for wheel in package.get("wheels", []):
        try:
            _, _, _, tags = parse_wheel_filename(_wheel_filename(wheel, source))
        except InvalidWheelFilename:
            continue
        if tags & supported:
            return True
    return False


def check_lock(lock_text: str, minor: str, *, groups: Sequence[str] = (),
               extras: Sequence[str] = ()) -> tuple[str, ...]:
    """Refuse a lock the image cannot install; return the sdist-only warnings.

    Every package :func:`applicable_packages` reaches is checked, and every
    refusal is reported at once, one line per package.

    Args:
        lock_text: The ``uv.lock`` text.
        minor: The image's Python minor.
        groups: The chosen dependency groups.
        extras: The project's chosen extras.

    Returns:
        One warning per package that has an sdist but no Linux wheel.

    Raises:
        UvRefusal: A package has a local, editable or directory source; a
            git or URL source with no hash; or neither a Linux wheel nor an
            sdist. The message names each one.
    """
    try:
        lock = tomllib.loads(lock_text)
    except tomllib.TOMLDecodeError as exc:
        raise UvRefusal(f"{LOCK_FILENAME} is not valid TOML: {exc}") from exc
    target = f"Linux x86_64 with CPython {minor}"
    refusals: list[str] = []
    warnings: list[str] = []
    for package in applicable_packages(lock, minor, groups=groups, extras=extras):
        name = package["name"]
        version = package.get("version", "")
        label = f"{name} {version}".strip()
        source = package.get("source") or {}
        local = next((kind for kind in _LOCAL_SOURCES if kind in source), None)
        if local is not None:
            refusals.append(
                f"{label} is a local {local} dependency ({source[local]}); the "
                f"image installs only what the lock can download. Depend on a "
                f"published version instead.")
        elif "git" in source or not _has_hash(package):
            where = source.get("git") or source.get("url") or "its source"
            refusals.append(
                f"{label} comes from {where} with no hash, which "
                f"`uv pip install --require-hashes` cannot install. Depend on "
                f"a published version instead.")
        elif not _has_linux_wheel(package, minor):
            if package.get("sdist"):
                warnings.append(
                    f"{label} has no wheel for {target}, only an sdist; the "
                    f"image (python:{minor}-slim-bookworm) has no compiler, so "
                    f"it builds only if the package is pure Python.")
            else:
                refusals.append(
                    f"{label} has no wheel for {target} and no sdist, so the "
                    f"image cannot install it.")
    if refusals:
        raise UvRefusal(
            f"{LOCK_FILENAME} has packages the image cannot install:\n  "
            + "\n  ".join(refusals))
    return tuple(warnings)


def _unhashed_requirements(requirements: str) -> list[str]:
    r"""The requirements in an export that carry no ``--hash``.

    Continuation lines (a trailing ``\\``) are joined first; comments, blank
    lines and option lines (``--index-url``) are skipped. An editable
    requirement is reported whole (``-e ./lib``), any other by its first
    token.
    """
    entries: list[str] = []
    current = ""
    for raw in requirements.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            line = ""
        current = f"{current} {line}".strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        if current:
            entries.append(current)
        current = ""
    if current:
        entries.append(current)
    unhashed: list[str] = []
    for entry in entries:
        if "--hash=" in entry:
            continue
        if entry.startswith("-e"):
            unhashed.append(entry)
        elif not entry.startswith("-"):
            unhashed.append(entry.split()[0])
    return unhashed


def export_requirements(project_dir: Path, *, groups: Sequence[str] = (),
                        extras: Sequence[str] = ()) -> str:
    """Run ``uv export`` in *project_dir* and return its stdout.

    Args:
        project_dir: The uv project directory.
        groups: The chosen dependency groups.
        extras: The project's chosen extras.

    Returns:
        The requirements-txt text, every requirement pinned and hashed.

    Raises:
        FileNotFoundError: ``uv`` is not on PATH.
        RuntimeError: ``uv export`` exits nonzero.
    """
    args = export_args(groups, extras)
    try:
        result = subprocess.run(args, cwd=project_dir, capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=EXPORT_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"uv executable not found on PATH — cannot export {project_dir}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"`{' '.join(args)}` did not finish within {EXPORT_TIMEOUT_S:g} s "
            f"in {project_dir}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"`{' '.join(args)}` failed in {project_dir} "
            f"(exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


@task(purpose="Check a uv project before any Docker work (uv on PATH, the "
              ".python-version minor, the lock's sources and Linux wheels for "
              "the chosen groups and extras) and export its requirements",
      inputs="--from (a uv project directory or its uv.lock), the groups and "
             "extras",
      outputs="UvCapture: the register source payload and the sdist-only "
              "warnings")
def capture_project(from_path: Path, *, groups: Sequence[str] = (),
                    extras: Sequence[str] = ()) -> UvCapture:
    """Check a uv project and export what its image installs.

    Args:
        from_path: ``--from``: the project directory or its ``uv.lock``.
        groups: ``--group``: the dependency groups to include.
        extras: ``--extra``: the project's extras to include.

    Returns:
        The source payload and the warnings.

    Raises:
        UvRefusal: uv is not on PATH, or the project or its lock cannot be
            built (see :func:`python_minor`, :func:`check_lock`), or the
            export holds a requirement with no hash.
        FileNotFoundError: *from_path* does not exist.
        RuntimeError: ``uv export`` failed.
    """
    口 = Step(step_num=1, name="uv on PATH",
             purpose="The export runs uv on this machine; refuse first without it")
    if shutil.which("uv") is None:
        raise UvRefusal(
            "uv is not on PATH: --backend uv runs `uv export` on this machine. "
            "Install uv (https://docs.astral.sh/uv/) and try again.")

    口 = Step(step_num=2, name="Project, lock and Python minor",
             purpose="Resolve --from to the project and its uv.lock, and read "
                     "the minor .python-version pins")
    project_dir, lock_path = project_paths(from_path)
    minor = python_minor(project_dir)

    口 = Step(step_num=3, name="Lock guards",
             purpose="Refuse a package the image cannot install on Linux x86_64 "
                     "with that CPython; collect the sdist-only warnings and "
                     "one per default group the image will not install")
    warnings = check_lock(_read_text(lock_path), minor,
                          groups=groups, extras=extras)
    warnings += tuple(
        f"pyproject.toml makes {group!r} a default dependency group, which "
        f"the image does not install: it installs only the groups named with "
        f"--group. Add `--group {group}` to install it."
        for group in unchosen_default_groups(project_dir, groups))

    口 = Step(step_num=4, name="Export",
             purpose="uv export --no-dev --no-default-groups with the named "
                     "groups and extras: the requirements.txt the image "
                     "installs")
    requirements = export_requirements(project_dir, groups=groups, extras=extras)
    unhashed = _unhashed_requirements(requirements)
    if unhashed:
        raise UvRefusal(
            "uv export wrote requirements with no hash, which "
            "`uv pip install --require-hashes` cannot install: "
            + ", ".join(unhashed))

    from .backends import STAGED_PIP_FREEZE

    source = {
        REQUIREMENTS_SOURCE_KEY: requirements,
        STAGED_PIP_FREEZE.source_key: "",
        PYTHON_MINOR_SOURCE_KEY: minor,
        GROUPS_SOURCE_KEY: list(groups),
        EXTRAS_SOURCE_KEY: list(extras),
    }
    return UvCapture(source=source, warnings=warnings)
