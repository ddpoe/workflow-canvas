"""wfc share: a read-only checklist of what a collaborator will be missing.

``wfc share`` prints six rows under its own heading -- the git remote, Docker
logged in to the registry, the registry base, every recorded image pushed,
every pushed GHCR package connected to the repository, and the method
versions that cannot run -- each non-ok row naming the command that fixes
it. It writes and commits nothing, never pushes, never logs in, and exits 0
on warnings; outside a project it exits 1.

Projects come from the scenario harness (``build_project``); the git remote
is added with real ``git remote add``; pushes go through ``wfc push-env``.
Docker and the registry are faked at the ``wfc.environments.docker``
wrappers and the GitHub CLI at ``wfc.environments.gh``'s process boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_image_labels,
    stub_docker_push,
    stub_docker_read_manifest,
    stub_docker_tag,
    stub_gh,
)
from tests.harness import Scenario, build_project, node, selector, wire
from wfc.environments.docker import ImageNotFoundError

ENV = "image-io"
REMOTE = "git@github.com:Lab/Tools-Project.git"
BASE = "ghcr.io/lab/tools-project"
PACKAGE = "lab/tools-project/image-io"
HELD = "sha256:" + "c" * 64


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _project(git_project, monkeypatch):
    """One env and one method whose v1 records the env's current image."""
    return build_project(
        Scenario(nodes=[selector(), node("a", inputs=[wire("sel")])],
                 samples=["S1"], pipeline_id="pipe-share", env_name=ENV,
                 env_backend="pixi"),
        root=git_project, monkeypatch=monkeypatch)


def _state(root: Path) -> tuple[str, str]:
    """HEAD and the full porcelain status, the two things share must not move."""
    return (_git(root, "rev-parse", "HEAD"),
            _git(root, "status", "--porcelain", "--untracked-files=all"))


def _no_push_or_tag(monkeypatch) -> None:
    def never(*args, **kwargs):
        raise AssertionError("wfc share must not tag or push")

    stub_docker_tag(monkeypatch, never)
    stub_docker_push(monkeypatch, never)


def _share(monkeypatch, capsys, root: Path) -> tuple[int, str]:
    from wfc.cli import cli_main

    monkeypatch.chdir(root)
    capsys.readouterr()
    before = _state(root)
    rc = cli_main(["share"])
    assert _state(root) == before, "wfc share changed the repository"
    return rc, capsys.readouterr().out


def _row(out: str, name: str) -> str:
    """The row named *name* and its fix-hint line, as share printed them."""
    lines = out.splitlines()
    at = next(i for i, line in enumerate(lines)
              if line.startswith("  [") and line[9:].split()[0] == name)
    rest = lines[at + 1:at + 2]
    hint = rest if rest and "->" in rest[0] else []
    return "\n".join([lines[at], *hint])


#: The content of the one image the daemon holds (HELD, under every name):
#: one layer named by its ID, the shape the harness's env record is made from.
HELD_CONTENT = {"layers": [HELD], "env": None, "user": None, "workdir": None}


def _never_the_registry(ref):
    raise AssertionError(f"the registry must not be asked ({ref})")


def _no_token(calls: list[list[str]]) -> None:
    """No gh call reads or prints the token (``gh auth status`` masks it)."""
    assert not any("token" in arg for call in calls for arg in call), calls
    assert all(call[:1] == ["api"] or call[:2] == ["auth", "status"]
               for call in calls), calls


@workflow(purpose="wfc share in a project missing each sharing item -- no git "
                  "remote, no registry base, Docker not logged in, an image "
                  "not pushed, a pushed package not connected to the "
                  "repository, and gh missing -- prints a warn row naming "
                  "the fix for each, exits 0, and leaves HEAD and git status "
                  "unchanged; nothing is tagged, pushed, or logged in")
def test_share_names_the_fix_for_every_missing_item(git_project, monkeypatch,
                                                    capsys):
    """Each non-ok row names its fix; nothing is written."""
    project = _project(git_project, monkeypatch)
    root = project.root
    stub_docker_image_inspect(monkeypatch, HELD)
    _no_push_or_tag(monkeypatch)

    口 = Step(step_num=1, name="No remote and no base",
             purpose="remote names gh repo create; base and images name "
                     "push-env --all --registry; the registry is not asked; "
                     "no line points at the GitHub guide page")
    stub_docker_read_manifest(monkeypatch, _never_the_registry)
    calls = stub_gh(monkeypatch)
    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    assert out.startswith("Sharing this project:"), out
    remote = _row(out, "remote")
    assert remote.startswith("  [WARN] remote"), remote
    assert f"gh repo create {root.name} --private --source . --push" in remote
    assert "wfc push-env --all --registry <url>" in _row(out, "base")
    assert "wfc push-env --all --registry <url>" in _row(out, "images")
    assert _row(out, "login").startswith("  [WARN] login")
    assert _row(out, "versions").startswith("  [  OK] versions"), out
    assert "See your images on GitHub" not in out, out

    口 = Step(step_num=2, name="A GitHub remote, Docker not logged in",
             purpose="base proposes the GHCR base through push-env --all; "
                     "login prints the gh auth token | docker login line "
                     "for the gh user; images names the unpushed image; the "
                     "last line names the GitHub guide page")
    _git(root, "remote", "add", "origin", REMOTE)
    stub_docker_read_manifest(monkeypatch, "unauthorized")
    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    assert _row(out, "remote").startswith("  [  OK] remote"), out
    base = _row(out, "base")
    assert base.startswith("  [WARN] base") and BASE in base, base
    assert "wfc push-env --all" in base
    login = _row(out, "login")
    assert login.startswith("  [WARN] login"), login
    assert ("gh auth token | docker login ghcr.io -u lab-user --password-stdin"
            in login), login
    images = _row(out, "images")
    assert images.startswith("  [WARN] images") and f"{ENV}:fp-" in images, images
    assert "wfc push-env --all" in images
    assert out.rstrip().endswith(
        "See your images on GitHub: https://workflow-canvas.readthedocs.io/"
        "en/latest/how-to/see-your-images-on-github.html"), out

    口 = Step(step_num=3, name="Pushed, but the package is not connected",
             purpose="push-env --all sets the base and pushes; share then "
                     "reads the package through gh api and warns with its "
                     "package page link and the connect button's name")
    stub_docker_tag(monkeypatch)
    stub_docker_push(monkeypatch)
    # The image was registered before the remote, so it has no source label:
    # push-env pushes a labelled copy of it.
    stub_docker_image_labels(monkeypatch)
    stub_docker_build(monkeypatch)
    stub_docker_image_content(monkeypatch, HELD_CONTENT)
    from wfc.cli import cli_main
    monkeypatch.chdir(root)
    assert cli_main(["push-env", "--all", "--yes"]) == 0
    _no_push_or_tag(monkeypatch)
    stub_docker_read_manifest(monkeypatch, "found")
    calls = stub_gh(monkeypatch, packages={PACKAGE: None})
    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    for name in ("remote", "login", "base", "images", "versions"):
        assert _row(out, name).startswith("  [  OK]"), out
    packages = _row(out, "packages")
    assert packages.startswith("  [WARN] packages"), packages
    assert ("https://github.com/users/lab/packages/container/package/"
            "tools-project%2Fimage-io") in packages
    assert '"Link this package to a repository" > "Connect Repository"' in packages
    assert "choose Lab/Tools-Project in the list" in packages
    _no_token(calls)

    口 = Step(step_num=4, name="gh is not installed",
             purpose="the packages row says it cannot tell and still prints "
                     "the package page link")
    calls = stub_gh(monkeypatch, installed=False)
    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    packages = _row(out, "packages")
    assert packages.startswith("  [WARN] packages") and "cannot tell" in packages
    assert "packages/container/package/tools-project%2Fimage-io" in packages
    assert '"Link this package to a repository" > "Connect Repository"' in packages
    assert "choose Lab/Tools-Project in the list" in packages

    口 = Step(step_num=5, name="Connected",
             purpose="gh api answers the project's repository: every row ok")
    calls = stub_gh(monkeypatch, packages={PACKAGE: "Lab/Tools-Project"})
    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    assert "[WARN]" not in out, out
    _no_token(calls)


@workflow(purpose="wfc share with an image pushed to GHCR while gh is logged "
                  "in without a packages scope (a default gh auth login) warns "
                  "in the packages row that gh's login lacks the scope and "
                  "names gh auth refresh with write:packages, or read:packages "
                  "to only pull, instead of saying a collaborator cannot pull "
                  "the package; with the scope added the same package reads "
                  "as connected and every row is ok")
def test_share_names_the_missing_gh_packages_scope(git_project, monkeypatch,
                                                   capsys):
    """A package read refused for want of a scope is named as that."""
    from tests.fixtures.fakes.gh import DEFAULT_LOGIN_SCOPES, LOGIN_SCOPES
    from wfc.cli import cli_main

    project = _project(git_project, monkeypatch)
    root = project.root
    stub_docker_image_inspect(monkeypatch, HELD)
    stub_docker_read_manifest(monkeypatch, "found")
    _git(root, "remote", "add", "origin", REMOTE)
    stub_docker_tag(monkeypatch)
    stub_docker_push(monkeypatch)
    stub_docker_image_labels(monkeypatch)
    stub_docker_build(monkeypatch)
    stub_docker_image_content(monkeypatch, HELD_CONTENT)
    monkeypatch.chdir(root)
    assert cli_main(["push-env", "--all", "--yes"]) == 0
    _no_push_or_tag(monkeypatch)

    口 = Step(step_num=1, name="gh logged in without a packages scope",
             purpose="GitHub answers the package read 404; the packages row "
                     "names the missing scope and the gh auth refresh line")
    calls = stub_gh(monkeypatch, packages={PACKAGE: "Lab/Tools-Project"},
                    scopes=DEFAULT_LOGIN_SCOPES)
    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    packages = _row(out, "packages")
    assert packages.startswith("  [WARN] packages"), packages
    assert "packages scope" in packages, packages
    assert ("gh auth refresh --hostname github.com --scopes write:packages"
            in packages), packages
    assert "read:packages" in packages, packages
    assert "cannot pull" not in packages, packages
    _no_token(calls)

    口 = Step(step_num=2, name="The scope added",
             purpose="The same package reads as connected: every row ok")
    calls = stub_gh(monkeypatch, packages={PACKAGE: "Lab/Tools-Project"},
                    scopes=LOGIN_SCOPES)
    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    assert "[WARN]" not in out, out
    _no_token(calls)


@workflow(purpose="wfc share with a method version whose image is neither on "
                  "this machine nor pushed lists it in the versions row with "
                  "the recreate command and wfc register-method, warns and "
                  "exits 0; with Docker not answering the row says it cannot "
                  "tell")
def test_share_lists_the_versions_that_cannot_run(git_project, monkeypatch,
                                                  capsys):
    """Share lists the versions that cannot run."""
    project = _project(git_project, monkeypatch)
    root = project.root
    _no_push_or_tag(monkeypatch)
    stub_docker_read_manifest(monkeypatch, _never_the_registry)
    stub_gh(monkeypatch)

    口 = Step(step_num=1, name="The image is gone",
             purpose="Never pushed and the daemon no longer holds it")
    stub_docker_image_inspect(monkeypatch,
                              ImageNotFoundError("Error: No such image"))
    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    row = _row(out, "versions")
    assert row.startswith("  [WARN] versions"), row
    assert "/a v1" in row
    assert f"wfc register-env {ENV} --force" in row
    assert "wfc register-method" in row

    口 = Step(step_num=2, name="Docker does not answer",
             purpose="The row warns that it cannot tell")
    stub_docker_image_inspect(monkeypatch, RuntimeError(
        "Cannot connect to the Docker daemon"))
    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    row = _row(out, "versions")
    assert row.startswith("  [WARN] versions") and "cannot tell" in row, row


def test_a_base_with_a_trailing_slash_is_read_the_same_by_every_reader(
        git_project, monkeypatch):
    """doctor, share and push-env's plan all read `<base>/` as `<base>`."""
    from wfc.environments import plan_push
    from wfc.execution.readiness import check_registry, share_checks
    from wfc.persistence import set_registry_url

    project = _project(git_project, monkeypatch)
    root = project.root
    _git(root, "remote", "add", "origin", REMOTE)
    set_registry_url(root, BASE + "/")
    _git(root, "commit", "-q", "-am", "set the registry base")
    stub_docker_image_inspect(monkeypatch, HELD)
    stub_docker_read_manifest(monkeypatch, "found")
    _no_push_or_tag(monkeypatch)
    stub_gh(monkeypatch)

    assert plan_push(root, None, recorded={}).base == BASE
    rows = {row.name: row for row in share_checks(root)}
    assert rows["base"].message == f"Registry base: {BASE}."
    assert f"in {BASE}: {ENV}:fp-" in rows["images"].message
    assert f"the registry {BASE} answers" in rows["login"].message
    doctor = check_registry(root)
    assert f"in the registry {BASE}: {ENV}:fp-" in doctor.message
    for text in (*(row.message for row in rows.values()), doctor.message):
        assert f"{BASE}/" not in text, text


@workflow(purpose="wfc share and doctor with an image record (pushed.json) "
                  "that cannot be read warn naming the env's unreadable record "
                  "in the images row and the versions row, never 'Docker did "
                  "not answer' and never a traceback")
def test_an_unreadable_image_record_is_named_not_a_traceback(
        git_project, monkeypatch, capsys):
    import json

    from wfc.execution.readiness import check_registry
    from wfc.persistence import set_registry_url

    project = _project(git_project, monkeypatch)
    root = project.root
    _git(root, "remote", "add", "origin", REMOTE)
    set_registry_url(root, BASE)
    _git(root, "commit", "-q", "-am", "set the registry base")
    stub_docker_image_inspect(monkeypatch, HELD)
    stub_docker_read_manifest(monkeypatch, "found")
    _no_push_or_tag(monkeypatch)
    stub_gh(monkeypatch)
    manifest = json.loads((root / ".wfc" / "envs.json").read_text(encoding="utf-8"))
    fingerprint = manifest["envs"][ENV]["env_fingerprint"]
    pushed = root / ".wfc" / "captures" / ENV / fingerprint / "pushed.json"
    pushed.write_text("{not json", encoding="utf-8", newline="\n")

    rc, out = _share(monkeypatch, capsys, root)
    assert rc == 0, out
    images = _row(out, "images")
    assert images.startswith("  [WARN] images"), images
    assert f"cannot read the image record for {ENV}" in images
    versions = _row(out, "versions")
    assert versions.startswith("  [WARN] versions"), versions
    assert "cannot be read" in versions and "/a v1" in versions
    assert "Docker did not answer" not in versions
    doctor = check_registry(root)
    assert doctor.status == "warn"
    assert f"cannot read the image record for {ENV}" in doctor.message


def test_share_outside_a_project_exits_1(tmp_path, monkeypatch, capsys):
    """The one refusal: no project to check."""
    from tests.fixtures.fakes import refuse_docker
    from wfc.cli import cli_main

    refuse_docker(monkeypatch)
    calls = stub_gh(monkeypatch)
    from wfc.persistence import reset_engine

    monkeypatch.delenv("WFC_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    reset_engine()  # the resolved root is cached per process
    assert cli_main(["share"]) == 1
    assert "No wfc project found" in capsys.readouterr().err
    assert calls == []


def test_package_connection_reads_the_org_route_after_a_user_404(monkeypatch):
    """An organisation's package is found under /orgs/ and its link says so."""
    from wfc.environments import gh

    seen = stub_gh(monkeypatch, packages={PACKAGE: "lab/tools-project"},
                   orgs={"lab"})
    conn = gh.package_connection("lab", "tools-project/image-io", "Lab/Tools-Project")
    assert conn.state == gh.PACKAGE_CONNECTED
    assert conn.page_url.startswith("https://github.com/orgs/lab/")
    assert [a[1] for a in seen] == [
        "/users/lab/packages/container/tools-project%2Fimage-io",
        "/orgs/lab/packages/container/tools-project%2Fimage-io"]


_ACCOUNT = ("  ✓ Logged in to github.com account {user} (keyring)\n"
            "  - Active account: {active}\n"
            "  - Token: gho_****\n"
            "  - Token scopes: {scopes}\n")


@pytest.mark.parametrize(("status", "lacks"), [
    # gh 2.4x, a default `gh auth login`.
    ("github.com\n" + _ACCOUNT.format(user="u", active="true",
                                      scopes="'gist', 'read:org', 'repo'"), True),
    # After `gh auth refresh --scopes write:packages`.
    ("github.com\n" + _ACCOUNT.format(
        user="u", active="true",
        scopes="'admin:public_key', 'gist', 'read:org', 'repo', 'write:packages'"),
     False),
    # Older gh: one account, unquoted, no Active line.
    ("github.com\n  ✓ Logged in to github.com as u (oauth_token)\n"
     "  ✓ Token scopes: gist, read:org, read:packages, repo\n", False),
    # Two accounts: the active one is read, wherever it is listed.
    ("github.com\n"
     + _ACCOUNT.format(user="a", active="false", scopes="'repo', 'write:packages'")
     + _ACCOUNT.format(user="b", active="true", scopes="'repo'"), True),
    # A token without classic scopes: gh cannot tell, so nothing is claimed.
    ("github.com\n" + _ACCOUNT.format(user="u", active="true", scopes="none"), False),
    ("You are not logged into any GitHub hosts.", False),
])
def test_the_packages_scope_is_read_from_the_active_logins_scopes(status, lacks):
    """Only a named scope list without a packages scope counts as lacking one."""
    from wfc.environments.gh import GhScopes, _active_scopes

    assert GhScopes(_active_scopes(status)).lacks_packages is lacks
