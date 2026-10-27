"""Captures: what each env image installed, one folder per image, in git.

``register`` writes ``.wfc/captures/<env>/<env_fingerprint>/`` for every
backend, in the same commit as the env's record:

- ``packages.lock``: the package list, the exact bytes the record's
  ``source_fingerprint`` is the md5 of: the lock or explicit list, the
  pip-freeze delimiter, then the pip freeze (:func:`package_list_text`).
  Written for a backend that stages a lock; byo has none.
- ``Dockerfile``: the recipe as rendered, for a backend that builds.
- ``image.json``: the image's facts (:class:`ImageInfo`): backend,
  interpreter, base image, image ref, uv's groups, extras and Python
  minor, and for a conda env solved for linux-64 ``solved_for``.
- the backend's other staged files (pixi's ``pixi.toml``).
- ``host/``, for a conda env solved for linux-64: the host's own capture
  (its explicit list, the ``--from-history`` spec, the pinned spec that was
  solved, its full pip freeze, and ``host.json`` with the host platform,
  conda version and capture time).
- ``pushed.json``, added after a successful push (:func:`write_pushed`):
  the registry digest, the tag and the push time (:class:`PushedRecord`).

A folder is written once: a temporary sibling is filled, then renamed into
place, and a folder that already exists for the fingerprint is left as it
is. Callers ask this module about an image by ``(env, fingerprint)``
(:func:`image_info`, :func:`capture_exists`) and about an env's current
package list (:func:`verified_package_list`); nothing else reads the folder.
A folder changed outside wfc (:func:`intact_image_info` refuses it) is
never written over: :func:`capture_remedy` names the way on, for every
caller.
Every path comes from :mod:`wfc.layout`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeGuard

from axiom_annotations import task

from .. import layout
from ..reserved import DEMO_ENV_NAME
from .packages import PIP_FREEZE_DELIMITER

_HEX = frozenset("0123456789abcdef")


class CaptureError(Exception):
    """An image's captures folder cannot answer; the message names the fix."""


class CaptureMissingError(CaptureError):
    """The captures folder, or a file it should hold, does not exist."""


class CaptureMismatchError(CaptureError):
    """``packages.lock`` no longer hashes to the record's ``source_fingerprint``."""


@dataclass(frozen=True)
class ImageInfo:
    """The facts ``image.json`` records about one env image.

    Attributes:
        backend: The backend that made the image (``pixi``, ``conda``, ``byo``).
        interpreter: The container-side interpreter a method script runs
            under: the ``--interpreter`` override, or the backend's default.
        image: The digest-pinned ``docker://`` ref the record names.
        base_image: The image the Dockerfile builds from; ``None`` when wfc
            built nothing (byo).
        groups: uv's dependency groups; empty for every other backend.
        extras: uv's extras; empty for every other backend.
        solved_for: The platform a conda env captured on another platform
            was solved for (``"linux-64"``); its package list is what the
            image installed, and the host's own capture is under ``host/``.
            ``None`` for every env installed from its own lock or list, and
            then absent from ``image.json``.
        python_minor: uv's Python minor (``"3.12"``), the CPython the image
            runs and its package list's markers are evaluated for. ``None``
            for every other backend, and then absent from ``image.json``.
    """

    backend: str
    interpreter: str
    image: str
    base_image: str | None = None
    groups: tuple[str, ...] = field(default_factory=tuple)
    extras: tuple[str, ...] = field(default_factory=tuple)
    solved_for: str | None = None
    python_minor: str | None = None

    def to_json(self) -> str:
        """Return the ``image.json`` text: sorted keys, two-space indent, LF."""
        payload = {
            "backend": self.backend,
            "interpreter": self.interpreter,
            "image": self.image,
            "base_image": self.base_image,
            "groups": list(self.groups),
            "extras": list(self.extras),
        }
        if self.solved_for is not None:
            payload["solved_for"] = self.solved_for
        if self.python_minor is not None:
            payload["python_minor"] = self.python_minor
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> ImageInfo:
        """Parse ``image.json`` text.

        Args:
            text: The file's text.

        Returns:
            The :class:`ImageInfo` it records.

        Raises:
            ValueError: The text is not a JSON object naming a backend, an
                interpreter and an image.
        """
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("image.json is not a JSON object")
        missing = [k for k in ("backend", "interpreter", "image") if not data.get(k)]
        if missing:
            raise ValueError(f"image.json has no {', '.join(missing)}")
        return cls(
            backend=data["backend"],
            interpreter=data["interpreter"],
            image=data["image"],
            base_image=data.get("base_image"),
            groups=tuple(data.get("groups") or ()),
            extras=tuple(data.get("extras") or ()),
            solved_for=data.get("solved_for"),
            python_minor=data.get("python_minor"),
        )


def package_list_text(lock_content: str, pip_freeze_content: str) -> str:
    """Return the package list: the lock section, the delimiter, the pip section.

    The same shape for every backend that stages a lock; its UTF-8 bytes
    are ``packages.lock`` and their md5 is ``source_fingerprint``.

    Args:
        lock_content: The lock or explicit list, LF line endings.
        pip_freeze_content: The pip freeze (empty when nothing was
            pip-installed), LF line endings.

    Returns:
        ``<lock><PIP_FREEZE_DELIMITER><pip freeze>``.
    """
    return f"{lock_content}{PIP_FREEZE_DELIMITER}{pip_freeze_content}"


def package_list_md5(text: str) -> str:
    """Return the md5 of a package list's UTF-8 bytes (its ``source_fingerprint``)."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _is_fingerprint(value: str | None) -> TypeGuard[str]:
    """Whether *value* is a 32-char lowercase hex md5, safe as a folder name."""
    return value is not None and len(value) == 32 and set(value) <= _HEX


@task(purpose="Write an env image's captures folder once: fill a temporary "
              "sibling with packages.lock, the Dockerfile, image.json and the "
              "backend's other staged files as LF bytes, then rename it into "
              "place; an existing folder for the fingerprint is left untouched",
      inputs="project root, env name, env fingerprint, the image's facts, the "
             "package list, the Dockerfile, other files by name",
      outputs="(the folder's path, whether this call created it)")
def write_capture(
    project_dir: Path,
    env: str,
    fingerprint: str,
    image: ImageInfo,
    *,
    package_list: str | None = None,
    dockerfile: str | None = None,
    files: dict[str, str] | None = None,
) -> tuple[Path, bool]:
    """Write ``.wfc/captures/<env>/<fingerprint>/``, unless it already exists.

    The files are written as the given text's UTF-8 bytes, never
    newline-translated, so ``packages.lock`` keeps exactly the bytes its md5
    was taken over.

    Args:
        project_dir: The project root.
        env: The env name.
        fingerprint: The image's ``env_fingerprint`` (32-char md5).
        image: The facts written to ``image.json``.
        package_list: The :func:`package_list_text` text for
            ``packages.lock``; ``None`` writes no package list (byo).
        dockerfile: The rendered Dockerfile; ``None`` writes none (byo).
        files: Other files to write, by name inside the folder (pixi's
            ``pixi.toml``).

    Returns:
        ``(folder, created)``: the folder's path and ``False`` when a folder
        for *fingerprint* was already there and was left as it was.

    Raises:
        ValueError: *fingerprint* is not a 32-char md5.
    """
    if not _is_fingerprint(fingerprint):
        raise ValueError(f"not an env fingerprint: {fingerprint!r}")
    folder = layout.env_capture_dir(project_dir, env, fingerprint)
    if folder.exists():
        return folder, False

    contents: dict[str, str] = {layout.CAPTURE_IMAGE_FILENAME: image.to_json()}
    if package_list is not None:
        contents[layout.CAPTURE_PACKAGES_FILENAME] = package_list
    if dockerfile is not None:
        contents[layout.CAPTURE_DOCKERFILE_FILENAME] = dockerfile
    contents.update(files or {})

    folder.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{fingerprint}.", dir=folder.parent))
    try:
        for name, text in contents.items():
            target = tmp / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
        tmp.rename(folder)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return folder, True


#: A stand-in fingerprint for probing paths before the image exists.
_PROBE_FINGERPRINT = "0" * 32


@task(purpose="Refuse, before any build, a captures path git would ignore: "
              "probe the env's captures directory and each file a folder of "
              "it would hold, naming the ignore rule git reports",
      inputs="project root, env name, the file names the folder will hold",
      outputs="None; ValueError naming the path and the rule")
def check_capture_committable(project_dir: Path, env: str,
                              filenames: list[str]) -> None:
    """Refuse an env whose captures folder git would not commit.

    Outside a git working tree there is nothing to commit, and nothing is
    refused. A rule can ignore the directory (``.wfc/captures/``) or only
    some files in it (``*.lock``), so the directory and each file a folder
    would hold are probed, under a stand-in fingerprint.

    Args:
        project_dir: The project root.
        env: The env name.
        filenames: The names of the files the folder will hold.

    Raises:
        ValueError: git ignores the directory or one of the files; the
            message names the path and, when git reports one, the rule.
    """
    from ..version import git_toplevel, ignore_rule, path_is_committable

    if git_toplevel(project_dir) is None:
        return
    env_dir = layout.env_captures_dir(project_dir, env)
    sample = layout.env_capture_dir(project_dir, env, _PROBE_FINGERPRINT)
    for path in [env_dir, *(sample / name for name in filenames)]:
        if path_is_committable(project_dir, path):
            continue
        shown = (layout.env_captures_dir(Path(), env) if path == env_dir
                 else layout.env_capture_dir(Path(), env, "<fingerprint>") / path.name)
        rule = ignore_rule(project_dir, path)
        because = (f"git ignores it by the rule {rule}" if rule
                   else "git would not commit it (it is ignored, or outside "
                        "the git work tree)")
        raise ValueError(
            f"Cannot register env {env!r}: its captures path "
            f"{shown.as_posix()} would not be committed; {because}. Every "
            f"env image's captures are committed with its record. Remove "
            f"that rule and run `wfc register-env` again."
        )


def captures_attributes_present(project_dir: Path) -> bool:
    """Whether ``.gitattributes`` already keeps captures byte-exact.

    Args:
        project_dir: The project root.

    Returns:
        ``True`` when the file has the :data:`wfc.layout.CAPTURES_GITATTRIBUTES_LINE`
        line.
    """
    path = layout.gitattributes_path(project_dir)
    if not path.is_file():
        return False
    lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
    return layout.CAPTURES_GITATTRIBUTES_LINE in {ln.strip() for ln in lines}


def add_captures_attributes(project_dir: Path) -> bool:
    """Append the captures line to ``.gitattributes`` when it is missing.

    Without ``-text``, a clone with ``core.autocrlf=true`` would check a
    capture out with CRLF line endings and its md5 would no longer match.
    The file's existing bytes are kept; the line is appended with LF.

    Args:
        project_dir: The project root.

    Returns:
        ``True`` when the line was added; ``False`` when it was there.
    """
    if captures_attributes_present(project_dir):
        return False
    path = layout.gitattributes_path(project_dir)
    existing = path.read_bytes() if path.is_file() else b""
    separator = b"\n" if existing and not existing.endswith(b"\n") else b""
    path.write_bytes(existing + separator
                     + layout.CAPTURES_GITATTRIBUTES_LINE.encode("utf-8") + b"\n")
    return True


def discard_capture(project_dir: Path, env: str, fingerprint: str) -> None:
    """Remove a captures folder this registration wrote but did not commit.

    The env's captures directory goes too when the folder was its last.

    Args:
        project_dir: The project root.
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
    """
    folder = layout.env_capture_dir(project_dir, env, fingerprint)
    shutil.rmtree(folder, ignore_errors=True)
    env_dir = folder.parent
    if env_dir.is_dir() and not any(env_dir.iterdir()):
        env_dir.rmdir()


def capture_exists(env: str, fingerprint: str | None, project_dir: Path) -> bool:
    """Whether the image *env* recorded at *fingerprint* has a captures folder.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``; an empty or malformed
            value has no folder.
        project_dir: The project root.

    Returns:
        ``True`` when the folder holds an ``image.json``.
    """
    if not _is_fingerprint(fingerprint):
        return False
    folder = layout.env_capture_dir(project_dir, env, fingerprint)
    return (folder / layout.CAPTURE_IMAGE_FILENAME).is_file()


def capture_files(env: str, fingerprint: str | None, project_dir: Path) -> dict[str, bytes]:
    """Return every file of the image's captures folder, by POSIX path inside it.

    The folder's bytes as git holds them, for a reader that carries the
    captures elsewhere (a run bundle's ``envs/<env>/``).

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        project_dir: The project root.

    Returns:
        ``{relative POSIX path: bytes}``, sorted by path.

    Raises:
        CaptureMissingError: The image has no captures folder.
    """
    folder = (layout.env_capture_dir(project_dir, env, fingerprint)
              if _is_fingerprint(fingerprint) else None)
    if folder is None or not folder.is_dir():
        raise CaptureMissingError(
            f"env {env!r} has no captures folder for image {fingerprint!r} "
            f"(it was registered before wfc recorded captures, or the folder "
            f"was removed)"
        )
    files = {path.relative_to(folder).as_posix(): path.read_bytes()
             for path in folder.rglob("*") if path.is_file()}
    return dict(sorted(files.items()))


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class PushedRecord:
    """What ``pushed.json`` records about one push of an env image.

    Attributes:
        digest: The manifest (or index) digest the registry reported for
            the pushed tag, ``sha256:<64 hex>``.
        tag: The tag pushed (``fp-<12 hex>``).
        pushed_at: When the push finished, ISO 8601 UTC. Informational:
            nothing orders by it.
    """

    digest: str
    tag: str
    pushed_at: str

    def to_json(self) -> str:
        """Return the ``pushed.json`` text: sorted keys, two-space indent, LF."""
        payload = {"digest": self.digest, "tag": self.tag,
                   "pushed_at": self.pushed_at}
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> PushedRecord:
        """Parse ``pushed.json`` text.

        Args:
            text: The file's text.

        Returns:
            The :class:`PushedRecord` it holds.

        Raises:
            ValueError: The text is not a JSON object with a ``sha256:``
                digest, a tag and a push time.
        """
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("pushed.json is not a JSON object")
        missing = [k for k in ("digest", "tag", "pushed_at")
                   if not isinstance(data.get(k), str) or not data[k]]
        if missing:
            raise ValueError(f"pushed.json has no {', '.join(missing)}")
        if not _DIGEST_RE.match(data["digest"]):
            raise ValueError(f"pushed.json's digest {data['digest']!r} is not "
                             f"a sha256 digest")
        return cls(digest=data["digest"], tag=data["tag"],
                   pushed_at=data["pushed_at"])


@task(purpose="Record a successful push in an image's captures folder: "
              "pushed.json with the registry digest, the tag and the push "
              "time, written once as LF bytes; an existing record is kept",
      inputs="project root, env name, env fingerprint, the pushed digest and tag",
      outputs="(the record on disk, whether this call wrote it)")
def write_pushed(project_dir: Path, env: str, fingerprint: str, *,
                 digest: str, tag: str) -> tuple[PushedRecord, bool]:
    """Add ``pushed.json`` to ``.wfc/captures/<env>/<fingerprint>/``.

    Called only after a push succeeded. The folder's other files are never
    touched. When the folder already holds a ``pushed.json`` it is kept as
    it is: two pushes of one image name the same image, and the first
    record stands.

    Args:
        project_dir: The project root.
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        digest: The registry digest of the pushed tag (``sha256:<hex>``).
        tag: The tag pushed.

    Returns:
        ``(record, created)``: the record now on disk, and ``False`` when
        one was already there and was kept.

    Raises:
        ValueError: *digest* is not a sha256 digest, or *tag* is empty.
        CaptureMissingError: The image has no captures folder.
    """
    if not _DIGEST_RE.match(digest or ""):
        raise ValueError(f"not a registry digest: {digest!r}")
    if not tag:
        raise ValueError("a push record needs the tag pushed")
    folder = (layout.env_capture_dir(project_dir, env, fingerprint)
              if _is_fingerprint(fingerprint) else None)
    if folder is None or not folder.is_dir():
        raise CaptureMissingError(
            f"env {env!r} has no captures folder for image {fingerprint!r}; "
            f"a push is recorded beside the image's captures")
    existing = pushed_record(env, fingerprint, project_dir)
    if existing is not None:
        return existing, False
    pushed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = PushedRecord(digest=digest, tag=tag, pushed_at=pushed_at)
    target = folder / layout.CAPTURE_PUSHED_FILENAME
    fd, tmp = tempfile.mkstemp(prefix=".pushed.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(record.to_json().encode("utf-8"))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return record, True


def pushed_record(env: str, fingerprint: str | None,
                  project_dir: Path) -> PushedRecord | None:
    """Return the push record of *env*'s image at *fingerprint*, if it has one.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``; an empty or malformed
            value has no record.
        project_dir: The project root.

    Returns:
        The :class:`PushedRecord`, or ``None`` when the image's folder holds
        no ``pushed.json`` (it was never pushed from this project).

    Raises:
        CaptureMismatchError: ``pushed.json`` is there but unreadable or not
            a push record; the message names the file.
    """
    if not _is_fingerprint(fingerprint):
        return None
    path = (layout.env_capture_dir(project_dir, env, fingerprint)
            / layout.CAPTURE_PUSHED_FILENAME)
    if not path.is_file():
        return None
    try:
        return PushedRecord.from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CaptureMismatchError(
            f"{path} is not a valid push record ({exc})") from exc


def has_package_list(env: str, fingerprint: str | None, project_dir: Path) -> bool:
    """Whether the image *env* recorded at *fingerprint* has a package list.

    A byo image's folder holds only ``image.json``; every other backend's
    holds a ``packages.lock``. The bytes are not verified here: reading
    the list goes through :func:`verified_package_list`.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``; an empty or malformed
            value has no folder.
        project_dir: The project root.

    Returns:
        ``True`` when the folder holds a ``packages.lock``.
    """
    if not _is_fingerprint(fingerprint):
        return False
    folder = layout.env_capture_dir(project_dir, env, fingerprint)
    return (folder / layout.CAPTURE_PACKAGES_FILENAME).is_file()


def image_info(env: str, fingerprint: str | None, project_dir: Path) -> ImageInfo:
    """Return the facts ``image.json`` records for *env*'s image at *fingerprint*.

    This is the interpreter and backend a method version pinned to that
    image runs with, whatever the env's current record says.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        project_dir: The project root.

    Returns:
        The image's :class:`ImageInfo`.

    Raises:
        CaptureMissingError: The image has no captures folder, or its
            ``image.json`` is missing or unreadable; the message names the
            file once.
    """
    folder = (layout.env_capture_dir(project_dir, env, fingerprint)
              if _is_fingerprint(fingerprint) else None)
    if folder is None or not folder.is_dir():
        raise CaptureMissingError(
            f"env {env!r} has no captures folder for image {fingerprint!r} "
            f"(it was registered before wfc recorded captures, or the folder "
            f"was removed)"
        )
    path = folder / layout.CAPTURE_IMAGE_FILENAME
    if not path.is_file():
        raise CaptureMissingError(f"{path} is missing")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CaptureMissingError(
            f"{path} cannot be read ({exc.strerror or type(exc).__name__})"
        ) from exc
    try:
        return ImageInfo.from_json(text)
    except ValueError as exc:
        raise CaptureMissingError(
            f"{path} is not a valid image record ({exc})") from exc


def capture_remedy(env: str, fingerprint: str, *,
                   lock_differs: bool = False) -> str:
    """Return what was seen in a captures folder wfc won't use, and the way on.

    register-env's refusals and both of the package view's 409s end with
    this text, so their remedy cannot drift apart. The lead says what was
    seen: by default, that the folder's files were changed outside wfc (a
    missing or unreadable ``image.json``, a missing or edited
    ``packages.lock``); with *lock_differs*, that the folder's
    ``packages.lock`` is not the package list this registration produced
    (the folder holds the image's earlier list, or one edited since). wfc
    never writes over a folder (a method version may run the image it
    records), so the remedy is to register the image again under a new env
    name. The reserved demo env is re-created with ``wfc demo --force``,
    which removes the demo, its captures included, before registering it
    again.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        lock_differs: Whether register-env saw a ``packages.lock`` that
            differs from this registration's package list.

    Returns:
        One paragraph: the lead, then the remedy: register the env again
        under a new name (``wfc register-env <new-name> ...``), point the
        method's ``method.yaml`` at it and register the method.
    """
    folder = layout.env_capture_relpath(env, fingerprint)
    if lock_differs:
        lead = (f"env {env!r} already has a capture of this image under "
                f"{folder}/, and its packages.lock differs from the package "
                f"list this registration produced. wfc doesn't rewrite a "
                f"capture folder.")
    else:
        lead = (f"env {env!r}'s capture files under {folder}/ were changed "
                f"outside wfc.")
    if env == DEMO_ENV_NAME:
        remedy = ("Re-create the demo with `wfc demo --force`; it removes the "
                  "demo, captures included, first.")
    else:
        remedy = ("Register the env again under a new name: "
                  "`wfc register-env <new-name> ...`, set `env: <new-name>` "
                  "in the method's method.yaml, then run "
                  "`wfc register-method`.")
    return f"{lead} {remedy}"


def verified_package_list(env: str, project_dir: Path) -> str | None:
    """Return *env*'s current package list, verified against its record.

    Reads ``packages.lock`` from the captures folder of the record's
    ``env_fingerprint`` and checks its md5 against the record's
    ``source_fingerprint``.

    Args:
        env: The env name (the key in ``.wfc/envs.json``).
        project_dir: The project root.

    Returns:
        The package list text (see :func:`package_list_text`), or ``None``
        when the env is not registered or its record has no package list
        (byo).

    Raises:
        CaptureMissingError: The folder or its ``packages.lock`` is missing
            (the message ends with :func:`capture_remedy`), or the record is
            from before captures (it names :func:`~.manifest.recreate_command`).
        CaptureMismatchError: ``packages.lock``'s md5 is not the record's
            ``source_fingerprint``; the message ends with
            :func:`capture_remedy`.
        ValueError: The env manifest cannot be read.
    """
    from .manifest import get, recreate_command

    record = get(env, project_dir)
    if record is None or not record.source_fingerprint:
        return None
    if not _is_fingerprint(record.env_fingerprint):
        # A record from before captures, not a folder changed outside wfc.
        raise CaptureMissingError(
            f"env {env!r} has no recorded package list for its image "
            f"{record.env_fingerprint!r}. Re-register it: "
            f"{recreate_command(env)}"
        )
    try:
        data = _checked_package_list(env, record.env_fingerprint, project_dir,
                                     record.source_fingerprint)
    except CaptureError as exc:
        raise type(exc)(
            f"{exc}. {capture_remedy(env, record.env_fingerprint)}") from exc
    return data.decode("utf-8")


def _checked_package_list(env: str, fingerprint: str, project_dir: Path,
                          md5: str | None) -> bytes:
    """Return a captures folder's ``packages.lock`` bytes, refused when not intact.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint`` (32-char md5).
        project_dir: The project root.
        md5: The ``source_fingerprint`` a record names for these bytes;
            ``None`` checks only that the file is there.

    Returns:
        The file's bytes.

    Raises:
        CaptureMissingError: The file is missing; the message names it.
        CaptureMismatchError: Its md5 is not *md5*.
    """
    path = (layout.env_capture_dir(project_dir, env, fingerprint)
            / layout.CAPTURE_PACKAGES_FILENAME)
    if not path.is_file():
        raise CaptureMissingError(f"{path} is missing")
    data = path.read_bytes()
    if md5 is not None and hashlib.md5(data).hexdigest() != md5:
        raise CaptureMismatchError(
            f"{path} no longer matches env {env!r}'s record (its md5 is not "
            f"the recorded source_fingerprint {md5})")
    return data


def intact_image_info(env: str, fingerprint: str, project_dir: Path, *,
                      lock_md5: str | None = None,
                      needs_lock: bool = False) -> ImageInfo:
    """Return an existing captures folder's facts, once it is as wfc wrote it.

    The same checks the readers make: ``image.json`` parses
    (:func:`image_info`), and ``packages.lock`` is there and, given an md5,
    hashes to it (the check :func:`verified_package_list` makes).
    register-env asks this before it registers over a folder.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.
        project_dir: The project root.
        lock_md5: The md5 ``packages.lock`` must have (register-env passes
            its own list's, or the env's record's), or ``None`` to skip the
            comparison.
        needs_lock: Whether the folder must hold a ``packages.lock`` even
            when no record names its md5 (a backend that stages a lock).

    Returns:
        The folder's :class:`ImageInfo`.

    Raises:
        CaptureMissingError: ``image.json`` is missing or unreadable, or a
            needed ``packages.lock`` is missing; the message names the file.
        CaptureMismatchError: ``packages.lock``'s md5 is not *lock_md5*.
    """
    info = image_info(env, fingerprint, project_dir)
    if lock_md5 is not None or needs_lock:
        _checked_package_list(env, fingerprint, project_dir, lock_md5)
    return info
