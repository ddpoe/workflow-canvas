"""
wfc/version.py — Git-commit discipline.

Public functions:
  get_git_commit(repo_path)             Fail-fast on dirty working tree.
  commit_paths(repo_root, paths, msg)   Commit exactly the named paths.

The identities themselves — the content hash, the code fingerprint, the
input fingerprint and the cache key — live in ``wfc.identity``; this module
composes none of them. The env fingerprint is ``wfc.environments``'
(``capture_env_content``, ``resolve_env_fingerprint``), and the MethodVersion
lookup is ``wfc.registration.get_or_create_version``.

Design notes:
  - Every git call is bounded (``GIT_TIMEOUT``; the commit, which runs the
    user's hooks, ``GIT_COMMIT_TIMEOUT``, or ``WFC_GIT_COMMIT_TIMEOUT`` when
    set for hooks that need longer). A git that does not finish is
    stopped with its children and reported loudly (:class:`GitTimeoutError`);
    a commit stopped this way leaves nothing staged behind it.
  - get_git_commit raises DirtyRepositoryError if there are uncommitted changes.
    Commit-then-run is the intended discipline; there is no --allow-dirty escape hatch.
  - git_commit is retained as optional audit metadata on MethodVersion, not part of
    the cache key.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .persistence import project_root

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
    if os.name == "nt":
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
         timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run one git command, bounded by a timeout, never reading a terminal.

    Args:
        args: The command line, starting with ``git``.
        cwd: Directory to run it in.
        timeout: Seconds allowed; ``None`` means :data:`GIT_TIMEOUT`.

    Returns:
        The finished process, with text stdout and stderr.

    Raises:
        GitTimeoutError: The command did not finish in time; it was stopped
            together with every process it started.
        FileNotFoundError: git is not installed (or *cwd* does not exist).
    """
    limit = GIT_TIMEOUT if timeout is None else timeout
    started = time.time()
    proc = subprocess.Popen(
        args, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
        start_new_session=(os.name != "nt"),
    )
    try:
        out, err = proc.communicate(timeout=limit)
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
    """Return the current HEAD git commit SHA (40 hex chars).

    Args:
        repo_path: Directory within the git repository. Defaults to the
            resolved workflow-canvas project root (not ``Path.cwd()`` — the
            Snakemake hot path runs with cwd rewritten to ``C:\\Windows`` on
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
    status = _git(["git", "status", "--porcelain"], cwd)
    if status.returncode != 0:
        raise RuntimeError(
            f"git status failed in {cwd!r}: {status.stderr.strip()}"
        )
    # Only tracked-file changes (modified, staged, deleted, renamed) block the
    # run.  Untracked files (??) do not affect the commit SHA cache key, so
    # they are ignored here.
    tracked_dirty = [
        line for line in status.stdout.strip().splitlines()
        if line[:2].strip() and not line.startswith("??")
    ]
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


def commit_paths(
    repo_root: Path | str,
    paths: list[Path | str],
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
