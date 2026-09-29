"""Tier 3 E2E tests for the `wfc init` setup wizard.

Covers the setup scenarios:
  - `init --yes <clean dir>` lands a live [dvc] at the default
    archive, a DVC cache, the DB, and a clean HEAD from the
    fallback-identity commit (no global git identity required).
  - Re-running `init --yes` clobbers / re-asks nothing.
  - git absent → scaffold still completes, exit 0, git surfaced in
    the summary, no repo; re-running with git present fills the gap.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from axiom_annotations import workflow, Step, AutoStep

from tests.fixtures.fakes import (
    fake_subprocess_run,
    redirect_home,
    stub_binary_lookup,
    stub_interactive_prompt,
)
from wfc.init import init_project


def _read_toml(project_dir):
    return tomllib.loads(
        (project_dir / ".wfc" / "wf-canvas.toml").read_text(encoding="utf-8")
    )


@workflow(
    purpose="init --yes scaffolds a runnable project with a live DVC archive "
            "and a clean committed HEAD from the fallback identity"
)
def test_init_yes_lands_runnable_project(tmp_path, monkeypatch):
    # Redirect the default archive location into a tmp HOME so the test does
    # not touch the developer's real ~/.wfc/archives.
    home = tmp_path / "home"
    home.mkdir()
    redirect_home(monkeypatch, home)
    project = tmp_path / "proj"

    口 = AutoStep(step_num=1)
    created = init_project(project, assume_yes=True)

    口 = Step(step_num=2, name="Live [dvc] archive at default location",
             purpose="wf-canvas.toml carries a live [dvc] url under the home archive dir")
    parsed = _read_toml(project)
    assert "dvc" in parsed
    url = parsed["dvc"]["url"]
    # Plain absolute path, not a URL — DVC's schema rejects the file://C:/
    # form drive-letter paths would produce.
    assert "://" not in url
    assert Path(url).is_absolute()
    assert "archives" in url
    # init_dvc wired the cache + .dvc/config + pre-created the local-FS dir.
    assert (project / ".dvc" / "config").exists()
    assert created["dvc"] is True

    口 = Step(step_num=3, name="Database created",
             purpose="The SQLite DB is scaffolded")
    assert (project / ".wfc" / "wfc.db").exists()

    口 = Step(step_num=4, name="Clean committed HEAD from fallback identity",
             purpose="git repo has a HEAD commit even with no global identity")
    assert (project / ".git").is_dir()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project),
        capture_output=True, text=True,
    )
    assert head.returncode == 0, "expected a HEAD commit from the initial commit"
    # Tracked tree is clean (the DB is gitignored, not committed).
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(project),
        capture_output=True, text=True,
    )
    tracked_dirty = [
        ln for ln in status.stdout.splitlines()
        if ln[:2].strip() and not ln.startswith("??")
    ]
    assert tracked_dirty == [], f"HEAD not clean: {tracked_dirty}"

    口 = Step(step_num=5, name="DB never tracked",
             purpose="The initial commit stages only .gitignore + wf-canvas.toml")
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(project),
        capture_output=True, text=True,
    ).stdout.splitlines()
    assert ".wfc/wfc.db" not in tracked


@workflow(
    purpose="Re-running init --yes changes nothing present (idempotent)"
)
def test_init_yes_is_idempotent(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    redirect_home(monkeypatch, home)
    project = tmp_path / "proj"

    口 = AutoStep(step_num=1)
    init_project(project, assume_yes=True)

    config_before = (project / ".wfc" / "wf-canvas.toml").read_bytes()
    dvc_config_before = (project / ".dvc" / "config").read_bytes()
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project),
        capture_output=True, text=True,
    ).stdout.strip()

    口 = Step(step_num=2, name="Second run",
             purpose="Re-run init --yes on the same project")
    created2 = init_project(project, assume_yes=True)

    口 = Step(step_num=3, name="Nothing clobbered or re-asked",
             purpose="config, DVC config, and HEAD are byte-identical; no new dirty tracked files")
    assert created2["wf-canvas.toml"] is False
    assert (project / ".wfc" / "wf-canvas.toml").read_bytes() == config_before
    assert (project / ".dvc" / "config").read_bytes() == dvc_config_before
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project),
        capture_output=True, text=True,
    ).stdout.strip()
    assert head_after == head_before, "second run created a new commit"
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(project),
        capture_output=True, text=True,
    )
    tracked_dirty = [
        ln for ln in status.stdout.splitlines()
        if ln[:2].strip() and not ln.startswith("??")
    ]
    assert tracked_dirty == [], f"re-run dirtied tracked files: {tracked_dirty}"


@workflow(
    purpose="git absent → scaffold completes (exit 0), git surfaced as not-ready, "
            "no repo; re-run with git present fills the gap"
)
def test_init_completes_when_git_absent(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    redirect_home(monkeypatch, home)
    project = tmp_path / "proj"

    口 = Step(step_num=1, name="git binary missing",
             purpose="Simulate git not on PATH for both init and the preflight summary")
    # _is_git_repo / git init use subprocess directly; make `git` raise
    # FileNotFoundError like a truly-missing binary.
    real_run = subprocess.run

    def _run_no_git(cmd, *a, **k):
        if cmd and cmd[0] == "git":
            raise FileNotFoundError("git")
        return real_run(cmd, *a, **k)

    stub_binary_lookup(monkeypatch, git=None)
    fake_subprocess_run(monkeypatch, _run_no_git)

    口 = Step(step_num=2, name="init still completes",
             purpose="Scaffold succeeds and returns without raising")
    created = init_project(project, assume_yes=True)
    assert (project / ".wfc" / "wf-canvas.toml").exists()
    assert (project / ".wfc" / "wfc.db").exists()
    assert not (project / ".git").exists()
    assert created[".git/"] is False

    口 = Step(step_num=3, name="git surfaced as not-ready in the summary",
             purpose="The health summary shows git FAIL with an install hint")
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "git" in out.lower()

    口 = Step(step_num=4, name="Re-run with git present fills only the gap",
             purpose="With git restored, the repo is created; config/DVC untouched")
    monkeypatch.undo()
    redirect_home(monkeypatch, home)
    config_before = (project / ".wfc" / "wf-canvas.toml").read_bytes()
    dvc_before = (project / ".dvc" / "config").read_bytes()

    created2 = init_project(project, assume_yes=True)
    assert (project / ".git").is_dir()
    assert created2[".git/"] is True
    assert (project / ".wfc" / "wf-canvas.toml").read_bytes() == config_before
    assert (project / ".dvc" / "config").read_bytes() == dvc_before


@workflow(
    purpose="init --yes is fully non-interactive — no input() prompt even "
            "when a git repo with a remote is present"
)
def test_init_yes_never_prompts(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    redirect_home(monkeypatch, home)
    project = tmp_path / "proj"
    project.mkdir()

    # A git repo with a remote present must still not prompt (this is the case
    # where a prompt could plausibly sneak in from a remote-derived default).
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(project), "remote", "add", "origin",
         "git@github.com:dante/myproject.git"],
        check=True, capture_output=True,
    )

    input_calls: list[str] = []
    stub_interactive_prompt(monkeypatch, "", prompts=input_calls)

    init_project(project, assume_yes=True)

    assert input_calls == [], (
        f"`wfc init --yes` must be fully non-interactive — got "
        f"{len(input_calls)} prompt(s): {input_calls!r}"
    )
