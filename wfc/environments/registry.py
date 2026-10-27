"""The registry side of env images: where they are pushed, under what name.

A project pushes its env images under one registry base (``[registry] url``
in ``wf-canvas.toml``). This module owns the rules about that base and the
images in it:

- the base a GitHub remote proposes, ``ghcr.io/<owner>/<repo>`` lowercased,
  and the repository URL an image's source label names
  (:func:`github_repository`, :func:`project_github_repository`);
- the names a pushed image carries: the tag ``fp-<first 12 hex of the env
  fingerprint>`` (:func:`push_tag`), the repository ``<base>/<env>``
  (:func:`image_repository`), the push target (:func:`push_target`) and the
  digest-pinned ref a pushed image is pulled by (:func:`registry_ref`);
- :func:`probe_registry`, the one question asked of the registry itself,
  through the Docker CLI with Docker's own credentials;
- :func:`image_state`, where one image is: on this machine, recorded as
  pushed, a byo image in its own registry, missing, or unknown when Docker
  does not answer. It reads the image's captures folder and the local
  daemon only, never the registry;
- :func:`pullable_ref`, the digest-pinned ref an image is pulled by at run
  time (a pushed record's, or a byo registry image's own), read from the
  captures folder and the config alone;
- :func:`push_image`, the one push (tag, push, digest read, ``pushed.json``)
  that ``register`` and ``wfc push-env`` share, and ``push-env``'s body:
  :func:`plan_push` states the base it sets and the images it pushes before
  anything is written, :func:`apply_push` pushes them and commits the
  records (and, on a first run, ``[registry] url``) in one commit.

Credentials never pass through this module: Docker's config and credential
helpers are never read, and no token is taken.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from axiom_annotations import AutoStep, Step, task, workflow

from .. import layout
from . import docker as docker_runner
from .argv import _LOCAL_REPOSITORY_PREFIX, daemon_ref, strip_docker_scheme
from .captures import (
    CaptureError,
    CaptureMissingError,
    PushedRecord,
    image_info,
    pushed_record,
    write_pushed,
)

#: The registry a GitHub remote's images are pushed to.
GHCR_HOST: Final[str] = "ghcr.io"

#: The hosts a GitHub remote names: HTTPS and SSH, and SSH over port 443.
_GITHUB_HOSTS: Final[frozenset[str]] = frozenset(
    {"github.com", "www.github.com", "ssh.github.com"})

#: The URL schemes a git remote may use for GitHub.
_URL_SCHEMES: Final[frozenset[str]] = frozenset(
    {"https", "http", "ssh", "git", "git+ssh", "ssh+git"})

#: A GitHub account name and a repository name.
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: scp-like SSH: ``[user@]host:path``.
_SCP_RE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>[^\s]+)$")

#: The prefix of every tag a pushed env image carries.
PUSH_TAG_PREFIX: Final[str] = "fp-"
#: How many hex characters of the env fingerprint the tag keeps.
PUSH_TAG_HEX: Final[int] = 12

_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{32}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: The tag :func:`probe_registry` asks for when no pushed ref is given: one
#: that need not exist, since "no such manifest" proves the registry answered.
PROBE_REPOSITORY: Final[str] = "wfc-probe"
PROBE_TAG: Final[str] = PUSH_TAG_PREFIX + "0" * PUSH_TAG_HEX

#: The states :func:`image_state` answers.
IMAGE_LOCAL: Final[str] = "local"
IMAGE_PUSHED: Final[str] = "pushed"
IMAGE_BYO_REGISTRY: Final[str] = "byo-registry"
IMAGE_MISSING: Final[str] = "missing"
IMAGE_UNKNOWN: Final[str] = "unknown"


# -----------------------------------------------------------------------------
# The base a GitHub remote proposes, and the source label
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class GitHubRepository:
    """A GitHub repository a project's git remote names.

    Attributes:
        owner: The account or organisation, as the remote spells it.
        repo: The repository name, as the remote spells it, without ``.git``.
    """

    owner: str
    repo: str

    @property
    def base(self) -> str:
        """The registry base this repository proposes: ``ghcr.io/<owner>/<repo>``, lowercased."""
        return f"{GHCR_HOST}/{self.owner}/{self.repo}".lower()

    @property
    def source_url(self) -> str:
        """The repository's URL, the value an image's source label carries."""
        return f"https://github.com/{self.owner}/{self.repo}"


def github_repository(remote_url: str | None) -> GitHubRepository | None:
    """Read the GitHub repository a git remote URL names.

    ``git@github.com:Owner/Repo.git``, ``ssh://git@github.com/Owner/Repo.git``
    and ``https://github.com/Owner/Repo`` (with or without ``.git`` or a
    trailing slash) name the same repository.

    Args:
        remote_url: The remote URL as git has it configured, or ``None``.

    Returns:
        The :class:`GitHubRepository`, or ``None`` for no URL, a host other
        than GitHub, or a path that is not ``<owner>/<repo>``.
    """
    if not remote_url:
        return None
    url = remote_url.strip()
    if "://" in url:
        parts = urlsplit(url)
        if parts.scheme.lower() not in _URL_SCHEMES:
            return None
        try:
            host = (parts.hostname or "").lower()
        except ValueError:
            return None
        path = parts.path
    else:
        match = _SCP_RE.match(url)
        if match is None:
            return None
        host = match["host"].lower()
        path = match["path"]
    if host not in _GITHUB_HOSTS:
        return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    pieces = path.split("/")
    if len(pieces) != 2:
        return None
    owner, repo = pieces
    if not _OWNER_RE.match(owner) or not _REPO_RE.match(repo) or repo in (".", ".."):
        return None
    return GitHubRepository(owner=owner, repo=repo)


def project_github_repository(project_dir: Path | str) -> GitHubRepository | None:
    """Read the GitHub repository the project's git remote names.

    The remote is ``origin``, else the repository's only remote
    (``wfc.version.git_remote_url``).

    Args:
        project_dir: The project root.

    Returns:
        The :class:`GitHubRepository`, or ``None`` with no remote or a
        remote that is not on GitHub.
    """
    from ..version import git_remote_url

    return github_repository(git_remote_url(project_dir))


#: The OCI label naming the repository an image was built from; GHCR reads
#: it to connect a pushed package to that repository.
SOURCE_LABEL: Final[str] = "org.opencontainers.image.source"


def source_labels(project_dir: Path | str) -> dict[str, str]:
    """Return the labels an image built in the project carries.

    The source label names the GitHub repository the project's git remote
    names (SSH and HTTPS remotes give one URL). A label is image metadata:
    the env fingerprint does not move with it, and the rendered Dockerfile
    does not change.

    Args:
        project_dir: The project root.

    Returns:
        ``{SOURCE_LABEL: "https://github.com/<owner>/<repo>"}``, or ``{}``
        with no remote or a remote that is not on GitHub.
    """
    repository = project_github_repository(project_dir)
    return {SOURCE_LABEL: repository.source_url} if repository is not None else {}


# -----------------------------------------------------------------------------
# The names a pushed image carries
# -----------------------------------------------------------------------------

def push_tag(fingerprint: str) -> str:
    """Return the tag an env image is pushed under: ``fp-<first 12 hex>``.

    Args:
        fingerprint: The image's ``env_fingerprint`` (32 lowercase hex).

    Returns:
        ``fp-`` and the fingerprint's first twelve characters.

    Raises:
        ValueError: *fingerprint* is not an env fingerprint.
    """
    if not _FINGERPRINT_RE.match(fingerprint or ""):
        raise ValueError(f"not an env fingerprint: {fingerprint!r}")
    return f"{PUSH_TAG_PREFIX}{fingerprint[:PUSH_TAG_HEX]}"


def image_repository(base: str, env: str) -> str:
    """Return the repository an env's images are pushed to: ``<base>/<env>``.

    Args:
        base: The registry base (``[registry] url``).
        env: The env name.

    Returns:
        ``<base>/<env>``, with any trailing ``/`` on the base dropped.
    """
    return f"{base.rstrip('/')}/{env}"


#: Docker's grammar for one component of a repository path, as the
#: distribution/reference regex spells it (``path-component``): lower-case
#: letters and digits, joined by ``.``, ``_``, ``__`` or any run of ``-``.
_PATH_COMPONENT_RE = re.compile(r"[a-z0-9]+(?:(?:[._]|__|[-]*)[a-z0-9]+)*")


def push_name_problem(env: str, *, registered: bool = False,
                      registering: bool = False) -> str | None:
    """Say why an env's images cannot be pushed under its name, if they cannot.

    A pushed image is named ``<base>/<env>``, so the env name must be one
    repository path component in Docker's grammar
    (``[a-z0-9]+(?:(?:[._]|__|[-]*)[a-z0-9]+)*``, the distribution/reference
    ``path-component`` regex). The env-name rule is wider (upper case, a
    leading or trailing ``_`` or ``-``), so an env can be registered and run
    on this machine under a name no registry accepts.

    Args:
        env: The env name.
        registered: Whether the env is in ``envs.json``: the reason then
            also names ``wfc delete-env``.
        registering: Whether register-env is asking: the reason then also
            names ``--no-push``.

    Returns:
        ``None`` for a name Docker can push; otherwise the reason, naming
        the rule and the fixes: register the image again under a name that
        fits (and point each method at it), ``--no-push`` when registering,
        or ``wfc delete-env`` for an env nothing needs.
    """
    if _PATH_COMPONENT_RE.fullmatch(env or ""):
        return None
    fixes = [
        "register the image again under a name that fits: `wfc register-env "
        "<new-name> ...`, then set `env: <new-name>` in each method's "
        "method.yaml and run `wfc register-method`"]
    if registering:
        fixes.append("pass --no-push to keep the image on this machine")
    if registered:
        fixes.append(
            f"if nothing needs it, `wfc delete-env {env}` removes it (delete-env "
            f"refuses while a method version records the env, and names those "
            f"versions)")
    return (f"env {env!r} cannot be pushed: a registry repository name is "
            f"lower-case letters and digits joined by `.`, `_`, `__` or `-`, "
            f"and starts and ends with a letter or digit. To fix it, "
            + "; or ".join(fixes) + ".")


def push_target(base: str, env: str, fingerprint: str) -> str:
    """Return the name an env image is tagged and pushed as.

    Args:
        base: The registry base.
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.

    Returns:
        ``<base>/<env>:fp-<12 hex>``.

    Raises:
        ValueError: *fingerprint* is not an env fingerprint.
    """
    return f"{image_repository(base, env)}:{push_tag(fingerprint)}"


def registry_ref(base: str, env: str, digest: str) -> str:
    """Return the ref a pushed image is pulled and run by: ``<base>/<env>@<digest>``.

    Args:
        base: The registry base.
        env: The env name.
        digest: The registry digest ``pushed.json`` records.

    Returns:
        The digest-pinned registry reference, without a scheme.

    Raises:
        ValueError: *digest* is not a ``sha256:`` digest.
    """
    if not _DIGEST_RE.match(digest or ""):
        raise ValueError(f"not a registry digest: {digest!r}")
    return f"{image_repository(base, env)}@{digest}"


def registry_base(project_dir: Path | str) -> str | None:
    """Return the project's registry base: ``[registry] url`` as every reader reads it.

    The one reader of the setting: surrounding space and a trailing ``/``
    are dropped, so ``"ghcr.io/o/r/ "`` and ``"ghcr.io/o/r"`` name one base.

    Args:
        project_dir: The project root.

    Returns:
        The base, or ``None`` when ``[registry] url`` is absent or empty.

    Raises:
        FileNotFoundError: The project has no ``wf-canvas.toml``.
        ValueError: The config cannot be read as wfc config (a
            ``[registry]`` that is not a table, a ``url`` that is not a
            string, another setting refused).
    """
    from ..persistence import read_config

    registry = read_config(Path(project_dir))["registry"]
    base = registry["url"].strip().rstrip("/") if registry else ""
    return base or None


def registry_host(base: str) -> str:
    """Return the registry host of a base, the name ``docker login`` takes.

    Args:
        base: The registry base (``ghcr.io/<owner>/<repo>``).

    Returns:
        The part before the first ``/`` (``ghcr.io``).
    """
    return base.strip().split("/", 1)[0]


def ref_host(ref: str) -> str:
    """Return the registry host an image reference is pulled from.

    Docker's rule: the first part of the name is a host when it holds a
    ``.`` or a ``:`` or is ``localhost``; otherwise the image is on Docker
    Hub (``alpine@sha256:...`` is pulled from ``docker.io``).

    Args:
        ref: An image reference, with or without the ``docker://`` scheme.

    Returns:
        The host ``docker login`` takes for it.
    """
    name = strip_docker_scheme(ref).split("@", 1)[0]
    first, sep, _ = name.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        return first
    return "docker.io"


# -----------------------------------------------------------------------------
# The registry probe
# -----------------------------------------------------------------------------

def probe_registry(base: str, *, ref: str | None = None) -> tuple[bool, str]:
    """Ask the registry whether it answers and accepts Docker's credentials.

    One manifest read through the Docker CLI
    (``wfc.environments.docker.read_manifest``), which logs in with
    Docker's own credential store. A found manifest or "no such manifest"
    means the registry answered and accepted the credentials; anything else
    is a failure naming the registry, ``docker login <host>`` and
    ``wfc share``. Docker's own words are kept in the reason.

    Args:
        base: The registry base.
        ref: The reference to read; a pushed image's ref is the best
            question. ``None`` reads ``<base>/wfc-probe:fp-000000000000``,
            a tag that need not exist.

    Returns:
        ``(True, "")`` when the registry answered and accepted the
        credentials Docker has; ``(False, reason)`` otherwise.
    """
    target = ref or f"{image_repository(base, PROBE_REPOSITORY)}:{PROBE_TAG}"
    answer = docker_runner.read_manifest(target)
    if answer.reachable:
        return True, ""
    host = registry_host(base)
    said = " ".join(answer.detail.split()) or "no output"
    if answer.state == docker_runner.MANIFEST_UNAUTHORIZED:
        what = (f"the registry {base} refused Docker's credentials: Docker is "
                f"not logged in to {host}, or the login has no access")
    elif answer.state == docker_runner.MANIFEST_UNREACHABLE:
        what = f"the registry {base} did not answer"
    else:
        what = f"the registry {base} could not be asked"
    return False, (f"{what} (docker said: {said}). Run `docker login {host}`, "
                   f"then `wfc share` to check the project's sharing setup.")


# -----------------------------------------------------------------------------
# Where an image is
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ImageState:
    """Where one env image is, as this machine can tell without the registry.

    Attributes:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        state: :data:`IMAGE_PUSHED` (its captures folder records a push),
            :data:`IMAGE_BYO_REGISTRY` (a byo image recorded by its own
            registry's digest), :data:`IMAGE_LOCAL` (on this machine, not
            pushed), :data:`IMAGE_MISSING` (neither) or
            :data:`IMAGE_UNKNOWN` (not pushed, and Docker did not answer).
        pushed: The push record, when the image has one.
        image: The recorded ``docker://`` ref, when one is known.
        detail: Docker's own words when the state is ``unknown``; else ``""``.
    """

    env: str
    fingerprint: str | None
    state: str
    pushed: PushedRecord | None = None
    image: str | None = None
    detail: str = ""

    @property
    def in_registry(self) -> bool:
        """Whether the image can be pulled: recorded as pushed, or a byo registry image."""
        return self.state in (IMAGE_PUSHED, IMAGE_BYO_REGISTRY)


def image_state(env: str, fingerprint: str | None, project_dir: Path, *,
                container: str | None = None) -> ImageState:
    """Say where the image *env* recorded at *fingerprint* is.

    Reads the image's captures folder (``pushed.json``, ``image.json``) and
    asks the local daemon; never the registry. An image counts as in the
    registry when its folder records a push, or when it is a byo image
    recorded by a registry's digest; the daemon is not asked about those.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        project_dir: The project root.
        container: The image's recorded ``docker://`` ref, when the caller
            has it (an env's current record); otherwise it is read from the
            image's ``image.json``.

    Returns:
        The :class:`ImageState`. An image with no ref to ask about (no
        captures folder and no *container*) is ``missing``.

    Raises:
        CaptureMismatchError: The folder's ``pushed.json`` is unreadable.
    """
    record = pushed_record(env, fingerprint, project_dir)
    image = container
    if image is None:
        try:
            image = image_info(env, fingerprint, project_dir).image
        except CaptureMissingError:
            image = None
    if record is not None:
        return ImageState(env, fingerprint, IMAGE_PUSHED, pushed=record, image=image)
    if image is None:
        return ImageState(env, fingerprint, IMAGE_MISSING)
    if not strip_docker_scheme(image).startswith(_LOCAL_REPOSITORY_PREFIX):
        return ImageState(env, fingerprint, IMAGE_BYO_REGISTRY, image=image)
    try:
        docker_runner.image_inspect(daemon_ref(image))
    except docker_runner.ImageNotFoundError:
        return ImageState(env, fingerprint, IMAGE_MISSING, image=image)
    except (RuntimeError, OSError) as exc:
        return ImageState(env, fingerprint, IMAGE_UNKNOWN, image=image,
                          detail=str(exc))
    return ImageState(env, fingerprint, IMAGE_LOCAL, image=image)


def pullable_ref(env: str, fingerprint: str | None, image: str,
                 project_dir: Path) -> str | None:
    """Return the ref an image can be pulled by, if it can be pulled at all.

    A pushed image (its captures folder holds ``pushed.json``) is pulled by
    ``<base>/<env>@<digest>``, the base being the project's
    ``[registry] url``. A byo image recorded by its own registry's digest
    is pulled by that recorded ref. Anything else (a ``local/`` image never
    pushed) cannot be pulled. Nothing is asked of Docker or the registry.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        image: The image's recorded ``docker://`` ref.
        project_dir: The project root.

    Returns:
        The digest-pinned registry ref without a scheme, or ``None``.

    Raises:
        ValueError: The image is recorded as pushed but the project sets no
            ``[registry] url``, or ``wf-canvas.toml`` cannot be read.
        CaptureMismatchError: The folder's ``pushed.json`` is unreadable.
    """
    record = pushed_record(env, fingerprint, project_dir)
    if record is not None:
        base = _project_base(project_dir)
        if base is None:
            raise ValueError(
                f"env {env!r}: its image is recorded as pushed "
                f"({layout.CAPTURE_PUSHED_FILENAME}) but the project sets no "
                f"[registry] url to pull it from. Pull the project's latest "
                f"commit, which may set it; or set the base it was pushed to "
                f"with `wfc push-env {env} --registry <url>`")
        return registry_ref(base, env, record.digest)
    bare = strip_docker_scheme(image)
    if not bare.startswith(_LOCAL_REPOSITORY_PREFIX):
        return bare
    return None


# -----------------------------------------------------------------------------
# The canvas's Registry chip and the method-registration pre-check
# -----------------------------------------------------------------------------

#: The states of the canvas's Registry chip for an env's current image.
CHIP_PUSHED: Final[str] = "pushed"
CHIP_NOT_PUSHED: Final[str] = "not_pushed"
CHIP_NONE: Final[str] = "none"
CHIP_MISSING: Final[str] = "missing"
CHIP_UNKNOWN: Final[str] = "unknown"
CHIP_CANNOT_PUSH: Final[str] = "cannot_push"


@dataclass(frozen=True)
class RegistryChip:
    """Where an env's images are, as the canvas's Envs table shows it.

    Attributes:
        state: The current image's state: :data:`CHIP_PUSHED` (recorded as
            pushed, or a byo registry image), :data:`CHIP_NOT_PUSHED` (on
            this machine, a base set, not pushed), :data:`CHIP_NONE` (on
            this machine and no base set), :data:`CHIP_CANNOT_PUSH` (on
            this machine, and the env's name is one no registry accepts,
            base set or not), :data:`CHIP_MISSING` (neither on this machine
            nor in the registry) or :data:`CHIP_UNKNOWN` (Docker did not
            answer).
        base: The project's ``[registry] url``, or ``None``.
        older_not_pushed: Older images method versions record that are on
            this machine and not pushed, counted only with a base set and a
            name that can be pushed.
        older_missing: Method versions recording an older image that is
            neither on this machine nor in the registry.
        recreate: The command that recreates the env's image.
        own_registry: For a byo image recorded by its own registry's digest,
            that ref without the ``docker://`` scheme (the image lives
            there, not under *base*); otherwise ``None``.
        reason: For :data:`CHIP_CANNOT_PUSH`, why the name cannot be pushed
            and what to do (:func:`push_name_problem`); otherwise ``None``.
    """

    state: str
    base: str | None
    older_not_pushed: int = 0
    older_missing: int = 0
    recreate: str = ""
    own_registry: str | None = None
    reason: str | None = None


def _configured_base(project_dir: Path) -> str | None:
    """Return :func:`registry_base`, or ``None`` when the config is unreadable."""
    try:
        return registry_base(project_dir)
    except (FileNotFoundError, ValueError):
        return None


def _chip_state(state: str, base: str | None) -> str:
    """Map an :func:`image_state` answer to the chip's state."""
    if state in (IMAGE_PUSHED, IMAGE_BYO_REGISTRY):
        return CHIP_PUSHED
    if state in (IMAGE_MISSING, IMAGE_UNKNOWN):
        return CHIP_MISSING if state == IMAGE_MISSING else CHIP_UNKNOWN
    return CHIP_NOT_PUSHED if base is not None else CHIP_NONE


def registry_chip(env: str, project_dir: Path, *, container: str,
                  fingerprint: str | None,
                  versions: Sequence[tuple[str, str | None]]) -> RegistryChip:
    """Say where an env's current image and its versions' older images are.

    Every answer comes from :func:`image_state`: the captures folders and
    the local daemon, never the registry. The versions recording the env
    are Registration's, handed in as values.

    Args:
        env: The env name.
        project_dir: The project root.
        container: The current record's ``docker://`` ref.
        fingerprint: The current record's ``env_fingerprint``.
        versions: ``[(module/method vN, env_fingerprint)]`` for the method
            versions recording *env*.

    Returns:
        The :class:`RegistryChip`.
    """
    from .captures import CaptureError
    from .manifest import recreate_command

    base = _configured_base(project_dir)
    name_problem = push_name_problem(env, registered=True)
    own_registry: str | None = None
    reason: str | None = None
    if not fingerprint:
        current = CHIP_MISSING
    else:
        try:
            answer = image_state(env, fingerprint, project_dir, container=container)
        except CaptureError:
            current = CHIP_UNKNOWN
        else:
            current = _chip_state(answer.state, base)
            if answer.state == IMAGE_BYO_REGISTRY and answer.image is not None:
                own_registry = strip_docker_scheme(answer.image)
            elif answer.state == IMAGE_LOCAL and name_problem is not None:
                current, reason = CHIP_CANNOT_PUSH, name_problem
    older: dict[str, int] = {}
    for _label, older_fingerprint in versions:
        if older_fingerprint and older_fingerprint != fingerprint:
            older[older_fingerprint] = older.get(older_fingerprint, 0) + 1
    not_pushed = missing = 0
    for older_fingerprint, count in older.items():
        try:
            state = image_state(env, older_fingerprint, project_dir).state
        except CaptureError:
            continue
        if state == IMAGE_MISSING:
            missing += count
        elif state == IMAGE_LOCAL and base is not None and name_problem is None:
            not_pushed += 1
    return RegistryChip(state=current, base=base, older_not_pushed=not_pushed,
                        older_missing=missing, recreate=recreate_command(env),
                        own_registry=own_registry, reason=reason)


#: The word the canvas's Registry chip shows for each state.
CHIP_LABELS: Final[dict[str, str]] = {
    CHIP_PUSHED: "pushed",
    CHIP_NOT_PUSHED: "not pushed",
    CHIP_NONE: "no registry",
    CHIP_CANNOT_PUSH: "cannot push",
    CHIP_MISSING: "missing",
    CHIP_UNKNOWN: "unknown",
}


def chip_label(chip: RegistryChip) -> str:
    """Say a :class:`RegistryChip` in one cell, in the canvas chip's words.

    The state's word (:data:`CHIP_LABELS`), or ``own registry`` for a byo
    image recorded by its own registry's digest (the canvas chip says
    ``pushed`` and names the ref in its tooltip), then the older counts as
    the canvas shows them beside the chip.

    Args:
        chip: The env's :func:`registry_chip` answer.

    Returns:
        E.g. ``"not pushed, 1 older not pushed, 1 older missing"``.
    """
    parts = ["own registry" if chip.own_registry else CHIP_LABELS[chip.state]]
    if chip.older_not_pushed > 0:
        parts.append(f"{chip.older_not_pushed} older not pushed")
    if chip.older_missing > 0:
        parts.append(f"{chip.older_missing} older missing")
    return ", ".join(parts)


def _counted(count: int, word: str) -> str:
    """``"1 older image"`` / ``"2 older images"``."""
    return f"{count} {word}{'' if count == 1 else 's'}"


def chip_notes(env: str, chip: RegistryChip) -> list[str]:
    """Say what the canvas chip's tooltip says: where the image is and the fix.

    One line for the current image naming the command that fixes it
    (``wfc push-env <env>``, or the recreate command then ``wfc
    register-method``), then one for the older images not pushed and one for
    the versions whose older image is gone, when there are any.

    Args:
        env: The bare env name.
        chip: The env's :func:`registry_chip` answer.

    Returns:
        The lines, current image first.
    """
    push = f"wfc push-env {env}"
    recreate = f"recreate it with `{chip.recreate}`, then `wfc register-method`"
    if chip.state == CHIP_PUSHED and chip.own_registry:
        lines = [f"Image is in its own registry: {chip.own_registry}"]
    elif chip.state == CHIP_PUSHED:
        lines = [f"Image is in {chip.base or 'the registry'}"]
    elif chip.state == CHIP_NOT_PUSHED:
        lines = [f"Not pushed to {chip.base}: run `{push}`"]
    elif chip.state == CHIP_NONE:
        lines = ["No registry: this image stays on this machine; "
                 f"`{push}` sets one and pushes it"]
    elif chip.state == CHIP_CANNOT_PUSH:
        lines = ["Cannot be pushed: "
                 + (chip.reason or "the env name is not a registry repository name")]
    elif chip.state == CHIP_MISSING:
        lines = [f"Not on this machine or in the registry: {recreate}"]
    else:
        lines = ["Docker is not running: cannot tell whether the image is on "
                 "this machine"]
    if chip.older_not_pushed > 0:
        lines.append(f"{_counted(chip.older_not_pushed, 'older image')} not "
                     f"pushed: run `{push}`")
    if chip.older_missing > 0:
        lines.append(f"{_counted(chip.older_missing, 'older version')} with no "
                     f"image: {recreate}")
    return lines


# -----------------------------------------------------------------------------
# Pushing an image, and wfc push-env
# -----------------------------------------------------------------------------

@task(purpose="Push one env image to the project's registry: with a GitHub "
              "remote and an image that lacks the repository's source label, "
              "make a labelled copy whose content renders the same env "
              "fingerprint (refused otherwise); tag the image, or the copy, "
              "<base>/<env>:fp-<12 hex>, push it, read back the digest the "
              "registry reported (the push's own output, else the daemon's "
              "RepoDigests entry) and write pushed.json into the image's "
              "captures folder",
      inputs="project root, env name, env fingerprint, the recorded image ref, "
             "the registry base",
      outputs="(the push record on disk, whether this call wrote it)")
def push_image(project_dir: Path, env: str, fingerprint: str, image: str,
               base: str) -> tuple[PushedRecord, bool]:
    """Push *env*'s image at *fingerprint* to *base* and record the push.

    In a project whose git remote is on GitHub, the pushed image carries the
    repository's source label (:func:`source_labels`), which is how GHCR
    connects the package to the repository. An image built without it (one
    built before the remote was added, a byo ``local/`` image) is pushed as
    a labelled copy (:func:`_labelled_copy`): the same layers and run-time
    config, so the same env fingerprint, under another image ID. The
    record keeps naming the image registered; ``pushed.json`` holds the
    digest of what was pushed.

    The record is written only after the push succeeded; a folder that
    already holds ``pushed.json`` keeps it.

    Args:
        project_dir: The project root.
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        image: The image's recorded ``docker://local/...`` ref.
        base: The registry base.

    Returns:
        ``(record, created)`` as :func:`wfc.environments.captures.write_pushed`
        returns them.

    Raises:
        RuntimeError: Docker refused the tag, the copy or the push, or
            reported no digest, or the labelled copy's content renders
            another env fingerprint; nothing was pushed or recorded.
        ValueError: *env* is a name no registry accepts
            (:func:`push_name_problem`); Docker is not asked.
        CaptureMissingError: The image has no captures folder to record the
            push in.
    """
    name_problem = push_name_problem(env)
    if name_problem is not None:
        raise ValueError(name_problem)
    target = push_target(base, env, fingerprint)
    source = daemon_ref(image)
    labels = source_labels(project_dir)
    if labels and any(docker_runner.image_labels(source).get(key) != value
                      for key, value in labels.items()):
        source = _labelled_copy(env, fingerprint, source, labels)
    docker_runner.tag(source, target)
    digest = (docker_runner.push(target)
              or docker_runner.repo_digest(target, image_repository(base, env)))
    return write_pushed(project_dir, env, fingerprint, digest=digest,
                        tag=push_tag(fingerprint))


#: The tag prefix of the labelled copy :func:`push_image` makes, under ``local/<env>``.
LABELLED_COPY_TAG: Final[str] = "_wfc-labelled"


def labelled_copy_ref(env: str, fingerprint: str) -> str:
    """Return the local name of *env*'s labelled copy at *fingerprint*.

    The name carries the fingerprint, so pushes of two versions of one env
    never share a copy.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.

    Returns:
        ``local/<env>:_wfc-labelled-fp-<first 12 hex of fingerprint>``.
    """
    return f"{_LOCAL_REPOSITORY_PREFIX}{env}:{LABELLED_COPY_TAG}-{push_tag(fingerprint)}"


def _labelled_copy(env: str, fingerprint: str, source: str,
                   labels: dict[str, str]) -> str:
    """Make the labelled copy of *env*'s image that :func:`push_image` pushes.

    Args:
        env: The env name (a name :func:`push_name_problem` accepts).
        fingerprint: The image's ``env_fingerprint``.
        source: The image's daemon ref.
        labels: The labels the copy carries (:func:`source_labels`).

    Returns:
        The copy's local name (:func:`labelled_copy_ref`).

    Raises:
        RuntimeError: Docker refused the copy, or the copy's content renders
            another env fingerprint than *fingerprint*.
    """
    from .fingerprint import content_fingerprint

    copy = labelled_copy_ref(env, fingerprint)
    docker_runner.build_labelled(source, copy, labels)
    copied = content_fingerprint(docker_runner.image_content(copy))
    if copied != fingerprint:
        raise RuntimeError(
            f"the copy of env {env!r}'s image made to carry the source label "
            f"({', '.join(f'{k}={v}' for k, v in sorted(labels.items()))}) has "
            f"other content: its env fingerprint is {copied}, not the recorded "
            f"{fingerprint}. Nothing was pushed or recorded.")
    return copy


class PushRefused(ValueError):
    """``wfc push-env`` refuses before anything is pushed or written."""


#: The commit message of a push-env commit; the base is appended.
PUSH_COMMIT_MESSAGE: Final[str] = "Push env images to"


def check_base(url: str) -> str:
    """Return a registry base given on the command line, or refuse it.

    Args:
        url: ``--registry``'s value.

    Returns:
        The base with surrounding space and a trailing ``/`` dropped.

    Raises:
        PushRefused: The value is empty, carries a scheme or whitespace, or
            has upper-case letters (image repositories are lower case).
    """
    base = (url or "").strip().rstrip("/")
    if (not base or "://" in base or any(ch.isspace() for ch in base)
            or base != base.lower()):
        raise PushRefused(
            f"--registry {url!r} is not a registry base: give it as "
            f"<host>/<path>, lower case and without a scheme (for example "
            f"ghcr.io/<owner>/<repo>)")
    return base


def pushable_envs(project_dir: Path) -> list[str]:
    """Return every env ``wfc push-env --all`` covers, before version reads.

    The envs the manifest records and the envs with a captures folder: a
    method version can only record an image registered in this project, and
    every registered image has a captures folder. The caller adds the
    versions recording each (Environments never reads the version lists).

    Args:
        project_dir: The project root.

    Returns:
        The env names, sorted.

    Raises:
        ValueError: The env manifest cannot be read.
    """
    from .manifest import list_envs

    names = {name for name, _ in list_envs(project_dir)}
    captures = layout.captures_dir(project_dir)
    if captures.is_dir():
        names.update(p.name for p in captures.iterdir() if p.is_dir()
                     and not p.name.startswith("."))
    return sorted(names)


@dataclass(frozen=True)
class PushItem:
    """One image in ``wfc push-env``'s worklist.

    Attributes:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``, or ``None`` for a
            version entry that records none.
        state: An :func:`image_state` answer: ``local`` images are pushed.
        image: The recorded ``docker://`` ref, when known.
        current: Whether this is the env's current record's image.
        versions: The method versions recording the image (``module/method vN``).
        detail: Why a ``local`` image cannot be pushed, or Docker's words for
            ``unknown``; else ``""``.
        name_problem: For an image never pushed (``local``, ``missing`` or
            ``unknown``) whose env name no registry accepts,
            :func:`push_name_problem`'s reason; it takes precedence over the
            state's own reason. Else ``""``.
    """

    env: str
    fingerprint: str | None
    state: str
    image: str | None = None
    current: bool = False
    versions: tuple[str, ...] = ()
    detail: str = ""
    name_problem: str = ""

    @property
    def label(self) -> str:
        """``<env>:fp-<12 hex>``, the name the user reads (and the pushed tag)."""
        if self.fingerprint and _FINGERPRINT_RE.match(self.fingerprint):
            return f"{self.env}:{push_tag(self.fingerprint)}"
        return f"{self.env} (a version that records no image fingerprint)"

    @property
    def recorded_by(self) -> str:
        """Who needs the image: the env's current record and/or method versions."""
        who = (["the env's current record"] if self.current else []) + list(self.versions)
        return ", ".join(who) if who else "no method version"

    @property
    def pushable(self) -> bool:
        """Whether apply pushes it.

        It does when the image is on this machine, unpushed, with a folder
        to record in and a name a registry accepts.
        """
        return self.state == IMAGE_LOCAL and not self.detail and not self.name_problem


@dataclass(frozen=True)
class PushPlan:
    """What ``wfc push-env`` will do, stated before anything is written.

    Attributes:
        project_dir: The project root.
        base: The registry base pushed to.
        set_base: Whether apply writes ``[registry] url`` (a first run).
        base_from: Where a base to set came from, for the message.
        items: The worklist, one per ``(env, fingerprint)``.
    """

    project_dir: Path
    base: str
    set_base: bool
    base_from: str
    items: tuple[PushItem, ...] = field(default_factory=tuple)

    def _with(self, *states: str) -> list[PushItem]:
        return [item for item in self.items if item.state in states]

    @property
    def to_push(self) -> list[PushItem]:
        """The local, unpushed images apply pushes."""
        return [item for item in self.items if item.pushable]

    @property
    def unreachable(self) -> list[PushItem]:
        """Images that cannot be pushed from here.

        They are missing, unknown, unrecordable, or named so no registry
        accepts them.
        """
        return [item for item in self.items
                if item.state in (IMAGE_MISSING, IMAGE_UNKNOWN)
                or (item.state == IMAGE_LOCAL and (item.detail or item.name_problem))]

    @property
    def writes(self) -> bool:
        """Whether apply writes anything: the base, or a push record."""
        return self.set_base or bool(self.to_push)

    @property
    def message(self) -> str:
        """The plan as the user reads it before confirming."""
        config = (f"{layout.STATE_DIR_NAME}/{layout.MARKER_FILENAME}")
        lines = [f"wfc push-env: registry base {self.base}"]
        if self.set_base:
            lines.append(
                f"  set: [registry] url = \"{self.base}\" in {config} "
                f"({self.base_from}), committed with the push records. It is "
                f"set once: a later change of git remote does not move it.")
        for item in self.to_push:
            lines.append(f"  push: {item.label} to {image_repository(self.base, item.env)} "
                         f"(needed by {item.recorded_by})")
        for item in self._with(IMAGE_PUSHED):
            lines.append(f"  already pushed: {item.label}")
        for item in self._with(IMAGE_BYO_REGISTRY):
            lines.append(f"  skipped: {item.label} is a byo image pulled from its "
                         f"own registry ({strip_docker_scheme(item.image or '')})")
        for item in self.unreachable:
            if item.name_problem:
                why = item.name_problem
            elif item.state == IMAGE_UNKNOWN:
                why = (f"Docker did not answer, so whether it is on this "
                       f"machine is unknown ({item.detail})")
            elif item.detail:
                why = item.detail
            else:
                why = "not on this machine and never pushed"
            lines.append(f"  cannot push: {item.label}: {why} (needed by "
                         f"{item.recorded_by})")
        if not self.writes:
            lines.append("  nothing to push.")
        return "\n".join(lines)


@dataclass
class PushResult:
    """What :func:`apply_push` did.

    Attributes:
        pushed: The images pushed, with the record each now has.
        failure: Docker's refusal of a push, when one failed (later images
            were not attempted).
        base_written: Whether ``[registry] url`` was written.
        config_error: Why ``[registry] url`` could not be written, when the
            edit was refused or the file could not be written.
        commit: The commit's SHA, or ``None`` (nothing new, or no git).
        commit_error: git's refusal of the commit, when it refused.
        exit_code: 1 when a push, the config write or the commit failed or
            an image could not be pushed from this machine; else 0.
    """

    pushed: list[tuple[PushItem, PushedRecord]] = field(default_factory=list)
    failure: str | None = None
    base_written: bool = False
    config_error: str | None = None
    commit: str | None = None
    commit_error: str | None = None
    exit_code: int = 0


def _project_base(project_dir: Path) -> str | None:
    """Return :func:`registry_base`, refusing a config push-env cannot read."""
    try:
        return registry_base(project_dir)
    except FileNotFoundError as exc:
        raise PushRefused(f"No wfc project found: {exc}") from exc
    except ValueError as exc:
        raise PushRefused(str(exc)) from exc


@task(purpose="Plan wfc push-env before anything is written: the registry "
              "base (the one set; else --registry; else the GitHub remote's "
              "proposal; else refuse naming --registry; a --registry that "
              "differs from a set base is refused), and the "
              "worklist, every env's current image plus every image a method "
              "version records, each classified as to push, already pushed, "
              "a byo registry image, or not pushable from this machine",
      inputs="project root, the env (or None for every env), the versions "
             "recording each env as values, --registry",
      outputs="a PushPlan")
def plan_push(project_dir: Path, env: str | None, *,
              recorded: Mapping[str, Sequence[tuple[str, str | None]]],
              registry: str | None = None) -> PushPlan:
    """State what ``wfc push-env`` will do; write nothing.

    The worklist for an env is its current record's image plus every image
    a method version records; the caller reads the versions from the
    committed version lists and hands them in, so Environments never reads
    Registration. With *env* ``None`` (``--all``), every env the manifest
    or a captures folder names, and every env in *recorded*, is covered.

    Only the captures folder (``pushed.json``) and the local daemon are
    read; the registry is not asked.

    Args:
        project_dir: The project root.
        env: The env to push, or ``None`` for every env.
        recorded: ``env -> [(module/method vN, env_fingerprint)]`` for the
            versions recording each env.
        registry: ``--registry``'s value, or ``None``.

    Returns:
        The :class:`PushPlan`.

    Raises:
        PushRefused: No base is set and none is given or proposed; a given
            base differs from the set one; the env is neither registered
            nor recorded by a version; the manifest or config is unreadable.
    """
    from ..version import git_remote_url
    from .manifest import list_envs, recreate_command

    口 = Step(step_num=1, name="Settle the registry base",
             purpose="The base already set wins and is never re-derived; a "
                     "first run takes --registry, else the GitHub remote's "
                     "proposal; with neither, refuse naming --registry",
             critical="a --registry differing from a set base is refused: "
                      "the base never moves")
    current = _project_base(project_dir)
    given = check_base(registry) if registry is not None else None
    if current is not None:
        if given is not None and given != current:
            raise PushRefused(
                f"the project's registry base is already {current}; "
                f"--registry {given} would move it, and a project's registry "
                f"base cannot be changed once set. Omit --registry to push to "
                f"{current}.")
        base, set_base, base_from = current, False, ""
    elif given is not None:
        base, set_base, base_from = given, True, "from --registry"
    else:
        repository = project_github_repository(project_dir)
        if repository is None:
            remote = git_remote_url(project_dir)
            why = (f"the git remote {remote} is not on GitHub" if remote
                   else "the project has no git remote")
            raise PushRefused(
                f"no registry base is set ([registry] url) and {why}, so none "
                f"can be proposed. Re-run with "
                f"--registry <url> (for example ghcr.io/<owner>/<repo>).")
        base, set_base = repository.base, True
        base_from = f"proposed from the git remote {repository.source_url}"

    口 = Step(step_num=2, name="Collect the worklist",
             purpose="Each env's current image, then every image a version "
                     "records, once per (env, fingerprint)",
             critical="Environments never reads the version lists: they "
                      "arrive in recorded")
    try:
        records = dict(list_envs(project_dir))
        names = ([env] if env is not None
                 else sorted({*pushable_envs(project_dir), *recorded}))
    except ValueError as exc:
        raise PushRefused(str(exc)) from exc
    if env is not None and env not in records and not recorded.get(env):
        raise PushRefused(
            f"env {env!r} is not in {layout.STATE_DIR_NAME}/"
            f"{layout.ENV_MANIFEST_FILENAME} and no method version records "
            f"it.")

    items: list[PushItem] = []
    never_pushed = (IMAGE_LOCAL, IMAGE_MISSING, IMAGE_UNKNOWN)
    for name in names:
        name_problem = push_name_problem(name, registered=name in records) or ""
        wanted: dict[str | None, list[str]] = {}
        record = records.get(name)
        if record is not None:
            wanted[record.env_fingerprint] = []
        for version, fingerprint in recorded.get(name, ()):
            wanted.setdefault(fingerprint, []).append(version)
        for fingerprint, versions in wanted.items():
            口 = Step(step_num=2.1, name="Say where the image is",
                     purpose="pushed.json, a byo registry ref, or a daemon "
                             "inspect; never the registry")
            is_current = record is not None and fingerprint == record.env_fingerprint
            if not fingerprint:
                items.append(PushItem(name, None, IMAGE_MISSING,
                                      current=is_current, versions=tuple(versions),
                                      name_problem=name_problem))
                continue
            try:
                state = image_state(name, fingerprint, project_dir,
                                    container=(record.container
                                               if record is not None and is_current
                                               else None))
            except CaptureError as exc:
                raise PushRefused(
                    f"cannot read the image record for {name}: {exc}") from exc
            detail = state.detail
            if (state.state == IMAGE_LOCAL and not layout.env_capture_dir(
                    project_dir, name, fingerprint).is_dir()):
                detail = ("it has no captures folder to record the push in; "
                          f"`{recreate_command(name)}` registers it again")
            items.append(PushItem(name, fingerprint, state.state, image=state.image,
                                  current=is_current, versions=tuple(versions),
                                  detail=detail,
                                  name_problem=(name_problem
                                                if state.state in never_pushed
                                                else "")))
    return PushPlan(project_dir=Path(project_dir), base=base, set_base=set_base,
                    base_from=base_from, items=tuple(items))


@workflow(purpose="Apply a confirmed wfc push-env plan: push every local, "
                  "unpushed image (tag, push, digest, pushed.json), write "
                  "[registry] url on a first run, and commit the new push "
                  "records and the config in one pathspec commit; images not "
                  "on this machine are reported by name and exit 1",
          inputs="the PushPlan the user confirmed",
          outputs="a PushResult with the exit code")
def apply_push(plan: PushPlan) -> PushResult:
    """Push what *plan* names and commit the records.

    A push that fails stops the run: the images pushed before it keep their
    records and are committed, the rest are not attempted. The base of a
    first run is written only once a push succeeded, or when there was
    nothing to push, so a first run whose every push fails writes nothing.

    Args:
        plan: The plan :func:`plan_push` made, confirmed by the user.

    Returns:
        The :class:`PushResult`; its ``exit_code`` is 1 when a push or the
        commit failed or an image could not be pushed from this machine.
    """
    from ..persistence import set_registry_url
    from ..version import commit_paths

    result = PushResult()
    project_dir = plan.project_dir
    口 = Step(step_num=1, name="Push each local, unpushed image",
             purpose="In worklist order; the first failure stops the run",
             critical="a record is written only after its push succeeded")
    for item in plan.to_push:
        if item.fingerprint is None or item.image is None:
            continue  # not pushable: PushItem.pushable needs a local image
        print(f"  pushing {item.label} to {image_repository(plan.base, item.env)} ...",
              flush=True)
        try:
            口 = AutoStep(step_num=1.1, name="Push one image")
            record, _ = push_image(project_dir, item.env, item.fingerprint,
                                   item.image, plan.base)
        except (RuntimeError, OSError, ValueError) as exc:
            if isinstance(exc, docker_runner.DockerTimeoutError):
                remedy = "Docker did not answer; check that Docker is running"
            else:
                remedy = f"Run `docker login {registry_host(plan.base)}`"
            result.failure = (
                f"Could not push {item.label} to the registry {plan.base}: "
                f"{exc}\n{remedy} and run `wfc push-env` again.")
            break
        result.pushed.append((item, record))
        print(f"  pushed {item.label} ({record.digest})")

    口 = Step(step_num=2, name="Write the base on a first run",
             purpose="[registry] url by a text edit that keeps every other byte",
             critical="not written when every push failed")
    paths: list[Path] = [
        layout.env_capture_dir(project_dir, item.env, item.fingerprint or "")
        / layout.CAPTURE_PUSHED_FILENAME for item, _ in result.pushed]
    if plan.set_base and (result.pushed or not plan.to_push):
        try:
            paths.append(set_registry_url(project_dir, plan.base))
            result.base_written = True
        except (OSError, ValueError) as exc:
            result.config_error = (
                f"[registry] url = \"{plan.base}\" could not be written: {exc}\n"
                f"Fix {layout.STATE_DIR_NAME}/{layout.MARKER_FILENAME} and run "
                f"`wfc push-env` again to set it; the images pushed are "
                f"recorded and not pushed again.")

    口 = Step(step_num=3, name="Commit the records and the base",
             purpose="One pathspec commit of every new pushed.json and the "
                     "config; none outside a git repository")
    if paths:
        try:
            result.commit = commit_paths(
                project_dir, paths, f"{PUSH_COMMIT_MESSAGE} {plan.base}",
                require_repo=False)
        except RuntimeError as exc:
            result.commit_error = (
                f"{exc}\nThe pushes succeeded and their records are written "
                f"but not committed; commit them with git.")

    out, err = _result_lines(plan, result)
    for line in out:
        print(line)
    for line in err:
        print(line, file=sys.stderr)
    result.exit_code = 1 if (result.failure or result.config_error
                             or result.commit_error or plan.unreachable) else 0
    return result


def _result_lines(plan: PushPlan,
                  result: PushResult) -> tuple[list[str], list[str]]:
    """Return the summary :func:`apply_push` prints: (stdout lines, stderr lines)."""
    out: list[str] = []
    if result.base_written:
        out.append(f"  set [registry] url = \"{plan.base}\"")
    if result.commit is not None:
        out.append(f"  git: committed as {result.commit[:12]}")
    err = [f"ERROR: {text}"
           for text in (result.failure, result.config_error, result.commit_error)
           if text]
    err.extend(unpushable_lines(plan))
    return out, err


def unpushable_lines(plan: PushPlan,
                     items: Sequence[PushItem] | None = None) -> list[str]:
    """Return one ``ERROR:`` line per image *plan* cannot push from this machine.

    A name no registry accepts is named first: such an image cannot be
    pushed from any machine, whatever its state.

    Args:
        plan: The plan.
        items: The unreachable items to name; ``None`` for all of them.

    Returns:
        The lines, naming each image and what to do about it.
    """
    from .manifest import recreate_command

    lines = []
    for item in (plan.unreachable if items is None else items):
        if item.name_problem:
            why = item.name_problem
        elif item.state == IMAGE_UNKNOWN:
            why = f"Docker did not answer ({item.detail})."
        elif item.detail:
            why = item.detail
        else:
            why = ("it is not on this machine and was never pushed. Push it "
                   "from the machine that registered it, or recreate the env "
                   f"(`{recreate_command(item.env)}`) and register the "
                   "methods that record it again (`wfc register-method`).")
        lines.append(f"ERROR: {item.label} was not pushed (needed by "
                     f"{item.recorded_by}): {why}")
    return lines
