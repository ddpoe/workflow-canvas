"""The GitHub CLI reads ``wfc share`` makes about pushed env images.

An image pushed to GHCR is a package under the repository owner's account,
and a collaborator can pull it only once the package is connected to the
repository (or made public). Whether it is connected is a question for
GitHub's API, asked here through the ``gh`` CLI with the user's own ``gh``
login:

- :func:`gh_login`: whether ``gh`` is installed and logged in, and the
  GitHub user it is logged in as (the ``-u`` of the ``docker login`` line
  ``wfc share`` prints);
- :func:`package_name` and :func:`package_page_url`: the GHCR package an
  env's images are pushed to, and its page on GitHub, where it is connected
  to a repository;
- :func:`package_connection`: the repository a package is connected to;
- :func:`gh_scopes`: the scopes ``gh``'s login holds, so a package read that
  fails for want of a packages scope is named as that.

Every call goes through :func:`_run_gh`, the one subprocess boundary. No
token is ever read or printed: ``gh auth token`` is never run here, only
printed for the user to run (``gh auth status`` prints the token masked).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote

from .registry import GHCR_HOST, image_repository, registry_host

#: Seconds a ``gh`` call may take before it counts as not answering.
GH_TIMEOUT_S: Final[float] = 30.0

#: The answers :func:`package_connection` gives.
PACKAGE_CONNECTED: Final[str] = "connected"
PACKAGE_OTHER_REPOSITORY: Final[str] = "other-repository"
PACKAGE_UNCONNECTED: Final[str] = "unconnected"
PACKAGE_NOT_FOUND: Final[str] = "not-found"
PACKAGE_UNKNOWN: Final[str] = "unknown"

#: The host ``gh``'s login is asked about.
GITHUB_HOST: Final[str] = "github.com"


def _run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``gh`` with *args*; the one place a ``gh`` process starts.

    Args:
        args: The arguments after ``gh``.

    Returns:
        The completed process, its output captured as text.

    Raises:
        OSError: ``gh`` is not installed (``FileNotFoundError``) or could
            not be started.
        subprocess.TimeoutExpired: ``gh`` did not answer in time.
    """
    return subprocess.run(["gh", *args], capture_output=True, text=True,
                          timeout=GH_TIMEOUT_S, check=False)


@dataclass(frozen=True)
class GhLogin:
    """Whether the ``gh`` CLI can answer for the user.

    Attributes:
        installed: ``gh`` could be started.
        user: The GitHub login ``gh`` is logged in as, or ``None`` when it
            is not logged in (or not installed).
        detail: ``gh``'s own words when it is not usable; else ``""``.
    """

    installed: bool
    user: str | None = None
    detail: str = ""

    @property
    def ready(self) -> bool:
        """Whether ``gh`` is installed and logged in."""
        return self.installed and bool(self.user)


def gh_login() -> GhLogin:
    """Ask ``gh`` who it is logged in as (``gh api user``).

    One API read, which fails when ``gh`` has no login. Never raises.

    Returns:
        The :class:`GhLogin`.
    """
    try:
        proc = _run_gh(["api", "user", "--jq", ".login"])
    except (OSError, subprocess.SubprocessError) as exc:
        return GhLogin(installed=False, detail=str(exc))
    user = proc.stdout.strip()
    if proc.returncode != 0 or not user:
        said = " ".join((proc.stderr or proc.stdout).split()) or "no output"
        return GhLogin(installed=True, detail=said)
    return GhLogin(installed=True, user=user)


#: The scopes that let a classic token read packages (``write:`` includes read).
PACKAGE_READ_SCOPES: Final[frozenset[str]] = frozenset(
    {"read:packages", "write:packages"})

_SCOPES_LINE = re.compile(r"Token scopes:\s*(.*)$")


@dataclass(frozen=True)
class GhScopes:
    """The scopes ``gh``'s GitHub login holds, as ``gh auth status`` reports them.

    Attributes:
        scopes: The classic OAuth scopes, or ``None`` when ``gh`` named none
            (not logged in, no scopes line, ``none``, or a token kind that
            has no scopes, such as a fine-grained token).
    """

    scopes: frozenset[str] | None = None

    @property
    def lacks_packages(self) -> bool:
        """Whether the login is known to hold no scope that reads packages."""
        return self.scopes is not None and not (self.scopes & PACKAGE_READ_SCOPES)


def _active_scopes(status: str) -> frozenset[str] | None:
    """Read the active github.com account's token scopes from ``gh auth status``.

    ``gh auth status`` prints one block per account (``Logged in to ...``),
    each with a ``Token scopes:`` line (``'gist', 'read:org', 'repo'``, or
    unquoted in older ``gh``). The block marked ``Active account: true`` is
    read; a single block is read whether or not it is marked.

    Args:
        status: ``gh auth status``'s output (stdout and stderr together).

    Returns:
        The scopes, or ``None`` when no block names a non-empty list.
    """
    # One [active, scopes line] per account block, in order.
    blocks: list[tuple[bool, str | None]] = []
    for line in status.splitlines():
        if "Logged in to" in line:
            blocks.append((False, None))
        elif blocks and "Active account: true" in line:
            blocks[-1] = (True, blocks[-1][1])
        elif blocks and (match := _SCOPES_LINE.search(line)):
            blocks[-1] = (blocks[-1][0], match.group(1))
    active = [said for is_active, said in blocks if is_active]
    said = active[0] if active else (blocks[0][1] if len(blocks) == 1 else None)
    if said is None:
        return None
    scopes = frozenset(part.strip().strip("'\"") for part in said.split(",")) - {""}
    if not scopes or scopes == {"none"}:
        return None
    return scopes


def gh_scopes() -> GhScopes:
    """Ask ``gh`` which scopes its github.com login holds (``gh auth status``).

    ``gh auth status`` prints the token masked; it is never printed here.
    Never raises.

    Returns:
        The :class:`GhScopes`; ``scopes`` is ``None`` when ``gh`` did not
        name them.
    """
    try:
        proc = _run_gh(["auth", "status", "--hostname", GITHUB_HOST])
    except (OSError, subprocess.SubprocessError):
        return GhScopes()
    return GhScopes(_active_scopes(f"{proc.stdout}\n{proc.stderr}"))


def package_name(base: str, env: str) -> tuple[str, str] | None:
    """Return ``(owner, package)`` for the GHCR package *env*'s images go to.

    ``ghcr.io/<owner>/<repo>/<env>`` is the package ``<repo>/<env>`` of
    ``<owner>``.

    Args:
        base: The registry base.
        env: The env name.

    Returns:
        The owner and the package name, or ``None`` when the base is not
        on GHCR.
    """
    if registry_host(base).lower() != GHCR_HOST:
        return None
    parts = image_repository(base, env).split("/")
    return parts[1], "/".join(parts[2:])


#: Where a package page offers to connect an unconnected package: the box
#: and its button, as GitHub names them.
CONNECT_REPOSITORY_STEPS: Final[str] = (
    '"Link this package to a repository" > "Connect Repository"')


def package_page_url(owner: str, package: str, *, org: bool = False) -> str:
    """Return a package's page on GitHub, where it is connected to a repository.

    An unconnected package's page ends with the box named in
    :data:`CONNECT_REPOSITORY_STEPS`; its settings page has no such control.

    Args:
        owner: The account owning the package.
        package: The package name (``<repo>/<env>``).
        org: Whether *owner* is an organisation.

    Returns:
        ``https://github.com/users/<owner>/packages/container/package/<package>``
        (``/orgs/`` for an organisation), the package name URL-encoded.
    """
    kind = "orgs" if org else "users"
    return (f"https://github.com/{kind}/{owner}/packages/container/package/"
            f"{quote(package, safe='')}")


@dataclass(frozen=True)
class PackageConnection:
    """The repository a GHCR package is connected to, as ``gh`` answered.

    Attributes:
        owner: The account owning the package.
        package: The package name.
        state: :data:`PACKAGE_CONNECTED` (to the expected repository),
            :data:`PACKAGE_OTHER_REPOSITORY`, :data:`PACKAGE_UNCONNECTED`,
            :data:`PACKAGE_NOT_FOUND` (``gh`` cannot see it) or
            :data:`PACKAGE_UNKNOWN` (``gh`` did not answer).
        repository: The connected repository (``owner/repo``), when any.
        page_url: The package's page (:func:`package_page_url`).
        detail: ``gh``'s own words for ``unknown``; else ``""``.
    """

    owner: str
    package: str
    state: str
    repository: str | None = None
    page_url: str = ""
    detail: str = ""


def package_connection(owner: str, package: str,
                       expected: str | None) -> PackageConnection:
    """Ask GitHub, through ``gh api``, which repository a package is connected to.

    The package is looked up under the user account, then under the
    organisation of that name. Never raises.

    Args:
        owner: The account owning the package.
        package: The package name (``<repo>/<env>``).
        expected: The repository it should be connected to
            (``owner/repo``), or ``None`` when any connection will do.

    Returns:
        The :class:`PackageConnection`.
    """
    encoded = quote(package, safe="")
    for org in (False, True):
        kind = "orgs" if org else "users"
        url = package_page_url(owner, package, org=org)
        try:
            proc = _run_gh(["api", f"/{kind}/{owner}/packages/container/{encoded}",
                            "--jq", ".repository.full_name // \"\""])
        except (OSError, subprocess.SubprocessError) as exc:
            return PackageConnection(owner, package, PACKAGE_UNKNOWN,
                                     page_url=url, detail=str(exc))
        if proc.returncode != 0:
            said = " ".join((proc.stderr or proc.stdout).split())
            if "404" in said or "Not Found" in said:
                continue
            return PackageConnection(owner, package, PACKAGE_UNKNOWN,
                                     page_url=url, detail=said or "no output")
        repository = proc.stdout.strip()
        if not repository or repository == "null":
            return PackageConnection(owner, package, PACKAGE_UNCONNECTED,
                                     page_url=url)
        connected = expected is None or repository.lower() == expected.lower()
        return PackageConnection(
            owner, package,
            PACKAGE_CONNECTED if connected else PACKAGE_OTHER_REPOSITORY,
            repository=repository, page_url=url)
    return PackageConnection(owner, package, PACKAGE_NOT_FOUND,
                             page_url=package_page_url(owner, package))
