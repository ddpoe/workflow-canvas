"""GitHub CLI boundary: the ``gh api`` reads ``wfc share`` makes.

``wfc.environments.gh`` starts every ``gh`` process through ``_run_gh``;
the one entry here replaces it with a GitHub that answers the login read,
the login's scopes and the package reads from canned values and refuses any
other ``gh`` command, so a test also proves ``gh auth token`` is never run.
"""

from __future__ import annotations

import subprocess
from collections.abc import Collection, Mapping
from urllib.parse import unquote

from ._entry import fake

#: What ``gh`` prints when it has no login.
_NOT_LOGGED_IN = ("You are not logged into any GitHub hosts. To log in, run: "
                  "gh auth login")

#: The scopes a ``gh auth login --scopes write:packages`` login holds.
LOGIN_SCOPES = ("gist", "read:org", "repo", "write:packages")

#: The scopes of a default ``gh auth login``: no packages scope.
DEFAULT_LOGIN_SCOPES = ("gist", "read:org", "repo")

_PACKAGE_SCOPES = {"read:packages", "write:packages"}


def _auth_status(user: str, scopes: Collection[str] | None) -> str:
    """``gh auth status --hostname github.com`` as gh 2.4x prints it."""
    lines = ["github.com",
             f"  ✓ Logged in to github.com account {user} (keyring)",
             "  - Active account: true",
             "  - Git operations protocol: https",
             "  - Token: gho_************************************"]
    if scopes is not None:
        lines.append("  - Token scopes: "
                     + (", ".join(f"'{s}'" for s in sorted(scopes)) or "none"))
    return "\n".join(lines) + "\n"


@fake(
    boundary="wfc.environments.gh._run_gh -- the gh CLI process: `gh api "
             "user` (the login), `gh auth status --hostname github.com` (the "
             "login's scopes) and `gh api /users|orgs/<owner>/packages/"
             "container/<package>` (the package's connected repository)",
    preserves="the argv wfc hands gh, recorded and checked against the three "
              "reads it may make; gh's exit code and its stdout/stderr shape "
              "(the --jq'd value, a 404 on stderr, auth status's `Token "
              "scopes:` line); GitHub's 404 for a package read by a login "
              "with no packages scope",
    not_proven="that GitHub answers those reads with that shape, that the "
               "--jq expressions select the fields GitHub returns, that the "
               "settings URLs printed are GitHub's, or that gh auth status "
               "prints its scopes line in that shape (taken from gh's "
               "output by hand; CI's token has no classic scopes to show)",
    backed_by="pm_mvp::tests.integration.test_ghcr_package_link::"
              "test_a_labelled_ghcr_push_keeps_its_label_and_gh_finds_the_package",
)
def stub_gh(monkeypatch, *, installed: bool = True, user: str | None = "lab-user",
            packages: Mapping[str, str | None] | None = None,
            orgs: Collection[str] = (),
            scopes: Collection[str] | None = LOGIN_SCOPES) -> list[list[str]]:
    """Replace ``_run_gh``. A true external edge: the gh CLI and GitHub's API.

    Considered: ``fake_subprocess_run`` (a whole-process ``subprocess.run``
    patch, which would also catch git and docker calls the same test makes
    for real or through their own entries); ``stub_binary_lookup`` (answers
    only whether a binary is on PATH, not what gh says). Neither stands at
    gh's own boundary.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        installed: ``False`` makes every call raise ``FileNotFoundError``,
            as starting a missing binary does.
        user: The login ``gh api user`` answers; ``None`` answers as gh does
            with no login.
        packages: ``"<owner>/<package>" -> "<owner>/<repo>"`` (connected) or
            ``None`` (not connected); a package not named answers 404 under
            both the user and the organisation route.
        orgs: Owners that are organisations: their packages answer only
            under ``/orgs/``, and 404 under ``/users/``.
        scopes: The classic scopes ``gh auth status`` names for the login
            (default: a ``--scopes write:packages`` login). A list with no
            packages scope makes every package read answer 404, as GitHub
            does; ``None`` prints no scopes line (a token kind without
            scopes) and leaves the package reads as configured.

    Returns:
        The argv of every gh call made, in order (without the leading ``gh``).
    """
    from wfc.environments import gh as gh_module

    calls: list[list[str]] = []
    known = dict(packages or {})

    def answer(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if not installed:
            raise FileNotFoundError(2, "The system cannot find the file specified", "gh")
        if args[:2] == ["api", "user"]:
            if user is None:
                return subprocess.CompletedProcess(["gh", *args], 1, "", _NOT_LOGGED_IN)
            return subprocess.CompletedProcess(["gh", *args], 0, f"{user}\n", "")
        if args == ["auth", "status", "--hostname", "github.com"]:
            if user is None:
                return subprocess.CompletedProcess(["gh", *args], 1, "", _NOT_LOGGED_IN)
            return subprocess.CompletedProcess(["gh", *args], 0,
                                               _auth_status(user, scopes), "")
        if args[:1] == ["api"] and "/packages/container/" in args[1]:
            if user is None:
                return subprocess.CompletedProcess(["gh", *args], 1, "", _NOT_LOGGED_IN)
            kind_owner, encoded = args[1].lstrip("/").split("/packages/container/", 1)
            kind, owner = kind_owner.split("/", 1)
            key = f"{owner}/{unquote(encoded)}"
            no_scope = scopes is not None and not _PACKAGE_SCOPES & set(scopes)
            if no_scope or key not in known or (kind == "orgs") != (owner in orgs):
                return subprocess.CompletedProcess(
                    ["gh", *args], 1, "", "gh: Package not found. (HTTP 404)")
            return subprocess.CompletedProcess(["gh", *args], 0,
                                               f"{known[key] or ''}\n", "")
        raise AssertionError(f"wfc must not run `gh {' '.join(args)}`")

    monkeypatch.setattr(gh_module, "_run_gh", answer)
    return calls
