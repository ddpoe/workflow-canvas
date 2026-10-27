"""wfc/version.py — Git-commit discipline.

Public functions:
  get_git_commit(repo_path)             Fail-fast on dirty working tree.
  commit_paths(repo_root, paths, msg)   Commit exactly the named paths.
  path_is_committable(repo_root, path)  Would commit_paths commit it, not skip it.
  commit_holds_path(repo_root, c, path) Is commit c in the repo, holding path.
  git_remote_url(directory)             The project's remote URL (origin, else the only one).
  installed_wfc_version()               The installed wfc version, or None.

The identities themselves — the content hash, the code fingerprint, the
input fingerprint and the cache key — live in ``wfc.identity``; this module
composes none of them. The env fingerprint is ``wfc.environments``'
(``capture_env_content``, ``resolve_env_fingerprint``). A MethodVersion is
written only by registration (an entry of the method's version list, then
its row) and verified at the claim
(``wfc.registration.verify_registered_snapshot``).

Design notes:
  - Every git call is bounded (``GIT_TIMEOUT``; the commit, which runs the
    user's hooks, ``GIT_COMMIT_TIMEOUT``, or ``WFC_GIT_COMMIT_TIMEOUT`` when
    set for hooks that need longer). A git that does not finish is
    stopped with its children and reported loudly (:class:`GitTimeoutError`);
    a commit stopped this way leaves nothing staged behind it.
  - get_git_commit raises DirtyRepositoryError if there are uncommitted changes.
    Commit-then-run is the intended discipline; there is no --allow-dirty escape hatch.
  - MethodVersion.git_commit holds the registration commit that wrote the
    version's entry and snapshot; it is not part of the cache key.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .persistence import project_root

logger = logging.getLogger(__name__)

#: The distribution whose installed metadata names wfc's version (and its
#: issues URL, which Registration's advice reads).
_DISTRIBUTION = "workflow-canvas"

#: Seconds a git query or staging call may take before it is stopped.
GIT_TIMEOUT = 60

#: Seconds ``git commit`` may take; it runs the user's hooks, so it gets more.
GIT_COMMIT_TIMEOUT = 300

#: Environment variable that overrides :data:`GIT_COMMIT_TIMEOUT` (seconds).
GIT_COMMIT_TIMEOUT_ENV = "WFC_GIT_COMMIT_TIMEOUT"


def commit_timeout() -> float:
    """The seconds ``git commit`` may take before it is stopped.

    ``WFC_GIT_COMMIT_TIMEOUT`` overrides the default for a project whose
    hooks need more (or less) time.

    Returns:
        The override when set, else :data:`GIT_COMMIT_TIMEOUT`.

    Raises:
        ValueError: The override is not a positive number.
    """
    raw = os.environ.get(GIT_COMMIT_TIMEOUT_ENV, "").strip()
    if not raw:
        return GIT_COMMIT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if value <= 0:
        raise ValueError(
            f"{GIT_COMMIT_TIMEOUT_ENV}={raw!r} is not a positive number of seconds"
        )
    return value


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


# =============================================================================
# Exceptions
# =============================================================================

class DirtyRepositoryError(RuntimeError):
    """Raised when the git working tree has uncommitted changes.

    The fix is always ``git commit`` (or ``git stash``), never a bypass flag.
    """


class GitTimeoutError(RuntimeError):
    """A git command did not finish within its timeout and was stopped.

    Attributes:
        started: ``time.time()`` when the command was launched.
    """

    def __init__(self, message: str, started: float):
        super().__init__(message)
        self.started = started


# =============================================================================
# Running git
# =============================================================================

def _stop_tree(proc: subprocess.Popen) -> None:
    """Kill *proc* and every process it started (a hook and its children).

    Killing only git would leave a hook holding the output pipes open, and
    reading them would wait for the hook.

    Args:
        proc: The git process that ran out of time.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _git(args: list[str], cwd: Path | str,
         timeout: float | None = None,
         env: dict[str, str] | None = None,
         binary: bool = False,
         input: bytes | None = None) -> subprocess.CompletedProcess:
    """Run one git command, bounded by a timeout, never reading a terminal.

    ``-c core.longpaths=true`` goes in right after ``git``, so git on
    Windows reads and writes paths past 260 characters (a deep project's
    registry staging); git elsewhere ignores the option.

    Args:
        args: The command line, starting with ``git``.
        cwd: Directory to run it in.
        timeout: Seconds allowed; ``None`` means :data:`GIT_TIMEOUT`.
        env: Variables added to the inherited environment (a temporary
            ``GIT_INDEX_FILE``, say); ``None`` adds nothing.
        binary: Return stdout and stderr as bytes, untranslated.
        input: Bytes written to the command's stdin (requires *binary*);
            ``None`` gives it no stdin.

    Returns:
        The finished process, with text (or, with *binary*, bytes) stdout
        and stderr.

    Raises:
        GitTimeoutError: The command did not finish in time; it was stopped
            together with every process it started.
        FileNotFoundError: git is not installed (or *cwd* does not exist).
    """
    limit = GIT_TIMEOUT if timeout is None else timeout
    args = [args[0], "-c", "core.longpaths=true", *args[1:]]
    started = time.time()
    proc = subprocess.Popen(
        args, cwd=str(cwd),
        stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=not binary,
        start_new_session=(os.name != "nt"),
        env=({**os.environ, **env} if env else None),
    )
    try:
        out, err = proc.communicate(input=input, timeout=limit)
    except subprocess.TimeoutExpired:
        _stop_tree(proc)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        verb = next((a for a in args[1:]
                     if not a.startswith("-") and "=" not in a), "")
        raise GitTimeoutError(
            f"`git {verb}` in {str(cwd)!r} did not finish within {limit:g} s "
            f"(a hook, a filter or a prompt waiting for input?); it was stopped",
            started,
        ) from None
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


# =============================================================================
# Public API
# =============================================================================

def get_git_commit(repo_path: Path | str | None = None) -> str:
    r"""Return the current HEAD git commit SHA (40 hex chars).

    Args:
        repo_path: Directory within the git repository. Defaults to the
            resolved workflow-canvas project root (not ``Path.cwd()`` — the
            Snakemake hot path runs with cwd rewritten to ``C:\Windows`` on
            Windows and ``wfc run-step`` must still find the real repo).

    Returns:
        40-character SHA1 hex string.

    Raises:
        DirtyRepositoryError: If the working tree has uncommitted changes.
        RuntimeError: If git is not available or the path is not a git repo.
        GitTimeoutError: If git did not answer in time.
    """
    if repo_path is None:
        repo_path = project_root()
    cwd = str(repo_path)

    # Fail-fast: check working tree first
    # Only tracked-file changes (modified, staged, deleted, renamed) block the
    # run.  Untracked files (??) do not affect the commit SHA cache key.
    tracked_dirty = tracked_changes(cwd)
    if tracked_dirty:
        dirty_lines = "\n".join(f"    {line}" for line in tracked_dirty)
        raise DirtyRepositoryError(
            "Working tree has uncommitted changes to tracked files — commit before running.\n"
            f"  Dirty files:\n{dirty_lines}"
        )

    result = _git(["git", "rev-parse", "HEAD"], cwd)
    if result.returncode != 0:
        raise RuntimeError(
            f"git rev-parse HEAD failed in {cwd!r}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


class NotAGitRepositoryError(RuntimeError):
    """Raised by :func:`commit_paths` when a commit is required outside git."""


def git_toplevel(directory: Path | str) -> Path | None:
    """Return the root of the git working tree holding *directory*.

    Args:
        directory: Any directory.

    Returns:
        The working tree's top level, resolved, or ``None`` when *directory*
        is not inside a git working tree (or git is not installed).

    Raises:
        GitTimeoutError: If git did not answer in time.
    """
    try:
        result = _git(["git", "rev-parse", "--show-toplevel"], directory)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


#: The remote :func:`git_remote_url` prefers when a repository has several.
_PREFERRED_REMOTE = "origin"


def git_remote_url(directory: Path | str) -> str | None:
    """Return the URL of the project's git remote, as git has it configured.

    The remote is ``origin`` when there is one, else the repository's only
    remote. The URL is returned as configured (SSH, ``ssh://`` or HTTPS);
    reading what it names is Environments' rule.

    Args:
        directory: A directory inside the repository (the project root).

    Returns:
        The remote's fetch URL, or ``None`` when *directory* is not in a git
        working tree, it has no remote, or it has several and none is
        ``origin``.

    Raises:
        GitTimeoutError: If git did not answer in time.
    """
    try:
        listed = _git(["git", "remote"], directory)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if listed.returncode != 0:
        return None
    remotes = [name.strip() for name in listed.stdout.splitlines() if name.strip()]
    if _PREFERRED_REMOTE in remotes:
        name = _PREFERRED_REMOTE
    elif len(remotes) == 1:
        name = remotes[0]
    else:
        return None
    result = _git(["git", "remote", "get-url", name], directory)
    url = result.stdout.strip()
    if result.returncode != 0 or not url:
        return None
    return url


def path_is_committable(repo_root: Path | str, path: Path | str) -> bool:
    """Say whether :func:`commit_paths` would commit *path* rather than skip it.

    ``commit_paths`` skips a path outside the working tree or one git
    ignores, and returns no SHA, the same answer it gives when nothing
    changed. A caller that must tell the two apart asks here first.

    Args:
        repo_root: A directory inside the repository (the project root).
        path: The file or directory, absolute or relative to *repo_root*.

    Returns:
        ``True`` when *path* lies inside the working tree and git does not
        ignore it; ``False`` otherwise, or when *repo_root* is not in a git
        working tree.

    Raises:
        GitTimeoutError: If git did not answer in time.
    """
    root = Path(repo_root).resolve()
    top = git_toplevel(root)
    if top is None:
        return False
    full = Path(path)
    if not full.is_absolute():
        full = root / full
    try:
        rel = full.resolve().relative_to(top).as_posix()
    except ValueError:
        return False
    if rel in ("", "."):
        return False
    ignored = _git(["git", "check-ignore", "-q", "--", rel], top)
    return ignored.returncode != 0


def ignore_rule(repo_root: Path | str, path: Path | str) -> str | None:
    """Name the ignore rule that makes git ignore *path*, for a refusal message.

    Args:
        repo_root: A directory inside the repository (the project root).
        path: The file or directory, absolute or relative to *repo_root*.
            It need not exist.

    Returns:
        ``<source>:<line>:<pattern>`` as ``git check-ignore -v`` reports it
        (``.gitignore:3:*.lock``), or ``None`` when git does not ignore
        *path*, *path* is outside the working tree, or *repo_root* is not in
        a git working tree.

    Raises:
        GitTimeoutError: If git did not answer in time.
    """
    root = Path(repo_root).resolve()
    top = git_toplevel(root)
    if top is None:
        return None
    full = Path(path)
    if not full.is_absolute():
        full = root / full
    try:
        rel = full.resolve().relative_to(top).as_posix()
    except ValueError:
        return None
    result = _git(["git", "check-ignore", "-v", "--", rel], top)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip().split("\t", 1)[0]


def _top(repo_root: Path | str, top: Path | None) -> Path:
    """Return *top* when the caller resolved it, else the working tree's top level.

    One operation that runs several git commands resolves the top level
    once (:func:`git_toplevel`) and passes it to each, rather than asking
    git again for every command. It is never kept beyond that operation: a
    ``git init`` in a subfolder changes the answer.

    Args:
        repo_root: A directory inside the repository (the project root).
        top: The top level already resolved for *repo_root*, or ``None``.

    Returns:
        The top level.

    Raises:
        NotAGitRepositoryError: *top* is ``None`` and *repo_root* is not in
            a git working tree.
    """
    if top is not None:
        return top
    found = git_toplevel(Path(repo_root).resolve())
    if found is None:
        raise NotAGitRepositoryError(f"{str(repo_root)!r} is not a git repository.")
    return found


def _tree_rel(repo_root: Path | str, path: Path | str,
              top: Path | None = None) -> tuple[Path, str]:
    """Return the working tree's top level and *path* relative to it (posix).

    Args:
        repo_root: A directory inside the repository (the project root).
        path: A file or directory, absolute or relative to *repo_root*.
        top: The top level already resolved for *repo_root*, or ``None``.

    Returns:
        ``(top, rel)``.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        ValueError: *path* lies outside the working tree.
    """
    root = Path(repo_root).resolve()
    top = _top(root, top)
    full = Path(path)
    if not full.is_absolute():
        full = root / full
    return top, full.resolve().relative_to(top).as_posix()


def untracked_files(repo_root: Path | str, path: Path | str) -> list[str]:
    """List the untracked, not-ignored files under *path*.

    Unlike :func:`get_git_commit`, which ignores untracked files across the
    whole repository, this answers for one directory.

    Args:
        repo_root: A directory inside the repository (the project root).
        path: The directory to look in, absolute or relative to *repo_root*.

    Returns:
        The untracked files, relative to the working tree's top level
        (posix), sorted; empty when there are none.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        RuntimeError: ``git ls-files`` failed.
    """
    top, rel = _tree_rel(repo_root, path)
    result = _git(["git", "ls-files", "--others", "--exclude-standard",
                   "-z", "--", rel], top)
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {result.stderr.strip()}")
    return sorted(f for f in result.stdout.split("\0") if f)


def diff_path_against_commit(repo_root: Path | str, commit: str,
                             path: Path | str) -> str:
    """Return how *path* on disk differs from *commit*, as a git diff.

    Covers a committed edit, an uncommitted edit and a deleted file (``git
    diff <commit>`` compares the working tree with the commit), and names
    each untracked file under *path* after the diff.

    Args:
        repo_root: A directory inside the repository (the project root).
        commit: The commit to compare with.
        path: The directory to compare, absolute or relative to *repo_root*.

    Returns:
        The diff text; empty when *path* matches *commit* and holds no
        untracked file.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        RuntimeError: ``git diff`` failed.
    """
    top, rel = _tree_rel(repo_root, path)
    result = _git(["git", "diff", "--no-color", commit, "--", rel], top)
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    text = result.stdout
    for name in untracked_files(repo_root, path):
        text += f"Untracked file: {name}\n"
    return text


def commit_holds_path(repo_root: Path | str, commit: str,
                      path: Path | str) -> bool:
    """Say whether *commit* is in the repository and holds *path*.

    A commit can be missing (history rewritten and garbage-collected), and a
    commit that exists need not hold the path (one made before it was
    added). Neither can be compared with or restored from.

    Args:
        repo_root: A directory inside the repository (the project root).
        commit: The commit to look in.
        path: A file or directory, absolute or relative to *repo_root*.

    Returns:
        ``True`` when *commit* names a commit in the repository whose tree
        holds *path*; ``False`` otherwise.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        GitTimeoutError: If git did not answer in time.
    """
    top, rel = _tree_rel(repo_root, path)
    exists = _git(["git", "cat-file", "-e", f"{commit}^{{commit}}"], top)
    if exists.returncode != 0:
        return False
    held = _git(["git", "cat-file", "-e", f"{commit}:{rel}"], top)
    return held.returncode == 0


def tree_at(repo_root: Path | str, commit: str, path: Path | str, *,
            top: Path | None = None) -> str | None:
    """Return the git object id *commit* holds at *path* (a tree for a directory).

    Args:
        repo_root: A directory inside the repository (the project root).
        commit: A commit-ish (``HEAD``, a SHA).
        path: A file or directory, absolute or relative to *repo_root*.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        The object id, or ``None`` when *commit* is missing or does not hold
        *path*.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
    """
    top, rel = _tree_rel(repo_root, path, top)
    result = _git(["git", "rev-parse", "--verify", "-q", f"{commit}:{rel}"], top)
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else None


def path_tree_hash(repo_root: Path | str, path: Path | str) -> str:
    """Return the tree hash git would commit for the directory *path* as it stands.

    The directory is staged into a temporary index (``GIT_INDEX_FILE``), with
    the same ignore rules and line-ending filters ``git add`` applies, and
    its tree is written from there; the user's index is never touched. The
    blobs and the tree land in the object store, as a commit's would.

    Args:
        repo_root: A directory inside the repository (the project root).
        path: The directory, absolute or relative to *repo_root*.

    Returns:
        The tree's object id.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        RuntimeError: ``git add`` or ``git write-tree`` failed.
    """
    import tempfile

    top, rel = _tree_rel(repo_root, path)
    with tempfile.TemporaryDirectory(prefix="wfc-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        add = _git(["git", "add", "-A", "--", rel], top, env=env)
        if add.returncode != 0:
            raise RuntimeError(f"git add (temporary index) failed: {add.stderr.strip()}")
        tree = _git(["git", "write-tree", f"--prefix={rel}/"], top, env=env)
        if tree.returncode != 0:
            raise RuntimeError(f"git write-tree failed: {tree.stderr.strip()}")
    return tree.stdout.strip()


def read_blob(repo_root: Path | str, tree: str, rel_path: str) -> str | None:
    """Return the text of the file *rel_path* inside the git tree *tree*.

    Args:
        repo_root: A directory inside the repository (the project root).
        tree: A tree-ish (a tree hash, a commit).
        rel_path: The file's POSIX path relative to the tree's root.

    Returns:
        The file's text, or ``None`` when git holds no such file.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
    """
    top = git_toplevel(Path(repo_root).resolve())
    if top is None:
        raise NotAGitRepositoryError(f"{str(repo_root)!r} is not a git repository.")
    result = _git(["git", "cat-file", "-p", f"{tree}:{rel_path}"], top)
    return result.stdout if result.returncode == 0 else None


def archive_tree(repo_root: Path | str, tree: str, dest: Path | str, *,
                 top: Path | None = None) -> bool:
    """Write the files of the git tree *tree* into the directory *dest*.

    ``git archive`` of the tree into a temporary tar file, unpacked into
    *dest*. No index is read or written, so the user's staging area is
    never touched.

    Args:
        repo_root: A directory inside the repository (the project root).
        tree: The tree hash to extract.
        dest: An existing, empty directory to unpack into.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        ``True`` when the tree was extracted; ``False`` when git no longer
        holds *tree* (history rewritten and garbage-collected).

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        RuntimeError: ``git archive`` failed for another reason.
    """
    import tarfile
    import tempfile

    top = _top(repo_root, top)
    held = _git(["git", "cat-file", "-e", f"{tree}^{{tree}}"], top)
    if held.returncode != 0:
        return False
    with tempfile.TemporaryDirectory(prefix="wfc-archive-") as tmp:
        tar_path = Path(tmp) / "tree.tar"
        # No line-ending conversion on the way out: with core.autocrlf=true
        # git archive would write an LF file with CRLF, and the copy would no
        # longer fingerprint as the version. The bytes are the blob's.
        result = _git(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                       "archive", "--format=tar", "-o", str(tar_path), tree], top)
        if result.returncode != 0:
            raise RuntimeError(f"git archive failed: {result.stderr.strip()}")
        with tarfile.open(tar_path) as tar:
            tar.extractall(Path(dest), filter="data")
    return True


def git_installed() -> bool:
    """Return whether a ``git`` binary is on PATH.

    Returns:
        ``True`` when ``git`` resolves on PATH.
    """
    import shutil
    return shutil.which("git") is not None


def in_work_tree(directory: Path | str, timeout: float | None = None) -> bool:
    """Return whether *directory* is inside a git working tree.

    Args:
        directory: An existing directory.
        timeout: Seconds allowed; ``None`` means :data:`GIT_TIMEOUT`.

    Returns:
        ``True`` when ``git rev-parse --is-inside-work-tree`` succeeds there.

    Raises:
        FileNotFoundError: git is not installed.
        GitTimeoutError: git did not answer in time.
    """
    return _git(["git", "rev-parse", "--is-inside-work-tree"], directory,
                timeout=timeout).returncode == 0


def has_head_commit(directory: Path | str) -> bool:
    """Return whether the repository at *directory* has a HEAD commit.

    Args:
        directory: A directory inside the repository.

    Returns:
        ``True`` when ``git rev-parse HEAD`` succeeds there.

    Raises:
        FileNotFoundError: git is not installed.
    """
    return _git(["git", "rev-parse", "HEAD"], directory).returncode == 0


def global_identity_configured() -> bool:
    """Return whether the global git config names a user and an email.

    Returns:
        ``True`` when both ``user.name`` and ``user.email`` are set globally.

    Raises:
        FileNotFoundError: git is not installed.
    """
    home = Path.home()
    name = _git(["git", "config", "--global", "user.name"], home)
    email = _git(["git", "config", "--global", "user.email"], home)
    return bool(name.stdout.strip()) and bool(email.stdout.strip())


def init_repository(directory: Path | str) -> None:
    """Run ``git init`` in the existing directory *directory*.

    On Windows the new repository's local config also gets
    ``core.longpaths=true``, so the user's own git commands work in a deep
    project as wfc's do.

    Args:
        directory: The directory to make a repository.

    Raises:
        FileNotFoundError: git is not installed.
        RuntimeError: ``git init`` or the config write failed, with git's
            message.
    """
    result = _git(["git", "init", str(directory)], directory)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git init failed")
    if sys.platform == "win32":
        result = _git(["git", "config", "--local", "core.longpaths", "true"], directory)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git config core.longpaths failed")


class GitStatusError(RuntimeError):
    """``git status`` failed; :attr:`stderr` holds git's message."""

    def __init__(self, message: str, stderr: str):
        super().__init__(message)
        self.stderr = stderr


def tracked_changes(directory: Path | str) -> list[str]:
    """Return the porcelain lines of tracked files that differ from HEAD.

    Untracked files (``??``) are left out: they do not change the commit a
    run records.

    Args:
        directory: A directory inside the repository.

    Returns:
        The ``git status --porcelain`` lines for modified, staged, deleted
        or renamed tracked files; empty when the tracked tree is clean.

    Raises:
        GitStatusError: ``git status`` failed.
        FileNotFoundError: git is not installed.
        GitTimeoutError: git did not answer in time.
    """
    status = _git(["git", "status", "--porcelain"], directory)
    if status.returncode != 0:
        raise GitStatusError(
            f"git status failed in {str(directory)!r}: {status.stderr.strip()}",
            status.stderr.strip(),
        )
    return [line for line in status.stdout.strip().splitlines()
            if line[:2].strip() and not line.startswith("??")]


def missing_gitattributes_lines(project_dir: Path | str,
                                lines: Sequence[str]) -> list[str]:
    """Return those of *lines* the project's ``.gitattributes`` lacks.

    Args:
        project_dir: The project root.
        lines: Attribute lines, e.g. Layout's registry and captures lines.

    Returns:
        The lines not present (compared stripped), in the order given.
    """
    from . import layout
    path = layout.gitattributes_path(Path(project_dir))
    present: set[str] = set()
    if path.is_file():
        present = {ln.strip() for ln in
                   path.read_bytes().decode("utf-8", errors="replace").splitlines()}
    return [line for line in lines if line not in present]


def add_gitattributes_lines(project_dir: Path | str,
                            lines: Sequence[str]) -> list[str]:
    """Append to ``.gitattributes`` those of *lines* it lacks, with LF.

    The file's existing bytes are kept.

    Args:
        project_dir: The project root.
        lines: Attribute lines to ensure.

    Returns:
        The lines appended; empty when every line was there.
    """
    from . import layout
    missing = missing_gitattributes_lines(project_dir, lines)
    if missing:
        path = layout.gitattributes_path(Path(project_dir))
        existing = path.read_bytes() if path.is_file() else b""
        separator = b"\n" if existing and not existing.endswith(b"\n") else b""
        path.write_bytes(existing + separator
                         + "".join(f"{ln}\n" for ln in missing).encode("utf-8"))
    return missing


def commit_with_tree(repo_root: Path | str, path: Path | str, tree: str,
                     candidates: Sequence[str] = (), *, history: int = 200) -> str | None:
    """Find a commit whose tree at *path* is *tree*.

    The *candidates* are tried first (a registration commit a row recorded,
    ``HEAD``), then the newest *history* commits that touched *path*.

    Args:
        repo_root: A directory inside the repository (the project root).
        path: A directory, absolute or relative to *repo_root*.
        tree: The tree hash wanted at *path*.
        candidates: Commits to try first, in order.
        history: How many commits of *path*'s log to search after them.

    Returns:
        The first such commit's SHA (a candidate as given), or ``None``.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
    """
    for commit in candidates:
        if commit and tree_at(repo_root, commit, path) == tree:
            return commit
    top, rel = _tree_rel(repo_root, path)
    log = _git(["git", "log", f"-n{history}", "--format=%H", "--", rel], top)
    if log.returncode != 0:
        return None
    for commit in log.stdout.split():
        if tree_at(repo_root, commit, path) == tree:
            return commit
    return None


def restore_path_from_commit(repo_root: Path | str, commit: str,
                             path: Path | str) -> None:
    """Put the directory *path* back exactly as *commit* holds it.

    First checks that *commit* is in the repository and holds *path*; only
    then deletes the directory on disk (untracked files included) and
    checks it out from *commit*, which also stages it. Nothing is
    committed; pass *path* to :func:`commit_paths` for that.

    Args:
        repo_root: A directory inside the repository (the project root).
        commit: The commit to restore from.
        path: The directory to restore, absolute or relative to *repo_root*.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        RuntimeError: *commit* is missing or does not hold *path* (the
            directory and the index are left as they were), or ``git rm``
            or ``git checkout`` failed.
    """
    import shutil

    top, rel = _tree_rel(repo_root, path)
    if not commit_holds_path(repo_root, commit, path):
        raise RuntimeError(
            f"Cannot restore {rel} from commit {commit}: the commit is not in "
            f"this repository or does not hold {rel}. Nothing was changed."
        )
    target = top / rel
    if target.exists():
        shutil.rmtree(target)
    # Drop the index entries too, so a file added after *commit* is staged
    # as deleted rather than left behind in the index.
    result = _git(["git", "rm", "-r", "-q", "--cached", "--ignore-unmatch",
                   "--", rel], top)
    if result.returncode != 0:
        raise RuntimeError(f"git rm --cached failed: {result.stderr.strip()}")
    result = _git(["git", "checkout", commit, "--", rel], top)
    if result.returncode != 0:
        raise RuntimeError(f"git checkout failed: {result.stderr.strip()}")


# =============================================================================
# The tree a directory would commit, and a commit from a temporary index
# =============================================================================

#: git's empty tree (SHA-1 repositories).
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

#: The attributes that change what git stores or extracts for a file.
_CONTENT_ATTRIBUTES = ("export-ignore", "export-subst", "text", "eol", "crlf",
                       "filter", "ident", "working-tree-encoding")


class HeadMovedError(RuntimeError):
    """HEAD is no longer the commit a temporary-index commit was built on.

    Nothing of the commit stays: either git refused it, or it was taken back
    off HEAD. The caller rebuilds on the new HEAD and tries again.
    """


@dataclass(frozen=True)
class TreeDifference:
    """One file a directory's committed tree would not give back as it is.

    Attributes:
        path: The file, relative to the directory (POSIX).
        reason: Why: the ignore rule, or the attributes that left it out of
            the extracted copy or changed its bytes.
    """

    path: str
    reason: str


@dataclass(frozen=True)
class GitObject:
    """One object a commit holds at a path, as :func:`objects_at` read it.

    Attributes:
        oid: The object's id.
        kind: ``blob``, ``tree``, ``commit`` (a submodule) or ``tag``.
        data: The object's bytes, unconverted.
    """

    oid: str
    kind: str
    data: bytes


def _cat_file_batch(top: Path, names: Sequence[str],
                    contents: bool) -> list[tuple[str, str, bytes] | None]:
    """Resolve *names* in one ``git cat-file --batch`` (or ``--batch-check``).

    Each name is resolved when git reads its line, in the order given, as
    ``git rev-parse --verify`` would resolve it.

    Args:
        top: The working tree's top level.
        names: Object names (``HEAD``, ``<commit>:<path>``, ``<sha>^{tree}``),
            none holding a newline.
        contents: Read each object's bytes (``--batch``) or only its id and
            type (``--batch-check``).

    Returns:
        ``(oid, type, bytes)`` per name (``b""`` without *contents*), or
        ``None`` for a name git does not resolve to an object.

    Raises:
        RuntimeError: git failed or its answer could not be read.
    """
    import re

    if not names:
        return []
    mode = "--batch" if contents else "--batch-check"
    result = _checked(_git(["git", "cat-file", mode], top, binary=True,
                           input="".join(f"{n}\n" for n in names).encode("utf-8")),
                      f"git cat-file {mode}")
    out = result.stdout
    found: list[tuple[str, str, bytes] | None] = []
    pos = 0
    for _name in names:
        end = out.find(b"\n", pos)
        if end < 0:
            raise RuntimeError(f"git cat-file {mode} gave a short answer.")
        header = out[pos:end].decode("utf-8", "replace")
        pos = end + 1
        known = re.match(r"^([0-9a-f]{40,64}) (\w+) (\d+)$", header)
        if known is None:  # "<name> missing" or "<name> ambiguous"
            found.append(None)
            continue
        data = b""
        if contents:
            size = int(known.group(3))
            data = out[pos:pos + size]
            pos += size + 1
        found.append((known.group(1), known.group(2), data))
    return found


def objects_at(repo_root: Path | str, treeish: str, paths: Sequence[Path | str], *,
               top: Path | None = None) -> list[GitObject | None]:
    """Read the objects *treeish* holds at each of *paths*, with one git command.

    Every path is read from the same *treeish*. Pass a commit SHA, not a
    ref that could move between the reads.

    Args:
        repo_root: A directory inside the repository (the project root).
        treeish: A commit or tree.
        paths: Files or directories, absolute or relative to *repo_root*.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        A :class:`GitObject` per path, or ``None`` where *treeish* holds
        nothing there (or is itself missing).

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        RuntimeError: git failed.
    """
    top = _top(repo_root, top)
    names = [f"{treeish}:{_tree_rel(repo_root, p, top)[1]}" for p in paths]
    return [GitObject(*hit) if hit else None
            for hit in _cat_file_batch(top, names, contents=True)]


def head_commit(repo_root: Path | str, *, top: Path | None = None) -> str | None:
    """Return the commit HEAD names, or ``None`` in a repository with none.

    Args:
        repo_root: A directory inside the repository (the project root).
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        The SHA, or ``None``.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
    """
    top = _top(repo_root, top)
    result = _git(["git", "rev-parse", "--verify", "-q", "HEAD"], top)
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else None


def object_exists(repo_root: Path | str, oid: str, *,
                  top: Path | None = None) -> bool:
    """Say whether the repository's object store holds *oid*.

    Args:
        repo_root: A directory inside the repository (the project root).
        oid: An object id.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        ``True`` when ``git cat-file -e`` finds it.
    """
    if top is None:
        top = git_toplevel(Path(repo_root).resolve())
    if top is None:
        return False
    return _git(["git", "cat-file", "-e", oid], top).returncode == 0


def read_blob_bytes(repo_root: Path | str, treeish: str,
                    path: Path | str, *, top: Path | None = None) -> bytes | None:
    """Return the bytes *treeish* holds at *path*, unconverted.

    Args:
        repo_root: A directory inside the repository (the project root).
        treeish: A commit or tree.
        path: The file, absolute or relative to *repo_root*.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        The blob's bytes, or ``None`` when *treeish* holds no file there.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
    """
    top, rel = _tree_rel(repo_root, path, top)
    result = _git(["git", "cat-file", "blob", f"{treeish}:{rel}"], top, binary=True)
    return result.stdout if result.returncode == 0 else None


def index_matches(repo_root: Path | str, commit: str, path: Path | str, *,
                  top: Path | None = None) -> bool:
    """Say whether the repository's index holds *path* as *commit* does.

    Args:
        repo_root: A directory inside the repository (the project root).
        commit: The commit to compare with.
        path: A file or directory, absolute or relative to *repo_root*.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        ``True`` when nothing is staged at *path* relative to *commit*.
    """
    top, rel = _tree_rel(repo_root, path, top)
    return _git(["git", "diff", "--cached", "--quiet", commit, "--", rel],
                top).returncode == 0


def _checked(result: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
    if result.returncode != 0:
        err = result.stderr.decode(errors="replace") if isinstance(
            result.stderr, bytes) else result.stderr
        raise RuntimeError(f"{what} failed: {err.strip()}")
    return result


def _ignored_rules(top: Path, env: dict[str, str], rels: list[str]) -> dict[str, str]:
    """Return ``{path: rule}`` for those of *rels* git's ignore rules leave out.

    Read with the index *env* names, so a path tracked there is not ignored,
    as ``git add`` would treat it. The paths go to one ``git check-ignore``
    through its stdin, NUL-separated, so a snapshot of any size fits (a
    command line naming each is capped, at 32,767 characters on Windows).

    Args:
        top: The working tree's top level.
        env: The variables naming the index to read (``GIT_INDEX_FILE``).
        rels: Paths relative to *top*, POSIX.

    Returns:
        ``{path: "<source>:<line>:<pattern>"}`` for each ignored path.

    Raises:
        RuntimeError: ``git check-ignore`` failed.
    """
    if not rels:
        return {}
    result = _git(["git", "check-ignore", "-v", "-z", "--stdin"], top, env=env,
                  binary=True,
                  input=b"".join(r.encode("utf-8") + b"\0" for r in rels))
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: "
                           f"{result.stderr.decode(errors='replace').strip()}")
    # -z -v: <source> NUL <line> NUL <pattern> NUL <path> NUL, per path.
    fields = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    rules: dict[str, str] = {}
    for i in range(0, len(fields) - 3, 4):
        source, line, pattern, path = fields[i:i + 4]
        if pattern and not pattern.startswith("!"):
            rules[path] = f"{source}:{line}:{pattern}"
    return rules


def _attributes(top: Path, rel: str) -> list[str]:
    """Return ``attr=value`` for each content attribute set on *rel*."""
    result = _git(["git", "check-attr", "-z", *_CONTENT_ATTRIBUTES, "--", rel], top)
    fields = result.stdout.split("\0")
    return [f"{fields[i + 1]}={fields[i + 2]}"
            for i in range(0, len(fields) - 2, 3)
            if fields[i + 2] not in ("unspecified", "")]


def tree_as_if_at(repo_root: Path | str, directory: Path | str, at: Path | str,
                  base: str | None, *,
                  top: Path | None = None) -> tuple[str, dict[str, str]]:
    """Return the tree git would commit for *directory* if it sat at *at*.

    Each file is stored as ``git add`` would store it at its path under
    *at*: that path's ignore rules (a file tracked at *base* is never
    ignored) and attributes (a clean filter, a line-ending conversion) apply.
    The blobs and the tree are written to the object store; no index the
    user has is read or written, and *directory* itself can be anywhere.

    Args:
        repo_root: A directory inside the repository (the project root).
        directory: The directory whose files are stored.
        at: The path, inside the work tree, the tree is built for.
        base: The commit whose tracked files and modes count, or ``None``.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        ``(tree, ignored)``: the tree's id and ``{file: rule}`` for each file
        of *directory* (relative, POSIX) the ignore rules left out.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        RuntimeError: A git command failed.
    """
    import tempfile

    top, rel = _tree_rel(repo_root, at, top)
    directory = Path(directory)
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    names = [p.relative_to(directory).as_posix() for p in files]
    trust_exec = _git(["git", "config", "--type=bool", "core.filemode"],
                      top).stdout.strip() != "false"
    with tempfile.TemporaryDirectory(prefix="wfc-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if base:
            _checked(_git(["git", "read-tree", base], top, env=env), "git read-tree")
        modes = {}
        for line in _git(["git", "ls-files", "-s", "--", rel], top,
                         env=env).stdout.splitlines():
            meta, _, path = line.partition("\t")
            modes[path] = meta.split()[0]
        ignored = _ignored_rules(top, env, [f"{rel}/{n}" for n in names])
        _checked(_git(["git", "rm", "--cached", "-r", "-q", "--ignore-unmatch",
                       "--", rel], top, env=env), "git rm --cached")
        # One entry per file, "<mode> <sha>\t<path>\0", fed to one
        # `git update-index --index-info` through stdin: argv would cap the
        # snapshot's size (32,767 characters on Windows).
        index_info: list[bytes] = []
        for path, name in zip(files, names):
            full = f"{rel}/{name}"
            if full in ignored:
                continue
            sha = _checked(_git(["git", "hash-object", "-w", f"--path={full}",
                                 str(path)], top), "git hash-object").stdout.strip()
            if trust_exec:
                mode = "100755" if os.access(path, os.X_OK) else "100644"
            else:
                mode = modes.get(full, "100644")
            index_info.append(f"{mode} {sha}\t{full}".encode() + b"\0")
        if not index_info:
            tree = EMPTY_TREE
        else:
            _checked(_git(["git", "update-index", "-z", "--index-info"], top, env=env,
                          binary=True, input=b"".join(index_info)),
                     "git update-index")
            tree = _checked(_git(["git", "write-tree", f"--prefix={rel}/"], top,
                                 env=env), "git write-tree").stdout.strip()
    return tree, {name: ignored[f"{rel}/{name}"] for name in names
                  if f"{rel}/{name}" in ignored}


def tree_differences(repo_root: Path | str, tree: str, directory: Path | str,
                     at: Path | str, ignored: dict[str, str], *,
                     top: Path | None = None) -> list[TreeDifference]:
    """Name each file of *directory* that *tree*, extracted as a run extracts it, lacks or changes.

    *tree* is extracted with :func:`archive_tree` and compared with
    *directory* byte for byte. Each difference carries its reason: the
    ignore rule (from *ignored*), or the attributes at the file's path under
    *at* that left it out of the extraction (``export-ignore``) or changed
    its bytes (a line-ending conversion, a filter).

    Args:
        repo_root: A directory inside the repository (the project root).
        tree: What :func:`tree_as_if_at` returned for *directory*.
        directory: The directory the tree was built from.
        at: The path the tree was built for.
        ignored: The ignored files :func:`tree_as_if_at` returned.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        The differences, sorted by path; empty when the extraction is
        *directory* exactly.
    """
    import tempfile

    top, rel = _tree_rel(repo_root, at, top)
    directory = Path(directory)
    want = {p.relative_to(directory).as_posix(): p
            for p in directory.rglob("*") if p.is_file()}
    with tempfile.TemporaryDirectory(prefix="wfc-extract-") as tmp:
        dest = Path(tmp) / "copy"
        dest.mkdir()
        archive_tree(repo_root, tree, dest, top=top)
        got = {p.relative_to(dest).as_posix(): p.read_bytes()
               for p in dest.rglob("*") if p.is_file()}
    out: list[TreeDifference] = []
    for name in sorted(set(want) | set(got)):
        if name in ignored:
            out.append(TreeDifference(name, f"git ignores it ({ignored[name]})"))
            continue
        if name in got and name in want and got[name] == want[name].read_bytes():
            continue
        attrs = _attributes(top, f"{rel}/{name}")
        set_on = f" (attributes: {', '.join(attrs)})" if attrs else ""
        if name not in got:
            reason = f"git archive leaves it out{set_on}"
        elif name not in want:
            reason = f"git archive adds it{set_on}"
        else:
            reason = f"git changes its bytes, a line-ending conversion or a filter{set_on}"
        out.append(TreeDifference(name, reason))
    return out


def commit_over_head(repo_root: Path | str, base: str | None, *,
                     trees: dict[Path | str, str] | None = None,
                     blobs: dict[Path | str, bytes] | None = None,
                     message: str, top: Path | None = None) -> str | None:
    """Commit *base*'s tree with *trees* and *blobs* put in, through ``git commit``.

    The commit is made from a temporary index holding *base*'s tree, each
    path of *trees* replaced by its tree and each path of *blobs* by its
    bytes (stored unconverted), so nothing else the user changed or staged
    is swept in and the user's index is never touched. ``git commit`` runs
    it, so the repository's hooks run, bounded by :func:`commit_timeout`.
    HEAD moves only from *base*: when HEAD is not *base* when the commit
    starts, or another commit lands first, nothing stays committed and
    :class:`HeadMovedError` is raised. The commit uses the fixed ``wfc``
    identity.

    Args:
        repo_root: A directory inside the repository (the project root).
        base: The commit to build on (the HEAD the caller read), or ``None``
            in a repository with no commit.
        trees: ``{directory path: tree id}`` to put in.
        blobs: ``{file path: bytes}`` to put in.
        message: The commit message.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Returns:
        The new commit's SHA, or ``None`` when the result is *base*'s tree.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        HeadMovedError: HEAD is not *base*.
        RuntimeError: A merge, cherry-pick or revert is in progress, a hook
            refused the commit, or git failed or did not finish in time.
    """
    import tempfile

    top = _top(repo_root, top)
    states = (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"),
              ("REVERT_HEAD", "revert"))
    # One git command reads the three states, HEAD and base's tree, in order.
    found = _cat_file_batch(top, [s for s, _ in states] + ["HEAD"]
                            + ([f"{base}^{{tree}}"] if base else []), contents=False)
    for (_state, what), hit in zip(states, found):
        if hit is not None:
            raise RuntimeError(
                f"git commit refused: a {what} is in progress in {top}. Finish "
                f"or abort it, then run the command again. Nothing was committed."
            )
    head = found[len(states)]
    if (head[0] if head else None) != base:
        raise HeadMovedError(f"HEAD moved from {base}; nothing was committed.")
    held_tree = found[len(states) + 1] if base else None
    base_tree = held_tree[0] if held_tree else None
    with tempfile.TemporaryDirectory(prefix="wfc-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if base:
            _checked(_git(["git", "read-tree", base], top, env=env), "git read-tree")
        for path, tree in (trees or {}).items():
            rel = _tree_rel(repo_root, path, top)[1]
            _checked(_git(["git", "rm", "--cached", "-r", "-q", "--ignore-unmatch",
                           "--", rel], top, env=env), "git rm --cached")
            if tree != EMPTY_TREE:
                _checked(_git(["git", "read-tree", f"--prefix={rel}/", tree], top,
                              env=env), "git read-tree --prefix")
        for i, (path, data) in enumerate((blobs or {}).items()):
            rel = _tree_rel(repo_root, path, top)[1]
            held = Path(tmp) / f"blob-{i}"
            held.write_bytes(data)
            sha = _checked(_git(["git", "hash-object", "-w", "--no-filters", str(held)],
                                top), "git hash-object").stdout.strip()
            _checked(_git(["git", "update-index", "--add", "--cacheinfo",
                           f"100644,{sha},{rel}"], top, env=env), "git update-index")
        tree = _checked(_git(["git", "write-tree"], top, env=env),
                        "git write-tree").stdout.strip()
        if base and tree == base_tree:
            return None
        try:
            commit = _git(["git", "-c", "user.email=wfc@wfc", "-c", "user.name=wfc",
                           "-c", "core.abbrev=40", "commit", "--no-status", "-m", message],
                          top, timeout=commit_timeout(), env=env)
        except GitTimeoutError as exc:
            _release_index_locks(top, exc.started)
            raise RuntimeError(f"git commit failed: {exc}. Nothing was committed.") from exc
    if commit.returncode != 0:
        if head_commit(top, top=top) != base:
            raise HeadMovedError(f"HEAD moved from {base} during the commit; "
                                 f"nothing was committed.")
        raise RuntimeError(f"git commit failed: {(commit.stderr or commit.stdout).strip()}")
    # The commit git made, from its own summary line ("[main 1a2b...] msg"):
    # HEAD read now could already be another process's commit.
    import re

    made = re.match(r"^\[.*?([0-9a-f]{40})\]", commit.stdout.strip())
    if made is None:
        raise RuntimeError(f"git commit said nothing of the commit it made: "
                           f"{commit.stdout.strip()!r}")
    new = made.group(1)
    parent = _git(["git", "rev-parse", "--verify", "-q", f"{new}^"], top).stdout.strip()
    if (parent or None) != base:
        # HEAD moved after it was checked and before git commit read it: the
        # tree, built on *base*, would undo that other commit. Take it back
        # off HEAD (compare-and-swap: only while HEAD is still this commit)
        # and let the caller rebuild.
        undone = parent and _git(
            ["git", "update-ref", "-m", "wfc: undo a commit built on a moved HEAD",
             "HEAD", parent, new], top).returncode == 0
        if not undone:
            raise RuntimeError(
                f"git commit {new[:12]} was built on {base} but landed on "
                f"{parent or 'no commit'}, and another commit followed it before "
                f"it could be taken back: revert {new[:12]} (`git revert "
                f"{new[:12]}`) to restore what it undid."
            )
        raise HeadMovedError(f"HEAD moved from {base} to {parent} before the "
                             f"commit; it was taken back off HEAD.")
    return new


def set_index_to(repo_root: Path | str, commit: str, paths: Sequence[Path | str],
                 attempts: int = 20, *, top: Path | None = None) -> None:
    """Make the user's index hold *paths* exactly as *commit* does.

    Only *paths* change; every other staged entry stays. Another git
    holding the index lock is waited for, briefly.

    Args:
        repo_root: A directory inside the repository (the project root).
        commit: The commit to take the entries from.
        paths: Files or directories, absolute or relative to *repo_root*.
        attempts: How many times to try while the index is locked.
        top: The working tree's top level when the caller resolved it
            (:func:`git_toplevel`); ``None`` resolves it.

    Raises:
        NotAGitRepositoryError: *repo_root* is not in a git working tree.
        RuntimeError: ``git reset`` failed, or the index stayed locked.
    """
    top = _top(repo_root, top)
    rels = [_tree_rel(repo_root, p, top)[1] for p in paths]
    for attempt in range(attempts):
        result = _git(["git", "reset", "-q", commit, "--", *rels], top)
        if result.returncode == 0:
            return
        if "index.lock" not in result.stderr:
            break
        time.sleep(0.05 * (attempt + 1))
    raise RuntimeError(f"git reset failed: {result.stderr.strip()}")


def commit_paths(
    repo_root: Path | str,
    paths: Sequence[Path | str],
    message: str,
    *,
    require_repo: bool = True,
) -> str | None:
    """Stage and commit exactly *paths*, and nothing else.

    Every commit wfc makes (init, method, module and env registration) goes
    through here, so every one names its paths. A path outside the working
    tree, or one git ignores, is skipped rather than force-added. Entries
    the user staged for other paths are neither committed nor unstaged: the
    commit is a pathspec commit, which takes only the named paths.

    Deletions inside a named directory are staged too (``git add -A``), so a
    snapshot that dropped a stale script commits the removal.

    The commit uses a fixed ``wfc`` identity, so the repository needs no git
    identity configured.

    Args:
        repo_root: A directory inside the repository (the project root).
        paths: Files or directories to commit, absolute or relative to
            *repo_root*.
        message: The commit message.
        require_repo: When ``True``, raise if *repo_root* is not inside a git
            working tree; when ``False``, return ``None`` without committing.

    Returns:
        The new commit's SHA, or ``None`` when nothing was committed (no
        repository and ``require_repo`` is ``False``, no path inside the
        tree, or every named path already committed as it stands).

    Raises:
        NotAGitRepositoryError: If *repo_root* is not in a git working tree
            and ``require_repo`` is ``True``.
        RuntimeError: If ``git add`` or ``git commit`` fails or does not
            finish within its timeout (its paths are unstaged either way).
        GitTimeoutError: If a git query before staging does not finish.
    """
    root = Path(repo_root).resolve()
    top = git_toplevel(root)
    if top is None:
        if require_repo:
            raise NotAGitRepositoryError(
                f"{str(root)!r} is not a git repository.\n"
                "Run `wfc init` before registering methods.\n"
                "wfc requires git to track method versions for cache-key "
                "computation."
            )
        return None

    # Resolve every path against the project root and keep those inside the
    # working tree, spelled relative to its top level with forward slashes.
    rels: list[str] = []
    for p in paths:
        full = Path(p)
        if not full.is_absolute():
            full = root / full
        full = full.resolve()
        try:
            rel = full.relative_to(top).as_posix()
        except ValueError:
            continue
        if rel in ("", "."):
            continue
        if rel not in rels:
            rels.append(rel)

    # A git-ignored path is skipped, never force-added. check-ignore exits 0
    # for an ignored path and 1 for one that is not. A tracked path is never
    # reported as ignored (check-ignore consults the index first).
    kept = []
    for rel in rels:
        ignored = _git(["git", "check-ignore", "-q", "--", rel], top)
        if ignored.returncode != 0:
            kept.append(rel)
    # A path that neither exists nor is tracked cannot be staged; skip it.
    staged_ok = []
    for rel in kept:
        if (top / rel).exists():
            staged_ok.append(rel)
            continue
        tracked = _git(["git", "ls-files", "--error-unmatch", "--", rel], top)
        if tracked.returncode == 0:
            staged_ok.append(rel)
    if not staged_ok:
        return None

    try:
        stage = _git(["git", "add", "-A", "--", *staged_ok], top)
        if stage.returncode != 0:
            raise RuntimeError(f"git add failed: {stage.stderr.strip()}")

        # Nothing new under the named paths: already committed as it stands.
        diff = _git(["git", "diff", "--cached", "--quiet", "--", *staged_ok],
                    top)
        if diff.returncode == 0:
            return None

        commit = _git(
            [
                "git",
                "-c", "user.email=wfc@wfc",
                "-c", "user.name=wfc",
                "commit", "-q", "-m", message,
                "--", *staged_ok,
            ],
            top, timeout=commit_timeout(),
        )
    except GitTimeoutError as exc:
        # A stopped add or commit is a refused commit: nothing half-done.
        _release_index_locks(top, exc.started)
        _unstage(top, staged_ok)
        raise RuntimeError(
            f"git commit failed: {exc}. Its paths were unstaged: "
            f"{', '.join(staged_ok)}"
        ) from exc
    if commit.returncode != 0:
        # Put the index back for the named paths, so a refused commit (a
        # hook, say) leaves nothing staged behind it.
        _unstage(top, staged_ok)
        raise RuntimeError(
            f"git commit failed: {(commit.stderr or commit.stdout).strip()}"
        )

    return _git(["git", "rev-parse", "HEAD"], top).stdout.strip()


def _unstage(top: Path, rels: list[str]) -> None:
    """Put the index back to ``HEAD`` for *rels* (drop them in a repo with none).

    Args:
        top: The working tree's top level.
        rels: Paths relative to *top*, as :func:`commit_paths` staged them.
    """
    has_head = _git(["git", "rev-parse", "--verify", "-q", "HEAD"],
                    top).returncode == 0
    unstage = (["git", "reset", "-q", "--", *rels] if has_head
               else ["git", "rm", "-r", "-q", "--cached",
                     "--ignore-unmatch", "--", *rels])
    _git(unstage, top)


def _release_index_locks(top: Path, since: float) -> None:
    """Remove the index locks a stopped git left behind.

    A git killed mid-commit cannot remove its ``index.lock`` (nor a pathspec
    commit's temporary ``next-index-*.lock``), and every later git command
    that writes the index would refuse to run. Only locks written since the
    stopped command started are removed: while it held them, nothing else
    could have taken them.

    Args:
        top: The working tree's top level.
        since: ``time.time()`` when the stopped command was launched.
    """
    git_dir = _git(["git", "rev-parse", "--absolute-git-dir"],
                   top).stdout.strip()
    if not git_dir:
        return
    for lock in [Path(git_dir) / "index.lock",
                 *Path(git_dir).glob("next-index-*.lock")]:
        try:
            if lock.stat().st_mtime >= since - 2:
                lock.unlink()
        except OSError:
            pass
