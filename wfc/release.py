"""wfc/release.py — Project git: which wfc is installed, and where its releases are published.

Part of the Project git unit, beside ``wfc.version``: where ``wfc.version``
holds the project's own commits, this module holds wfc's own release, read
from the installed distribution and its packaged changelog.

Public functions:
  installed_wfc_version()            The installed wfc version, or None.
  project_url(label)                 One Project-URL of the installed distribution, or None.
  changelog_url()                    The Changelog Project-URL (or the repository's).
  docs_url(version, page)            A guide page in the docs channel of *version*.
  issues_url()                       The Issues Project-URL (or the repository's issues).
  bug_report_body(v, docker)         A bug report's headings and its versions line.
  bug_report_url(v, docker)          The new-issue page prefilled with that body.
  parse_changelog(text)              A changelog's versions, each with its sections.
  changelog_versions(entries, v, s)  The entry for *v*, or every version after *s* up to *v*.
  installed_changelog(since, path)   The installed version's sections, from the packaged file.
  public_changelog_url()             The raw CHANGELOG.md on the repository's main branch.
  includes_prereleases(v, pre)       Whether pre-releases are offered to *v*.
  offered_release(releases, v, pre)  The newest release newer than *v* the install takes.
  install_command(version)           The pip command that installs *version*.
  update_offer(v, check, pre)        The cached offer, read without the network.
  update_check_due(v, check, pre)    Whether the next check would send a request.
  check_for_update(v, check, pre)    The once-a-day check, run in the caller's thread.
  start_update_check(v, check, pre)  The check on a daemon thread, unless one is running.
  poll_update(v, check, pre)         The cached offer at once; the check started when due.
  wait_for_update_check(timeout)     Wait for a running check to end.

Design notes:
  - Depends on the standard library and ``packaging`` only; nothing in wfc
    above Project git. Callers hand it values (a version, a path).
  - The changelog's headings are ``## [<version>] — <date>`` and
    ``### <Heading>``; each section's items are its ``- `` lines, kept as
    markdown. Upgrading comes first and Known issues last, whatever their
    order in the file.
  - The canvas build copies the repository's ``CHANGELOG.md`` into
    ``wfc/canvas/static/dist/``, so an installed wfc carries its own
    changelog. A source checkout has no built canvas: the reader answers the
    Changelog link instead.
  - The update check is the one place wfc sends a request on its own: a GET
    of PyPI's JSON and of the public ``CHANGELOG.md``, 3 s each, User-Agent
    ``wfc/<version>``, through :func:`_http_get` alone. It installs nothing.
    Its answer is kept in one file and asked again after a day; a failed
    request writes nothing, so the next check asks again. The caller hands
    it the ``[updates]`` settings and that file as values (the about route
    passes Layout's per-user ``update_check_path``, under ``~/.wfc/``):
    with ``check`` off it sends nothing and neither reads nor writes the
    file.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import platform
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from axiom_annotations import task
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

#: The distribution whose installed metadata names wfc's version and its
#: project URLs.
_DISTRIBUTION = "workflow-canvas"

#: Used when the metadata names no repository (a source checkout).
_FALLBACK_REPOSITORY_URL = "https://github.com/ddpoe/workflow-canvas"

#: Used when the metadata names no issues page.
_FALLBACK_ISSUES_URL = f"{_FALLBACK_REPOSITORY_URL}/issues"

#: Used when the metadata names no changelog.
_FALLBACK_CHANGELOG_URL = f"{_FALLBACK_REPOSITORY_URL}/blob/main/CHANGELOG.md"

#: The guide's root; a channel (``stable`` or ``latest``) follows it.
_DOCS_ROOT = "https://workflow-canvas.readthedocs.io/en"

#: The changelog the canvas build puts beside the built canvas.
PACKAGED_CHANGELOG = Path(__file__).parent / "canvas" / "static" / "dist" / "CHANGELOG.md"

#: The section a reader needs first, and the one it reads last.
_FIRST_SECTION = "upgrading"
_LAST_SECTION = "known issues"

_VERSION_HEADING = re.compile(r"^##\s+\[(?P<version>[^\]]+)\]\s*(?:[—–-]+\s*(?P<date>.*?))?\s*$")
_SECTION_HEADING = re.compile(r"^###\s+(?P<heading>.+?)\s*$")


@task(purpose="Read the installed wfc version from the workflow-canvas "
              "distribution's metadata; None, with a warning, when it is "
              "missing or unreadable")
def installed_wfc_version() -> str | None:
    """Return the installed wfc version, read from the distribution's metadata.

    An editable install answers the version its metadata was installed with.
    Never raises: a run records the version it ran under, and a missing
    answer must not stop the run.

    Returns:
        The version string of the ``workflow-canvas`` distribution, or None
        (with a logged warning) when its metadata is not installed or cannot
        be read.
    """
    try:
        return importlib.metadata.version(_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        logger.warning(
            "The %s package metadata is not installed; the wfc version is "
            "not recorded.", _DISTRIBUTION,
        )
        return None
    except Exception as exc:  # noqa: BLE001 -- a run must not stop on this
        logger.warning(
            "The %s package metadata could not be read (%s: %s); the wfc "
            "version is not recorded.", _DISTRIBUTION, type(exc).__name__, exc,
        )
        return None


@task(purpose="Read one Project-URL (Issues, Repository, Changelog) of the "
              "installed workflow-canvas distribution; None when the "
              "metadata names none")
def project_url(label: str) -> str | None:
    """Return the installed distribution's Project-URL named *label*.

    Args:
        label: The URL's label, matched without regard to case (``Issues``,
            ``Repository``, ``Changelog``).

    Returns:
        The URL, or None when the metadata is missing, unreadable or names
        no such URL. Never raises.
    """
    try:
        entries = importlib.metadata.metadata(_DISTRIBUTION).get_all("Project-URL") or []
    except Exception:  # noqa: BLE001 -- a missing link must not stop a caller
        return None
    wanted = label.strip().lower()
    for entry in entries:
        name, _, url = entry.partition(",")
        if name.strip().lower() == wanted and url.strip():
            return url.strip()
    return None


@task(purpose="The changelog's public URL: the Changelog Project-URL, else "
              "the repository's CHANGELOG.md")
def changelog_url() -> str:
    """Return where the changelog is published.

    Returns:
        The ``Changelog`` Project-URL, or the repository's ``CHANGELOG.md``
        when the metadata names none.
    """
    return project_url("Changelog") or _FALLBACK_CHANGELOG_URL


@task(purpose="A guide page in the docs channel of a wfc version: stable for "
              "a release, latest for a pre-release or an unreadable version")
def docs_url(version: str | None, page: str = "") -> str:
    """Return a guide page in the docs of *version*'s channel.

    A pre-release (or a version that cannot be read) reads the latest docs,
    a release the stable ones, so the page matches the wfc that links it.

    Args:
        version: The installed wfc version, or ``None``.
        page: The page's path under the channel, e.g.
            ``"how-to/see-your-images-on-github.html"``; ``""`` for the
            guide's front page.

    Returns:
        The page's URL.
    """
    try:
        release = version is not None and not Version(version).is_prerelease
    except InvalidVersion:
        release = False
    channel = "stable" if release else "latest"
    return f"{_DOCS_ROOT}/{channel}/{page}"


@task(purpose="The issues page: the Issues Project-URL, else the repository's "
              "issues page")
def issues_url() -> str:
    """Return where wfc's issues are filed.

    Returns:
        The ``Issues`` Project-URL, or the repository's issues page when the
        metadata names none (a source checkout).
    """
    return project_url("Issues") or _FALLBACK_ISSUES_URL


#: The headings a bug report's body opens with, each followed by a blank
#: line for the user to fill.
_REPORT_HEADINGS = ("What happened", "What I expected", "Steps to reproduce")


@task(purpose="The body of a bug report: three headings for the user to fill, "
              "then one line naming the wfc version, the platform, Python and "
              "Docker; no project path, no user name")
def bug_report_body(version: str | None, docker_version: str | None) -> str:
    """Return the text a new bug report opens with.

    Args:
        version: The installed wfc version, or ``None`` (written ``unknown``).
        docker_version: Docker's server version, or ``None`` when the daemon
            did not answer (written ``not running``). The caller asks
            Environments' probe; this module runs no command.

    Returns:
        Markdown: ``### What happened``, ``### What I expected`` and
        ``### Steps to reproduce``, each with a blank line under it, then
        ``wfc <version> · <platform> · Python <version> · Docker <version>``.
    """
    blocks = [f"### {heading}\n\n" for heading in _REPORT_HEADINGS]
    environment = " · ".join((
        f"wfc {version or 'unknown'}",
        platform.platform(),
        f"Python {platform.python_version()}",
        f"Docker {docker_version or 'not running'}",
    ))
    return "\n".join(blocks) + "\n" + environment + "\n"


@task(purpose="The GitHub new-issue URL Report a bug opens: the issues page's "
              "/new with the report body and an empty title; nothing is sent")
def bug_report_url(version: str | None, docker_version: str | None) -> str:
    """Return the new-issue page, prefilled with the bug report's body.

    The user reads and edits the page before submitting; opening the URL
    sends nothing. The title is left empty for the user to write.

    Args:
        version: The installed wfc version, or ``None``.
        docker_version: Docker's server version, or ``None`` when it did not
            answer.

    Returns:
        ``issues_url()`` + ``/new?body=`` + the URL-encoded
        :func:`bug_report_body`.
    """
    body = bug_report_body(version, docker_version)
    return f"{issues_url()}/new?body={urllib.parse.quote(body, safe='')}"


def _ordered(sections: list[dict]) -> list[dict]:
    """Upgrading first, Known issues last, the rest in the file's order."""
    def rank(section: dict) -> int:
        name = section["heading"].strip().lower()
        if name == _FIRST_SECTION:
            return 0
        if name == _LAST_SECTION:
            return 2
        return 1
    return sorted(sections, key=rank)


@task(purpose="Parse a changelog's text into its released versions, each "
              "with its date and sections (Upgrading first, Known issues "
              "last), items kept as markdown")
def parse_changelog(text: str) -> list[dict]:
    """Parse a Keep-a-Changelog text into its versions.

    A version heading is ``## [<version>] — <date>``; a heading whose label
    is not a version (``[Unreleased]``) and everything under it is left
    out. A section heading is ``### <Heading>``. Each ``- `` line is an
    item, kept as markdown; an indented line continues the item above it.
    A paragraph before a section's first item is its ``intro``.

    Args:
        text: The changelog's text.

    Returns:
        ``[{"version", "date", "sections": [{"heading", "intro",
        "items"}]}]`` in the file's order (newest first in a changelog),
        each version's sections ordered Upgrading first and Known issues
        last.
    """
    entries: list[dict] = []
    entry: dict | None = None
    section: dict | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        version_match = _VERSION_HEADING.match(line)
        if version_match:
            label = version_match.group("version").strip()
            section = None
            try:
                Version(label)
            except InvalidVersion:
                entry = None
                continue
            entry = {"version": label, "date": (version_match.group("date") or "").strip(),
                     "sections": []}
            entries.append(entry)
            continue
        if entry is None:
            continue
        section_match = _SECTION_HEADING.match(line)
        if section_match:
            section = {"heading": section_match.group("heading"), "intro": "", "items": []}
            entry["sections"].append(section)
            continue
        if section is None or not line.strip():
            continue
        if line.startswith("- "):
            section["items"].append(line[2:].strip())
        elif section["items"] and raw[:1].isspace():
            section["items"][-1] += " " + line.strip()
        elif not section["items"]:
            section["intro"] = (section["intro"] + " " + line.strip()).strip()
    for parsed in entries:
        parsed["sections"] = _ordered(parsed["sections"])
    return entries


def _as_version(value: object) -> Version | None:
    """*value* as a version, or None when it is missing or not one."""
    if not isinstance(value, str):
        return None
    try:
        return Version(value)
    except InvalidVersion:
        return None


@task(purpose="Select a changelog's entry for one version, or every version "
              "after an earlier one up to it, newest first")
def changelog_versions(entries: list[dict], version: str,
                       since: str | None = None) -> list[dict]:
    """Return the entries a reader of *version* is shown.

    Args:
        entries: :func:`parse_changelog`'s answer.
        version: The version to show (the installed one).
        since: The version the reader saw last. ``None`` (or a version not
            older than *version*, or not a version) shows *version* alone.

    Returns:
        Every entry newer than *since* and no newer than *version*, newest
        first; with no *since*, the entry for *version*. Empty when
        *version* has no entry or is not a version.
    """
    target = _as_version(version)
    if target is None:
        return []
    floor = _as_version(since)
    if floor is None or floor >= target:
        return [entry for entry in entries if _as_version(entry["version"]) == target][:1]
    chosen = [entry for entry in entries
              if (parsed := _as_version(entry["version"])) is not None
              and floor < parsed <= target]
    if not any(_as_version(entry["version"]) == target for entry in chosen):
        return []
    return sorted(chosen, key=lambda entry: Version(entry["version"]), reverse=True)


@task(purpose="The installed version's changelog sections (or every version "
              "since an earlier one), from the changelog packaged with the "
              "canvas; the Changelog link when it holds no section for the "
              "version or is not there")
def installed_changelog(version: str | None, since: str | None = None,
                        path: Path = PACKAGED_CHANGELOG) -> dict:
    """Return what the packaged changelog says about *version*.

    Args:
        version: The installed wfc version, or None when it is unknown.
        since: The version the reader saw last, for every version since it.
        path: The packaged changelog; the canvas build's copy by default.

    Returns:
        ``{"versions": [...], "link": None}`` with
        :func:`changelog_versions`' entries, or ``{"versions": [], "link":
        <Changelog URL>}`` when the file is missing or unreadable, or holds
        no section for *version*.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"versions": [], "link": changelog_url()}
    versions = changelog_versions(parse_changelog(text), version or "", since)
    if not versions:
        return {"versions": [], "link": changelog_url()}
    return {"versions": versions, "link": None}


# ---------------------------------------------------------------------------
# The once-a-day update check
# ---------------------------------------------------------------------------

#: PyPI's JSON answer for the distribution: every release and its files.
PYPI_JSON_URL = f"https://pypi.org/pypi/{_DISTRIBUTION}/json"

#: The most each request may take (seconds).
REQUEST_TIMEOUT_S = 3

#: How long a check's answer stands before the next check asks again (seconds).
CHECK_INTERVAL_S = 24 * 60 * 60

#: The offered version's sections the canvas shows inline, in this order.
_OFFER_SECTIONS = (_FIRST_SECTION, _LAST_SECTION)

#: How many times a write of the answer is tried when the file is held open.
_WRITE_ATTEMPTS = 3

_GITHUB_REPOSITORY = re.compile(
    r"^https?://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$")

_check_lock = threading.Lock()
_check_thread: threading.Thread | None = None


def _http_get(url: str, *, timeout: float, headers: dict[str, str]) -> tuple[int, bytes]:
    """Send one GET and answer its status and body: the check's only network call.

    Args:
        url: The URL to read.
        timeout: The most the request may take, in seconds.
        headers: The request's headers.

    Returns:
        ``(status, body)``; an HTTP error status answers with an empty body.

    Raises:
        OSError: The machine is offline, the name does not resolve, the
            connection fails or the request times out.
    """
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 -- https only
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code, b""


def _get(url: str, installed: str) -> bytes | None:
    """GET *url* as ``wfc/<installed>``: the body of a 200, else None, never raising."""
    try:
        status, body = _http_get(url, timeout=REQUEST_TIMEOUT_S,
                                 headers={"User-Agent": f"wfc/{installed}"})
    except Exception as exc:  # noqa: BLE001 -- offline is not an error
        logger.debug("The update check could not read %s (%s: %s).",
                     url, type(exc).__name__, exc)
        return None
    if status != 200:
        logger.debug("The update check read %s and got HTTP %s.", url, status)
        return None
    return body


@task(purpose="The raw CHANGELOG.md on the main branch of the repository the "
              "Repository Project-URL names; None when it is not on GitHub")
def public_changelog_url() -> str | None:
    """Return where the update check reads the public changelog.

    Returns:
        ``https://raw.githubusercontent.com/<owner>/<repo>/main/CHANGELOG.md``
        for the Repository Project-URL (the default repository when the
        metadata names none), or None when it is not a GitHub repository.
    """
    repository = project_url("Repository") or _FALLBACK_REPOSITORY_URL
    match = _GITHUB_REPOSITORY.match(repository.strip())
    if match is None:
        return None
    return (f"https://raw.githubusercontent.com/{match.group('owner')}/"
            f"{match.group('repo')}/main/CHANGELOG.md")


def _published_releases(payload: object) -> list[str] | None:
    """The installable releases in PyPI's JSON: every version with a file not yanked.

    None when the answer is not PyPI's shape (no ``releases`` table).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("releases"), dict):
        return None
    published = []
    for label, files in payload["releases"].items():
        if _as_version(label) is None or not isinstance(files, list):
            continue
        if any(isinstance(item, dict) and not item.get("yanked") for item in files):
            published.append(label)
    return published


@task(purpose="Whether the update check offers pre-releases: as the installed "
              "version is one, unless the prereleases setting overrides")
def includes_prereleases(installed: str | None, prereleases: bool | None) -> bool:
    """Say whether pre-releases are offered to the installed wfc.

    Args:
        installed: The installed wfc version; one that is missing or not a
            version counts as a stable install.
        prereleases: ``[updates] prereleases``: True or False decides, None
            follows the installed version.

    Returns:
        True when pre-releases are offered as well as stable releases.
    """
    if prereleases is not None:
        return bool(prereleases)
    current = _as_version(installed)
    return current is not None and current.is_prerelease


@task(purpose="The newest release newer than the installed version, pre-"
              "releases included when the installed version is one unless "
              "the prereleases setting overrides; None when there is none")
def offered_release(releases: list[str], installed: str | None,
                    prereleases: bool | None) -> str | None:
    """Return the release the installed wfc is offered.

    Args:
        releases: The installable releases (none yanked).
        installed: The installed wfc version.
        prereleases: ``[updates] prereleases``: True includes pre-releases,
            False excludes them, None follows the installed version (a
            pre-release install is offered pre-releases, a stable one not).

    Returns:
        The newest release newer than *installed* the rule admits, or None
        (also when *installed* is missing or not a version).
    """
    current = _as_version(installed)
    if current is None:
        return None
    include = includes_prereleases(installed, prereleases)
    newer = [(parsed, label) for label in releases
             if (parsed := _as_version(label)) is not None and parsed > current
             and (include or not parsed.is_prerelease)]
    return max(newer)[1] if newer else None


@task(purpose="The pip command that installs a release: pinned for a pre-"
              "release, an upgrade for a stable one")
def install_command(version: str) -> str:
    """Return the command that installs *version*.

    A pre-release is pinned, since ``pip install -U`` never picks one.

    Args:
        version: The release offered.

    Returns:
        ``pip install workflow-canvas==<version>`` for a pre-release,
        ``pip install -U workflow-canvas`` for a stable release.
    """
    parsed = _as_version(version)
    if parsed is not None and parsed.is_prerelease:
        return f"pip install {_DISTRIBUTION}=={version}"
    return f"pip install -U {_DISTRIBUTION}"


def _read_cache(path: Path) -> dict | None:
    """The cached answer at *path*, or None when it is absent or not the check's shape."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    checked_at, releases = data.get("checked_at"), data.get("releases")
    changelog = data.get("changelog")
    if (not isinstance(checked_at, (int, float)) or not isinstance(releases, list)
            or not all(isinstance(label, str) for label in releases)
            or not (changelog is None or isinstance(changelog, list))):
        return None
    return {"checked_at": float(checked_at), "releases": releases, "changelog": changelog}


def _write_cache(path: Path, data: dict) -> None:
    """Replace the cached answer at *path*; a failed write is left unwritten, never raised."""
    for attempt in range(_WRITE_ATTEMPTS):
        temp: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp = tempfile.mkstemp(prefix=".update-check-", suffix=".json",
                                            dir=path.parent)
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream)
            os.replace(temp, path)
            return
        except OSError as exc:
            if temp is not None:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
            if attempt == _WRITE_ATTEMPTS - 1:
                logger.debug("The update check could not write %s (%s).", path, exc)
                return
            time.sleep(0.05)


def _changelog_entry(changelog: list | None, version: str) -> dict | None:
    """The cached public changelog's entry for *version*, or None."""
    wanted = _as_version(version)
    for entry in changelog or []:
        if isinstance(entry, dict) and _as_version(entry.get("version")) == wanted:
            return entry
    return None


def _offer_from(cache: dict | None, installed: str | None,
                prereleases: bool | None) -> dict | None:
    """The offer a cached answer makes the installed version, or None."""
    if cache is None:
        return None
    version = offered_release(cache["releases"], installed, prereleases)
    if version is None:
        return None
    entry = _changelog_entry(cache["changelog"], version)
    sections = [section for section in (entry or {}).get("sections", [])
                if isinstance(section, dict)
                and str(section.get("heading", "")).strip().lower() in _OFFER_SECTIONS]
    return {
        "version": version,
        "prerelease": Version(version).is_prerelease,
        "command": install_command(version),
        "notes": {"sections": sections, "link": changelog_url()},
    }


def _needed(cache: dict | None, installed: str | None, prereleases: bool | None,
            now: float) -> str | None:
    """What the next check asks: ``"pypi"``, ``"changelog"`` or nothing (None)."""
    if _as_version(installed) is None:
        return None
    if cache is None or not 0 <= now - cache["checked_at"] < CHECK_INTERVAL_S:
        return "pypi"
    version = offered_release(cache["releases"], installed, prereleases)
    if (version is not None and _changelog_entry(cache["changelog"], version) is None
            and public_changelog_url() is not None):
        return "changelog"
    return None


@task(purpose="The offer the cached update-check answer makes the installed "
              "version, read without the network; None with the check off")
def update_offer(installed: str | None, *, check: bool, prereleases: bool | None,
                 path: Path) -> dict | None:
    """Return the offer the last check's answer makes, sending nothing.

    Args:
        installed: The installed wfc version.
        check: ``[updates] check``; False answers None without reading the file.
        prereleases: ``[updates] prereleases`` (None follows the install).
        path: The file keeping the check's answer.

    Returns:
        ``{"version", "prerelease", "command", "notes": {"sections",
        "link"}}`` -- *notes* holds the offered version's Upgrading and
        Known issues when the public changelog was fetched (``[]``
        otherwise) and the link to all its notes -- or None when there is
        no newer release, no answer yet, or the check is off.
    """
    if not check:
        return None
    return _offer_from(_read_cache(path), installed, prereleases)


@task(purpose="Whether the next update check would send a request: no answer "
              "cached, one a day old, or a cached changelog without the "
              "offered version")
def update_check_due(installed: str | None, *, check: bool, prereleases: bool | None,
                     path: Path, now: float | None = None) -> bool:
    """Say whether :func:`check_for_update` would send a request now.

    Args:
        installed: The installed wfc version.
        check: ``[updates] check``; False is never due.
        prereleases: ``[updates] prereleases`` (None follows the install).
        path: The file keeping the check's answer.
        now: The time to judge the answer's age at; the clock by default.

    Returns:
        True when no answer is cached, the cached one is a day old, or it
        offers a version its public changelog has no section for.
    """
    if not check:
        return False
    cache = _read_cache(path)
    moment = time.time() if now is None else now
    return _needed(cache, installed, prereleases, moment) is not None


@task(purpose="The once-a-day update check, in the caller's thread: ask PyPI "
              "and the public changelog when the cached answer is a day old, "
              "cache what was read, answer the offer; nothing sent, read or "
              "written with the check off")
def check_for_update(installed: str | None, *, check: bool, prereleases: bool | None,
                     path: Path, now: float | None = None) -> dict | None:
    """Run the update check and return the offer it makes.

    Asks PyPI's JSON when the cached answer is missing or a day old, then
    the public changelog when that answer makes an offer (so notes added to
    the offered version since are read), and within the day the changelog
    alone when the offered version has no section in the cached one;
    otherwise sends nothing. Each request takes at most 3 s. A failed PyPI
    request (offline, a timeout, a non-200, an unreadable answer) writes
    nothing, so the next check asks again; a failed changelog request keeps
    the cached entries and still caches PyPI's answer. Nothing is raised.

    Args:
        installed: The installed wfc version (the User-Agent's too); with
            none, nothing is sent.
        check: ``[updates] check``. False sends nothing, neither reads nor
            writes the cached answer, and answers None.
        prereleases: ``[updates] prereleases`` (None follows the install).
        path: The file keeping the check's answer, read and replaced here.
        now: The time the check runs at; the clock by default.

    Returns:
        :func:`update_offer`'s answer after the check, or None.
    """
    if not check:
        return None
    moment = time.time() if now is None else now
    cache = _read_cache(path)
    need = _needed(cache, installed, prereleases, moment)
    # _needed asks nothing unless the installed version parses.
    if need is None or installed is None:
        return _offer_from(cache, installed, prereleases)
    changed = False
    if need == "pypi":
        body = _get(PYPI_JSON_URL, installed)
        try:
            releases = _published_releases(json.loads(body)) if body is not None else None
        except (UnicodeDecodeError, ValueError):
            releases = None
        if releases is None:
            return _offer_from(cache, installed, prereleases)
        cache = {"checked_at": moment, "releases": releases,
                 "changelog": cache["changelog"] if cache else None}
        changed = True
        # A refresh that makes an offer reads the changelog again, so notes
        # added to the offered version after its release reach the offer.
        offers = offered_release(releases, installed, prereleases) is not None
        need = "changelog" if offers and public_changelog_url() is not None else None
    changelog_url = public_changelog_url()
    # A changelog read is asked only with a cached answer and a changelog URL.
    if need == "changelog" and cache is not None and changelog_url is not None:
        body = _get(changelog_url, installed)
        try:
            text = body.decode("utf-8") if body is not None else None
        except UnicodeDecodeError:
            text = None
        if text is not None:
            current = Version(installed)
            cache = {**cache, "changelog": [
                entry for entry in parse_changelog(text)
                if Version(entry["version"]) > current]}
            changed = True
    if changed and cache is not None:
        _write_cache(path, cache)
    return _offer_from(cache, installed, prereleases)


def _check_quietly(installed: str | None, prereleases: bool | None, path: Path) -> None:
    """Run the check on its thread; anything it raises is logged, never surfaced."""
    try:
        check_for_update(installed, check=True, prereleases=prereleases, path=path)
    except Exception:  # noqa: BLE001 -- a background check must never fail the canvas
        logger.debug("The update check failed.", exc_info=True)


@task(purpose="Start the update check on a daemon thread unless one is "
              "running; nothing with the check off")
def start_update_check(installed: str | None, *, check: bool, prereleases: bool | None,
                       path: Path) -> bool:
    """Start :func:`check_for_update` in the background.

    At most one check runs at a time; the caller never waits for it.

    Args:
        installed: The installed wfc version.
        check: ``[updates] check``; False starts nothing.
        prereleases: ``[updates] prereleases`` (None follows the install).
        path: The file keeping the check's answer.

    Returns:
        True when a check was started, False when one was already running or
        the check is off.
    """
    global _check_thread
    if not check:
        return False
    with _check_lock:
        if _check_thread is not None and _check_thread.is_alive():
            return False
        thread = threading.Thread(target=_check_quietly, args=(installed, prereleases, path),
                                  name="wfc-update-check", daemon=True)
        _check_thread = thread
        thread.start()
    return True


@task(purpose="Answer the cached update offer at once and start the "
              "background check when it is due")
def poll_update(installed: str | None, *, check: bool, prereleases: bool | None,
                path: Path) -> dict | None:
    """Return the cached offer without waiting, starting the check when due.

    The about route's call: the page asks on load and when it is shown
    again, so a check that is due starts within a second of opening the
    canvas, and its answer is served from the next ask on.

    Args:
        installed: The installed wfc version.
        check: ``[updates] check``; False sends, reads and writes nothing
            and answers None.
        prereleases: ``[updates] prereleases`` (None follows the install).
        path: The file keeping the check's answer.

    Returns:
        :func:`update_offer`'s answer from what is cached now.
    """
    if not check:
        return None
    if update_check_due(installed, check=True, prereleases=prereleases, path=path):
        start_update_check(installed, check=True, prereleases=prereleases, path=path)
    return update_offer(installed, check=True, prereleases=prereleases, path=path)


def wait_for_update_check(timeout: float) -> bool:
    """Wait up to *timeout* seconds for a running update check to end.

    For a caller that must not outlive the check (the test suite's network
    guard, a shutdown).

    Args:
        timeout: The most to wait, in seconds.

    Returns:
        True when no check is running at return.
    """
    thread = _check_thread
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()
