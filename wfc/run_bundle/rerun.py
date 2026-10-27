#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Rerun every step of this wfc bundle in Docker and compare its outputs with the recorded ones.

Run it from the extracted bundle::

    python rerun.py                 # reruns into a new rerun-<UTC time>/ folder here
    python rerun.py --work DIR      # reruns into DIR, which must be empty

It needs Python 3.11 or newer and Docker, and nothing else: it uses the
standard library only and never imports wfc.

What it does:

1. Checks the Python version, that ``docker`` is on PATH and that the Docker
   daemon answers. It names whatever is missing and runs nothing.
2. Finds each step's image: on this machine, then the bundle's saved copy
   (``images/<env folder>.tar``, loaded with ``docker load``), then a pull by
   its registry link. Each image's contents (its layers and its ``Env``,
   ``User`` and ``WorkingDir``) must give the env fingerprint the step
   recorded; an image with different contents is refused.
3. Replays each step, one at a time in dependency order, from its recorded
   invocation (``wfc-run.json``): the same mounts, container paths and
   ``WFC_*`` values, with each input placed at the container path the step
   read it from. On Linux and macOS it runs as your user and group.
4. Hashes each output the way wfc does (the MD5 of a file's bytes; for a
   directory, the MD5 of DVC's ``.dir`` manifest of it) and reports ``same``
   or ``DIFFERENT``, naming the first difference: the rerun's file and, when
   the bundle holds the outputs, the original in ``outputs/<step folder>/``.
   A step that fails, or whose image is not available, marks every step below
   it "not run"; other branches carry on. When any output is DIFFERENT, the
   last line points to README.md's "If a step says DIFFERENT".

The exit status is 0 when every step ran and every output is the same, 1 on
any difference or any step not run, and 2 when the rerun could not start.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: The oldest Python this script runs on.
PYTHON_FLOOR = (3, 11)
#: The bundle record this script reads, and the newest version it understands.
RECORD = "wfc-run.json"
RECORD_FORMAT = "wfc-run"
RECORD_VERSION = 1
#: How long a docker query (inspect, info) may take before it counts as no answer.
DOCKER_QUERY_TIMEOUT_S = 60
#: A directory's content hash ends with this (DVC's ``.dir``).
DIR_HASH_SUFFIX = ".dir"

SAME = "same"
DIFFERENT = "DIFFERENT"
MISSING = "missing"

#: Said in place of the original's path when the bundle holds no outputs.
HASHES_ONLY = ("This bundle holds only the hashes of the original outputs. Ask the sender "
               "for a bundle exported with --with-outputs.")
#: The last line printed when any output is DIFFERENT.
DIFFERENT_HINT = ("A byte difference is not always a different result: see 'If a step "
                  "says DIFFERENT' in README.md.")

_CHUNK_SIZE = 1 << 20


# ---------------------------------------------------------------------------
# Content hash: a copy of wfc's (wfc.identity.content_hash)
# ---------------------------------------------------------------------------


class ContentError(ValueError):
    """A directory holds content wfc does not hash: a symlink, a case-only name clash, no files."""


def hash_file(path: Path | str) -> str:
    """Return the MD5 hex digest of a file's bytes.

    Args:
        path: The file.

    Returns:
        32-character hex MD5.
    """
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_link(path: str) -> bool:
    """Whether a path is a symlink or (on Windows) a directory junction."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _check_case(names: Iterable[str], where: Path) -> None:
    """Refuse two names in one directory that differ only by case."""
    seen: dict[str, str] = {}
    for name in names:
        other = seen.get(name.casefold())
        if other is not None and other != name:
            raise ContentError(f"{where}: two names differ only by case ({other!r} and {name!r})")
        seen[name.casefold()] = name


def _walk(path: Path) -> list[tuple[str, Path]]:
    """Every file of a directory by POSIX relative path, refusing what wfc refuses."""
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")
    if _is_link(str(path)):
        raise ContentError(f"{path}: the directory itself is a symlink")
    files: list[tuple[str, Path]] = []
    for root, dirs, names in os.walk(path, followlinks=False):
        root_path = Path(root)
        _check_case(list(dirs) + list(names), root_path)
        for name in list(dirs) + list(names):
            if _is_link(os.path.join(root, name)):
                raise ContentError(f"{root_path / name}: a symlink")
        for name in names:
            member = root_path / name
            files.append((posixpath.join(*member.relative_to(path).parts), member))
    if not files:
        raise ContentError(f"{path}: the directory holds no files")
    return files


def manifest_bytes(entries: Iterable[tuple[str, str]]) -> bytes:
    """Serialize ``(relpath, md5)`` entries exactly as DVC serializes a ``.dir`` object.

    Args:
        entries: ``(relpath, md5)`` pairs; relpaths use ``/``.

    Returns:
        The manifest's bytes: a JSON list sorted by relpath, keys sorted.
    """
    ordered = sorted(entries, key=lambda entry: entry[0])
    return json.dumps([{"md5": md5, "relpath": rel} for rel, md5 in ordered],
                      sort_keys=True).encode("utf-8")


def hash_directory(path: Path | str) -> str:
    """Return a directory's content hash: the MD5 of its DVC manifest plus ``.dir``.

    Every file counts, dotfiles included; empty subdirectories and file
    modes do not.

    Args:
        path: The directory.

    Returns:
        The directory's hash.

    Raises:
        ContentError: The directory holds a symlink, a case-only name clash
            or no files.
    """
    entries = [(rel, hash_file(member)) for rel, member in _walk(Path(path))]
    return hashlib.md5(manifest_bytes(entries)).hexdigest() + DIR_HASH_SUFFIX


def hash_path(path: Path | str) -> str:
    """Hash a file or a directory the way wfc does.

    Args:
        path: The file or directory.

    Returns:
        A file's MD5, or a directory's ``.dir`` hash.
    """
    return hash_directory(path) if Path(path).is_dir() else hash_file(path)


# ---------------------------------------------------------------------------
# Image content: a copy of wfc's env fingerprint recipe
# (wfc.environments.docker.image_content, wfc.environments.fingerprint)
# ---------------------------------------------------------------------------


def image_content(inspected: Mapping) -> dict:
    """Read what an image runs with from its ``docker image inspect`` JSON.

    Args:
        inspected: One image's inspect JSON.

    Returns:
        ``{"layers", "env", "user", "workdir"}`` as the daemon reports them.
    """
    rootfs = inspected.get("RootFS") or {}
    config = inspected.get("Config") or {}
    return {"layers": rootfs.get("Layers"), "env": config.get("Env"),
            "user": config.get("User"), "workdir": config.get("WorkingDir")}


def image_fingerprint(content: Mapping) -> str:
    """Return the env fingerprint an image with *content* records.

    The MD5 of the canonical JSON ``{"type": "container-content", "layers",
    "env", "user", "workdir"}`` (sorted keys, no spaces), with a missing
    ``Env`` as ``[]`` and a missing ``User`` or ``WorkingDir`` as ``""``.

    Args:
        content: The shape :func:`image_content` returns.

    Returns:
        32-character hex MD5.

    Raises:
        ValueError: The content has no layers, or a value is not a string.
    """
    layers = content.get("layers")
    if not layers or not isinstance(layers, list | tuple):
        raise ValueError(f"the image has no layers ({layers!r})")
    env = content.get("env") or []
    user = content.get("user") or ""
    workdir = content.get("workdir") or ""
    if not isinstance(env, list | tuple):
        raise ValueError(f"the image's Env is not a list ({env!r})")
    for value in (*layers, *env, user, workdir):
        if not isinstance(value, str):
            raise ValueError(f"the image holds a non-string value {value!r}")
    blob = json.dumps({"type": "container-content", "layers": list(layers), "env": list(env),
                       "user": user, "workdir": workdir},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def image_refusal(content: Mapping, recorded: str | None) -> str | None:
    """Say why an image is not the one a step recorded, or ``None`` when it is.

    Args:
        content: The image's content, as :func:`image_content` returns it.
        recorded: The step's recorded env fingerprint.

    Returns:
        ``None`` when the contents give *recorded*; otherwise the reason.
    """
    try:
        found = image_fingerprint(content)
    except ValueError as exc:
        return f"its contents cannot be read ({exc})"
    if found == recorded:
        return None
    return f"its contents give env fingerprint {found}, not the recorded {recorded}"


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _daemon_problem(docker: str) -> str | None:
    """Ask the Docker daemon for its version; say why it did not answer, or ``None``.

    The daemon answered only when ``docker info`` exits 0 *and* prints a server
    version: Docker CLI 28.0.x exits 0 with nothing on stdout when it is gone.
    """
    try:
        proc = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, timeout=DOCKER_QUERY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"no answer within {DOCKER_QUERY_TIMEOUT_S} seconds"
    except OSError as exc:
        return str(exc)
    if proc.returncode != 0 or not proc.stdout.strip():
        return ((proc.stderr or proc.stdout).strip()
                or f"docker info exited {proc.returncode} without a server version")
    return None


def default_checks() -> dict:
    """The values preflight checks on this machine.

    Returns:
        ``version`` (this Python's), ``docker`` (its path on PATH, or
        ``None``) and ``daemon`` (asks the daemon to answer).
    """
    return {"version": tuple(sys.version_info[:3]), "docker": shutil.which("docker"),
            "daemon": _daemon_problem}


def preflight(*, version: Sequence[int], docker: str | None,
              daemon: Callable[[str], str | None]) -> list[str]:
    """Name everything a rerun needs and this machine lacks.

    Args:
        version: The Python version, ``(major, minor, micro)``.
        docker: The ``docker`` executable found on PATH, or ``None``.
        daemon: Asked with the executable when there is one; returns why the
            daemon did not answer, or ``None`` when it did.

    Returns:
        One sentence per missing item; empty when the rerun can start.
    """
    problems = []
    if tuple(version[:2]) < PYTHON_FLOOR:
        found = ".".join(str(part) for part in version)
        problems.append(f"Python {PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]} or newer is needed; "
                        f"this is Python {found}.")
    if docker is None:
        problems.append("docker is not on PATH: install Docker "
                        "(https://docs.docker.com/get-docker/) and run this again.")
    else:
        why = daemon(docker)
        if why is not None:
            problems.append(f"The Docker daemon does not answer ({why}): start Docker and "
                            f"run this again.")
    return problems


# ---------------------------------------------------------------------------
# The command: a copy of wfc's argv shape (wfc.run_bundle.command.render_argv)
# ---------------------------------------------------------------------------


def replay_argv(invocation: Mapping, hosts: Mapping[str, str], *, image: str,
                user: str) -> list[str]:
    """Render a step's ``docker run`` argv from its invocation record.

    The recorded argv, token for token, with the image the rerun found and
    the user it runs as.

    Args:
        invocation: The step's invocation record.
        hosts: The host side of each mount, by its container path.
        image: The image reference to run.
        user: The ``uid:gid`` to run as.

    Returns:
        The argv.
    """
    argv = ["docker", "run", "--rm"]
    for name, value in (invocation.get("env") or {}).items():
        argv.extend(["-e", f"{name}={value}"])
    argv.extend(["--pull", "never", "--user", user])
    workdir = invocation.get("workdir")
    for mount in invocation.get("mounts") or ():
        spec = f"{hosts[mount['container']]}:{mount['container']}"
        argv.extend(["-v", f"{spec}:ro" if mount.get("read_only") else spec])
        if workdir is not None and mount["container"] == workdir:
            argv.extend(["-w", workdir])
    if invocation.get("gpus"):
        argv.extend(["--gpus", "all"])
    argv.extend([image, str(invocation["interpreter"]), str(invocation["script"])])
    return argv


def _host_user(invocation: Mapping) -> str:
    """This machine's ``uid:gid`` on POSIX; elsewhere the recorded user."""
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is not None and getgid is not None:
        return f"{getuid()}:{getgid()}"
    return str(invocation.get("user"))


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass
class ImageResult:
    """One image's lookup.

    Attributes:
        ref: The reference the report shows.
        available: Whether an image with the recorded contents was found.
        detail: How it was found, or ``NOT AVAILABLE: <why>``.
        run_ref: The local reference the steps run.
    """

    ref: str
    available: bool
    detail: str
    run_ref: str | None = None


@dataclass
class OutputResult:
    """One output's comparison.

    Attributes:
        name: The output's file name, a directory's with a trailing ``/``.
        verdict: ``same``, ``DIFFERENT`` or ``missing``.
        path: Where the rerun wrote it, as the report shows it.
    """

    name: str
    verdict: str
    path: str | None = None


@dataclass
class StepResult:
    """One step's rerun.

    Attributes:
        number: The step's number.
        run_id: The run it reproduces.
        method: ``module.method``.
        version: The method version's label.
        sample: The sample the run ran on.
        outcome: ``ran``, ``failed``, ``image`` (its image is not available)
            or ``blocked`` (a step above it did not finish).
        detail: Why it failed or did not run.
        outputs: Each output's comparison.
        root: For a blocked step, the step whose failure or missing image
            stopped it.
        folder: Its step folder's name (``steps/<folder>``, ``outputs/<folder>``).
    """

    number: int
    run_id: int
    method: str
    version: object
    sample: str | None
    outcome: str
    detail: str = ""
    outputs: list[OutputResult] = field(default_factory=list)
    root: int | None = None
    folder: str = ""


def _sample_text(sample: str | None) -> str:
    if sample is None:
        return "no sample"
    if sample == "__all__":
        return "all samples"
    return sample


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


@dataclass
class _Columns:
    """The report's column widths, fixed before the first line is printed."""

    image: int = 0
    step: int = 0
    run: int = 0
    method: int = 0
    sample: int = 0
    output: int = 0


def _columns(images: Sequence[str], steps: Sequence[tuple[int, int, str, object, str | None]],
             outputs: Sequence[str]) -> _Columns:
    """Widths for image refs, ``(number, run, method, version, sample)`` and output names."""
    return _Columns(
        image=max((len(ref) for ref in images), default=0),
        step=max((len(f"Step {s[0]}") for s in steps), default=0),
        run=max((len(f"run {s[1]}") for s in steps), default=0),
        method=max((len(f"{s[2]} v{s[3]}") for s in steps), default=0),
        sample=max((len(_sample_text(s[4])) for s in steps), default=0),
        output=max((len(name) for name in outputs), default=0))


def _image_line(image: ImageResult, cols: _Columns) -> str:
    return f"Image {image.ref:<{cols.image}}   {image.detail}"


def _step_lines(step: StepResult, cols: _Columns) -> list[str]:
    prefix = (f"{f'Step {step.number}':<{cols.step}}  {f'run {step.run_id}':<{cols.run}}  "
              f"{f'{step.method} v{step.version}':<{cols.method}}  "
              f"{_sample_text(step.sample):<{cols.sample}}   ")
    if step.outcome in ("image", "blocked"):
        return [prefix + f"not run: {step.detail}"]
    if step.outcome == "failed" and not step.outputs:
        return [prefix + f"FAILED: {step.detail}"]
    if not step.outputs:
        return [prefix + "no outputs"]
    lines = []
    for index, output in enumerate(step.outputs):
        lead = prefix if index == 0 else " " * len(prefix)
        lines.append(f"{lead}{output.name:<{cols.output}}  {output.verdict}")
    return lines


def _differing(steps: Sequence[StepResult]) -> list[tuple[StepResult, OutputResult]]:
    """Each output reported DIFFERENT, with its step, in report order."""
    return [(s, o) for s in steps for o in s.outputs if o.verdict.startswith(DIFFERENT)]


def _summary_lines(steps: Sequence[StepResult], with_outputs: bool = False) -> list[str]:
    compared = [(s, o) for s in steps for o in s.outputs if o.verdict != MISSING]
    differ = _differing(steps)
    lines = []
    if differ:
        first_step, first_output = differ[0]
        lines.append(f"{len(differ)} of {_plural(len(compared), 'output')} differ. First "
                     f"difference: step {first_step.number} (run {first_step.run_id}), "
                     f"{first_output.name}")
        if first_output.path:
            lines.append(f"  Rerun's file: {first_output.path}")
        lines.append(f"  Original: outputs/{first_step.folder}/{first_output.name}"
                     if with_outputs else f"  {HASHES_ONLY}")
    no_image = [s for s in steps if s.outcome == "image"]
    if no_image:
        lines.append(f"{_plural(len(no_image), 'step')} could not run because "
                     f"{'its image is' if len(no_image) == 1 else 'their images are'} not "
                     f"available. Re-export with --with-image to keep a copy.")
    for failed in (s for s in steps if s.outcome == "failed"):
        below = sum(1 for s in steps if s.outcome == "blocked" and s.root == failed.number)
        lines.append(f"Step {failed.number} (run {failed.run_id}) failed: {failed.detail}."
                     + (f" {_plural(below, 'step')} below it did not run." if below else ""))
    if not differ and all(s.outcome == "ran" for s in steps):
        lines.append(f"All {_plural(len(compared), 'output')} are the same."
                     if len(compared) != 1 else "The 1 output is the same.")
    return lines


def render_report(images: Sequence[ImageResult], steps: Sequence[StepResult], *,
                  with_outputs: bool = False) -> list[str]:
    """Render the whole report: the images, one block per step, then the summary.

    Args:
        images: Each image's lookup.
        steps: Each step's rerun, in step order.
        with_outputs: Whether the bundle holds the original outputs, so the
            first difference can name the original's path.

    Returns:
        The report's lines.
    """
    cols = _columns([i.ref for i in images],
                    [(s.number, s.run_id, s.method, s.version, s.sample) for s in steps],
                    [o.name for s in steps for o in s.outputs])
    lines = [_image_line(i, cols) for i in images]
    for step in steps:
        lines += _step_lines(step, cols)
    return lines + _summary_lines(steps, with_outputs)


def closing_line(steps: Sequence[StepResult]) -> str | None:
    """The line printed last when any output is DIFFERENT, else ``None``.

    Steps not run and outputs not written do not count: only a byte
    difference points the reader to the README's section.

    Args:
        steps: Each step's rerun.

    Returns:
        :data:`DIFFERENT_HINT`, or ``None``.
    """
    return DIFFERENT_HINT if _differing(steps) else None


def exit_status(steps: Sequence[StepResult]) -> int:
    """0 when every step ran and every output is the same, else 1."""
    clean = all(s.outcome == "ran" and all(o.verdict == SAME for o in s.outputs)
                for s in steps)
    return 0 if clean else 1


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------


class DockerError(RuntimeError):
    """docker answered with an error other than "no such image"."""


class Docker:
    """The few docker commands a rerun uses."""

    def __init__(self, executable: str) -> None:
        """Use the ``docker`` at *executable*."""
        self.executable = executable

    def _call(self, args: Sequence[str], timeout: float | None) -> subprocess.CompletedProcess:
        return subprocess.run([self.executable, *args], capture_output=True, text=True,
                              timeout=timeout)

    def inspect(self, ref: str) -> dict | None:
        """Return an image's inspect JSON, or ``None`` when this machine has no such image.

        Raises:
            DockerError: docker failed for another reason.
        """
        try:
            proc = self._call(["image", "inspect", ref, "--format", "{{json .}}"],
                              DOCKER_QUERY_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise DockerError(f"no answer within {DOCKER_QUERY_TIMEOUT_S} seconds") from exc
        if proc.returncode != 0:
            if "no such image" in (proc.stderr or "").lower():
                return None
            raise DockerError((proc.stderr or "").strip() or f"exit {proc.returncode}")
        try:
            value = json.loads(proc.stdout)
        except ValueError as exc:
            raise DockerError(f"docker printed something that is not JSON ({exc})") from exc
        if isinstance(value, list):
            value = value[0] if value else None
        return value if isinstance(value, dict) else None

    def load(self, archive: Path) -> list[str]:
        """``docker load`` a saved image; return the references it loaded.

        Raises:
            DockerError: The load failed.
        """
        proc = self._call(["load", "-i", str(archive)], None)
        if proc.returncode != 0:
            raise DockerError((proc.stderr or "").strip() or f"exit {proc.returncode}")
        refs = []
        for line in proc.stdout.splitlines():
            for lead in ("Loaded image: ", "Loaded image ID: "):
                if line.startswith(lead):
                    refs.append(line[len(lead):].strip())
        return refs

    def pull(self, ref: str) -> str | None:
        """``docker pull`` *ref*; return why it failed, or ``None``."""
        try:
            proc = self._call(["pull", ref], None)
        except OSError as exc:
            return str(exc)
        if proc.returncode == 0:
            return None
        lines = [line.strip() for line in (proc.stderr or proc.stdout or "").splitlines()
                 if line.strip()]
        return lines[-1] if lines else f"exit {proc.returncode}"

    def run(self, argv: Sequence[str], stdout: Path, stderr: Path) -> int:
        """Run a ``docker run`` argv, its output into two log files; return its exit status."""
        with open(stdout, "wb") as out, open(stderr, "wb") as err:
            return subprocess.run([self.executable, *argv[1:]], stdout=out, stderr=err,
                                  stdin=subprocess.DEVNULL).returncode


# ---------------------------------------------------------------------------
# The rerun
# ---------------------------------------------------------------------------


class BundleError(Exception):
    """The bundle or the working directory cannot be used."""


def load_record(bundle: Path) -> dict:
    """Read the bundle's ``wfc-run.json``.

    Args:
        bundle: The extracted bundle.

    Returns:
        The record.

    Raises:
        BundleError: There is none, or it is not a record this script reads.
    """
    path = bundle / RECORD
    if not path.is_file():
        raise BundleError(f"{bundle} holds no {RECORD}: run rerun.py from an extracted wfc "
                          f"bundle, or name one with --bundle.")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BundleError(f"{path} is not JSON ({exc}).") from exc
    if not isinstance(record, dict) or record.get("format") != RECORD_FORMAT:
        raise BundleError(f"{path} is not a wfc run record.")
    if not isinstance(record.get("version"), int) or record["version"] > RECORD_VERSION:
        raise BundleError(f"{path} is version {record.get('version')!r}; this rerun.py reads "
                          f"version {RECORD_VERSION}. Use the rerun.py that came with it.")
    return record


def prepare_work(bundle: Path, work: Path | None) -> Path:
    """Create the rerun's folder: *work* (absent or empty), or a new ``rerun-<UTC time>/``.

    Args:
        bundle: The extracted bundle.
        work: The folder the user named, or ``None``.

    Returns:
        The folder, created.

    Raises:
        BundleError: *work* is a file or is not empty.
    """
    if work is not None:
        if work.exists() and (not work.is_dir() or any(work.iterdir())):
            raise BundleError(f"--work {work} is not an empty directory; name a new or empty one.")
        work.mkdir(parents=True, exist_ok=True)
        return work.resolve()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    candidate = bundle / f"rerun-{stamp}"
    count = 1
    while candidate.exists():
        count += 1
        candidate = bundle / f"rerun-{stamp}-{count}"
    candidate.mkdir(parents=True)
    return candidate.resolve()


def _env_key(step: Mapping) -> tuple[str | None, str | None]:
    env = step.get("env") or {}
    return env.get("name"), env.get("fingerprint")


def _distinct(values: Iterable[str | None]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _and(items: Sequence[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _shown_ref(steps: Sequence[Mapping]) -> str:
    env = steps[0].get("env") or {}
    return (env.get("pullable_ref") or (steps[0].get("invocation") or {}).get("image_ref")
            or env.get("image") or f"the image of env {env.get('name')}")


def find_image(steps: Sequence[Mapping], bundle: Path, docker: Docker) -> ImageResult:
    """Find the image the given steps (one env and fingerprint) ran in.

    On this machine first (under each reference the steps recorded), then the
    bundle's saved copy, then a pull by the registry link. The first image
    whose contents give the recorded env fingerprint is used.

    Args:
        steps: The steps that share the image, from ``wfc-run.json``.
        bundle: The extracted bundle.
        docker: The docker commands.

    Returns:
        The lookup's result.
    """
    env = steps[0].get("env") or {}
    recorded = env.get("fingerprint")
    shown = _shown_ref(steps)
    match = "contents match the recorded env"
    reasons: list[str] = []

    def check(ref: str, where: str) -> tuple[bool, bool]:
        """(found, matches) for one reference; a mismatch or error adds a reason."""
        try:
            inspected = docker.inspect(ref)
        except DockerError as exc:
            reasons.append(f"docker could not inspect {ref} ({exc})")
            return True, False
        if inspected is None:
            return False, False
        refusal = image_refusal(image_content(inspected), recorded)
        if refusal is not None:
            reasons.append(f"{where} has different contents: {refusal}")
            return True, False
        return True, True

    local = _distinct([*((s.get("invocation") or {}).get("image_ref") for s in steps),
                       env.get("image"), env.get("pullable_ref")])
    found_here = False
    for ref in local:
        found, ok = check(ref, f"the image on this machine as {ref}")
        found_here = found_here or found
        if ok:
            return ImageResult(shown, True, f"found on this machine; {match}", ref)
    if not found_here:
        reasons.append("not on this machine")

    folder = posixpath.basename(str(env.get("folder") or env.get("name") or ""))
    archive = bundle / "images" / f"{folder}.tar"
    if folder and archive.is_file():
        try:
            loaded = docker.load(archive)
        except DockerError as exc:
            reasons.append(f"the bundle's copy did not load ({exc})")
            loaded = []
        for ref in loaded:
            if check(ref, "the bundle's copy")[1]:
                return ImageResult(shown, True, f"loaded from the bundle; {match}", ref)
    else:
        reasons.append("no copy in the bundle")

    pullable = env.get("pullable_ref")
    if pullable:
        failure = docker.pull(pullable)
        if failure is not None:
            reasons.append(f"the pull failed ({failure})")
        elif check(pullable, "the pulled image")[1]:
            return ImageResult(shown, True, f"pulled; {match}", pullable)
    else:
        reasons.append("it was never pushed to a registry")
    return ImageResult(shown, False, "NOT AVAILABLE: " + _and(reasons))


def _mount_hosts(invocation: Mapping, root: Path, bundle: Path, folder: str) -> dict[str, Path]:
    """The host folder each recorded mount binds, by its role."""
    hosts: dict[str, Path] = {}
    for index, mount in enumerate(invocation.get("mounts") or ()):
        role = mount.get("role")
        if role == "project":
            hosts[mount["container"]] = root / "work"
        elif role == "cache":
            hosts[mount["container"]] = root / "cache"
        elif role == "code":
            hosts[mount["container"]] = bundle / "steps" / folder / "code"
        else:
            hosts[mount["container"]] = root / "mounts" / str(index)
    return hosts


def container_to_host(path: str, hosts: Mapping[str, Path]) -> Path | None:
    """Map a container path to its host path through the deepest mount that holds it.

    Args:
        path: A container path.
        hosts: The host side of each mount, by its container path.

    Returns:
        The host path, or ``None`` when no mount holds *path*.
    """
    best = None
    for container in hosts:
        if path == container or path.startswith(container.rstrip("/") + "/"):
            if best is None or len(container) > len(best):
                best = container
    if best is None:
        return None
    rel = path[len(best):].lstrip("/")
    return hosts[best].joinpath(*rel.split("/")) if rel else hosts[best]


def _place(source: Path, target: Path) -> None:
    """Put a copy of *source* (a file or a directory) at *target*."""
    if target.exists() and target.resolve() == source.resolve():
        return
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copyfile(source, target)


def _output_name(output: Mapping) -> str:
    name = str(output.get("file_name"))
    return name + "/" if str(output.get("content_hash") or "").endswith(DIR_HASH_SUFFIX) else name


def _shown_path(path: Path, bundle: Path) -> str:
    """*path* as the report shows it: relative to the bundle when it is inside it."""
    try:
        return path.resolve().relative_to(bundle.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _upstream(step: Mapping, numbers: Mapping[int, int]) -> list[int]:
    """The numbers of the steps whose outputs *step* reads, in first-read order."""
    found: list[int] = []
    for item in step.get("inputs") or ():
        source = item.get("from") or {}
        number = source.get("step")
        if number is None and source.get("run_id") is not None:
            number = numbers.get(source["run_id"])
        if number is not None and number not in found:
            found.append(number)
    for parent in step.get("parent_run_ids") or ():
        number = numbers.get(parent)
        if number is not None and number not in found:
            found.append(number)
    return found


def run_step(step: Mapping, image: ImageResult, bundle: Path, root: Path, docker: Docker,
             produced: dict[tuple, Path]) -> StepResult:
    """Replay one step in the rerun folder and compare its outputs.

    Args:
        step: The step, from ``wfc-run.json``.
        image: Its image's lookup, available.
        bundle: The extracted bundle.
        root: The rerun's folder.
        docker: The docker commands.
        produced: Each rerun output's host path by ``(run id, slot)``; this
            step's outputs are added.

    Returns:
        The step's result.
    """
    result = StepResult(step["number"], step["run_id"], f"{step['module']}.{step['method']}",
                        step.get("version_label"), step.get("sample"), "failed",
                        folder=str(step["folder"]))
    invocation = step.get("invocation") or {}
    folder = result.folder
    hosts = _mount_hosts(invocation, root, bundle, folder)
    roles = {m["container"]: m.get("role") for m in invocation.get("mounts") or ()}
    for container, host in hosts.items():
        if roles.get(container) == "code":
            if not host.is_dir():
                result.detail = f"its code folder steps/{folder}/code is missing from the bundle"
                return result
            outer = {c: h for c, h in hosts.items() if c != container and roles.get(c) != "code"}
            mountpoint = container_to_host(container, outer)
            if mountpoint is not None:
                mountpoint.mkdir(parents=True, exist_ok=True)
        else:
            host.mkdir(parents=True, exist_ok=True)
    run_dir = (invocation.get("env") or {}).get("WFC_RUN_DIR")
    if run_dir:
        target = container_to_host(run_dir, hosts)
        if target is not None:
            target.mkdir(parents=True, exist_ok=True)

    for item in step.get("inputs") or ():
        source_ref = item.get("from") or {}
        container = item.get("container_path")
        if not container:
            continue
        target = container_to_host(container, hosts)
        if "sample" in source_ref:
            source: Path | None = bundle.joinpath(*str(source_ref.get("path")).split("/"))
        else:
            source = produced.get((source_ref.get("run_id"), source_ref.get("slot")))
        if target is None or source is None or not source.exists():
            result.detail = f"its input {item.get('slot')} ({container}) could not be placed"
            return result
        _place(source, target)

    outputs = [(o, container_to_host(str(o.get("container_path")), hosts))
               for o in step.get("outputs") or ()]
    for _output, written in outputs:
        if written is not None:
            written.parent.mkdir(parents=True, exist_ok=True)
    logs = root / "logs" / folder
    logs.mkdir(parents=True, exist_ok=True)
    argv = replay_argv(invocation, {c: h.resolve().as_posix() for c, h in hosts.items()},
                       image=str(image.run_ref), user=_host_user(invocation))
    (logs / "command.txt").write_text(" ".join(argv) + "\n", encoding="utf-8")
    try:
        status = docker.run(argv, logs / "stdout.log", logs / "stderr.log")
    except OSError as exc:
        result.detail = f"docker could not start it ({exc})"
        return result
    if status != 0:
        result.detail = f"exit {status} (its logs are in {root.name}/logs/{folder}/)"
        return result

    missing = []
    for output, written in outputs:
        name = _output_name(output)
        if written is None or not written.exists():
            result.outputs.append(OutputResult(name, MISSING))
            missing.append(name)
            continue
        produced[(step["run_id"], output.get("slot"))] = written
        shown = _shown_path(written, bundle)
        try:
            found = hash_path(written)
        except (ContentError, OSError) as exc:
            result.outputs.append(OutputResult(name, f"{DIFFERENT} (cannot hash it: {exc})",
                                               shown))
            continue
        result.outputs.append(OutputResult(name, SAME if found == output.get("content_hash")
                                           else DIFFERENT, shown))
    if missing:
        result.detail = f"{_and(missing)} {'was' if len(missing) == 1 else 'were'} not written"
        return result
    result.outcome = "ran"
    return result


def rerun(record: Mapping, bundle: Path, root: Path, docker: Docker,
          emit: Callable[[str], object] = print) -> list[StepResult]:
    """Find every image, replay every step in order, and report as it goes.

    Args:
        record: The bundle's ``wfc-run.json``.
        bundle: The extracted bundle.
        root: The rerun's folder.
        docker: The docker commands.
        emit: Called with each report line.

    Returns:
        Each step's result, in step order.
    """
    steps = sorted(record.get("steps") or (), key=lambda s: s["number"])
    numbers = {s["run_id"]: s["number"] for s in steps}
    groups: dict[tuple[str | None, str | None], list[Mapping]] = {}
    for step in steps:
        groups.setdefault(_env_key(step), []).append(step)
    cols = _columns([_shown_ref(group) for group in groups.values()],
                    [(s["number"], s["run_id"], f"{s['module']}.{s['method']}",
                      s.get("version_label"), s.get("sample")) for s in steps],
                    [_output_name(o) for s in steps for o in s.get("outputs") or ()])

    images: dict[tuple[str | None, str | None], ImageResult] = {}
    for key, group in groups.items():
        images[key] = find_image(group, bundle, docker)
        emit(_image_line(images[key], cols))

    results: dict[int, StepResult] = {}
    produced: dict[tuple, Path] = {}
    for step in steps:
        image = images[_env_key(step)]
        blocked = [n for n in _upstream(step, numbers)
                   if n in results and results[n].outcome != "ran"]
        if blocked:
            above = results[blocked[0]]
            result = StepResult(step["number"], step["run_id"],
                                f"{step['module']}.{step['method']}", step.get("version_label"),
                                step.get("sample"), "blocked",
                                detail=f"needs step {above.number}'s output",
                                root=above.root if above.outcome == "blocked" else above.number)
        elif not image.available:
            result = StepResult(step["number"], step["run_id"],
                                f"{step['module']}.{step['method']}", step.get("version_label"),
                                step.get("sample"), "image", detail="its image is not available")
        else:
            result = run_step(step, image, bundle, root, docker, produced)
        results[step["number"]] = result
        for line in _step_lines(result, cols):
            emit(line)
    ordered = [results[s["number"]] for s in steps]
    for line in _summary_lines(ordered, bool(record.get("with_outputs"))):
        emit(line)
    return ordered


def main(argv: Sequence[str] | None = None, *, checks: Mapping | None = None) -> int:
    """Check the machine, then rerun the bundle and compare.

    Args:
        argv: The command-line arguments; ``sys.argv[1:]`` when ``None``.
        checks: The values preflight checks (see :func:`preflight`); this
            machine's when ``None``.

    Returns:
        0 when every step ran and every output is the same, 1 on any
        difference or step not run, 2 when the rerun could not start.
    """
    parser = argparse.ArgumentParser(
        prog="rerun.py",
        description="Rerun every step of this wfc bundle in Docker and compare each "
                    "output with its recorded hash.")
    parser.add_argument("--work", type=Path, default=None,
                        help="an empty directory to rerun in (default: a new "
                             "rerun-<UTC time>/ folder in the bundle)")
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent,
                        help="the extracted bundle (default: this script's folder)")
    args = parser.parse_args(argv)

    given = dict(checks) if checks is not None else default_checks()
    problems = preflight(version=given["version"], docker=given["docker"],
                         daemon=given["daemon"])
    if problems:
        print("Cannot rerun this bundle:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 2
    bundle = args.bundle.resolve()
    try:
        record = load_record(bundle)
        root = prepare_work(bundle, args.work)
    except BundleError as exc:
        print(f"Cannot rerun this bundle: {exc}", file=sys.stderr)
        return 2
    results = rerun(record, bundle, root, Docker(str(given["docker"])),
                    emit=lambda line: print(line, flush=True))
    print(f"Logs and rerun outputs: {root}")
    hint = closing_line(results)
    if hint:
        print(hint)
    return exit_status(results)


if __name__ == "__main__":
    sys.exit(main())
