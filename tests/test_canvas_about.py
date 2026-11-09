"""The canvas's about routes: the version menu's answer and the bug report.

Covers:
  - Tier 2 (case 5 at the route): with ``[updates] check = false`` the
    about route sends no request and makes no offer; with no ``[updates]``
    section the check runs and its offer is served from the next ask.
  - Tier 2 (case 6 at the route): the about route answers while the check's
    request is still held open: it never waits on the network.
  - Tier 2: the answer names the installed version, its changelog (or the
    Changelog link), the footer's state and sentence for each setting, and
    the guide links in the installed version's docs channel.
  - Tier 2: an invalid ``[updates]`` still answers the version and the
    changelog, with no offer and the config's refusal as the footer text;
    a missing ``wf-canvas.toml`` answers the same, the footer naming it.
  - Tier 2 (case 12 at the route): the report route answers the new-issue
    URL holding the wfc version and Docker's, ``not running`` when Docker
    does not answer, and no project path.

The update check's requests go to the faked HTTP seam
(``fakes.fake_release_http``) and the home is redirected
(``fakes.redirect_home``), so the cached answer is the test's own. The
Docker probe is faked at ``subprocess.run`` for ``docker`` only.
"""

from __future__ import annotations

import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import (
    canned_process,
    fake_release_http,
    fake_subprocess_run,
    redirect_home,
)
from wfc import layout, release
from wfc.persistence import project_root

PYPI = release.PYPI_JSON_URL

#: PyPI's answer: one stable release newer than any installed wfc.
PYPI_ANSWER = {"releases": {"99.0.0": [{"filename": "workflow_canvas-99.0.0.whl",
                                        "yanked": False}]}}

STABLE_ONLY = "Stable releases only. For betas, set `prereleases = true` in wf-canvas.toml"
BETAS = "Stable and beta releases. For stable only, set `prereleases = false` in wf-canvas.toml"
OFF = "Update check is off. To turn it on, set `check = true` in wf-canvas.toml"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The test's own home directory, where the check keeps its answer."""
    return redirect_home(monkeypatch, tmp_path / "home")


def _set_updates(root, block: str) -> None:
    """Replace the project's ``[updates]`` section with *block* ("" drops it)."""
    path = layout.marker_path(root)
    text = path.read_text(encoding="utf-8")
    head = text.split("# Update check.")[0]
    path.write_text(head + block, encoding="utf-8", newline="\n")


def _installed() -> str | None:
    return release.installed_wfc_version()


@workflow(purpose="With [updates] check = false the about route sends no "
                  "request, writes no cached answer and makes no offer; the "
                  "footer says the check is off")
def test_with_the_check_off_the_about_route_asks_nothing(canvas_client, tmp_project,
                                                         home, monkeypatch):
    _set_updates(tmp_project, "[updates]\ncheck = false\n")
    requests = fake_release_http(monkeypatch, {PYPI: (200, PYPI_ANSWER)})

    for _ in range(2):
        answer = canvas_client.get("/api/about")
        assert answer.status_code == 200
        body = answer.json()
        assert body["offer"] is None
        assert body["footer"] == {"state": "off", "text": OFF}
    release.wait_for_update_check(10)

    assert requests == []
    assert not (home / ".wfc" / "update-check.json").exists()


@workflow(purpose="With no [updates] section the about route starts the "
                  "check, and the next ask serves its offer with the pip "
                  "command and the link to the version's notes")
def test_with_no_updates_section_the_check_runs_and_its_offer_is_served(
        canvas_client, tmp_project, home, monkeypatch):
    _set_updates(tmp_project, "")
    requests = fake_release_http(monkeypatch, {PYPI: (200, PYPI_ANSWER)})

    first = canvas_client.get("/api/about").json()
    assert first["offer"] is None
    assert release.wait_for_update_check(10)
    assert [sent["url"] for sent in requests][:1] == [PYPI]

    offer = canvas_client.get("/api/about").json()["offer"]
    assert offer["version"] == "99.0.0"
    assert offer["prerelease"] is False
    assert offer["command"] == "pip install -U workflow-canvas"
    assert offer["notes"]["link"] == release.changelog_url()


@workflow(purpose="The about route answers the installed version while the "
                  "update check's request is still held open: it never "
                  "waits on the network")
def test_the_about_route_never_waits_on_the_network(canvas_client, home, monkeypatch):
    held = threading.Event()
    reached = threading.Event()

    def held_pypi(url):
        reached.set()
        held.wait(10)
        return 200, PYPI_ANSWER

    fake_release_http(monkeypatch, {PYPI: held_pypi})
    try:
        started = time.monotonic()
        answer = canvas_client.get("/api/about")
        elapsed = time.monotonic() - started
        assert reached.wait(10), "the about route started no check"
        assert not held.is_set()
    finally:
        held.set()
        release.wait_for_update_check(10)

    assert answer.status_code == 200
    assert answer.json()["version"] == _installed()
    assert elapsed < 5


@workflow(purpose="The about answer names the installed version, its "
                  "changelog (every version since the one the page saw last "
                  "when asked), the footer for each setting, and the guide "
                  "links in the installed version's docs channel")
@pytest.mark.parametrize("block, state, text", [
    ("[updates]\ncheck = true\nprereleases = true\n", "prereleases", BETAS),
    ("[updates]\ncheck = true\nprereleases = false\n", "stable", STABLE_ONLY),
])
def test_the_about_answer_names_the_version_changelog_footer_and_links(
        canvas_client, tmp_project, home, monkeypatch, block, state, text):
    _set_updates(tmp_project, block)
    fake_release_http(monkeypatch, {PYPI: (200, PYPI_ANSWER)})
    installed = _installed()

    body = canvas_client.get("/api/about").json()
    assert body["version"] == installed
    assert body["changelog"] == release.installed_changelog(installed)
    assert body["footer"] == {"state": state, "text": text}
    assert body["links"] == {
        "docs": release.docs_url(installed),
        "known_issues": release.docs_url(installed, "reference/known-issues.html"),
        "updates_guide": release.docs_url(installed, "reference/wf-canvas-toml.html#updates"),
        "plugin_install": release.docs_url(
            installed, "tutorials/work-with-an-ai-agent.html#install"),
        "changelog": release.changelog_url(),
    }

    since = canvas_client.get("/api/about", params={"since": "0.1.0"}).json()
    assert since["changelog"] == release.installed_changelog(installed, "0.1.0")


def test_the_footer_follows_the_install_when_prereleases_is_unset(
        canvas_client, tmp_project, home, monkeypatch):
    _set_updates(tmp_project, "[updates]\ncheck = true\n")
    fake_release_http(monkeypatch, {PYPI: (200, PYPI_ANSWER)})
    installed = _installed()

    footer = canvas_client.get("/api/about").json()["footer"]
    beta = installed is not None and release.includes_prereleases(installed, None)
    assert footer == ({"state": "prereleases", "text": BETAS} if beta
                      else {"state": "stable", "text": STABLE_ONLY})


@workflow(purpose="An invalid [updates] still answers the installed version "
                  "and its changelog, with no offer, no request, and the "
                  "config's refusal as the footer text")
def test_an_invalid_updates_section_still_answers_the_version_and_changelog(
        canvas_client, tmp_project, home, monkeypatch):
    _set_updates(tmp_project, '[updates]\ncheck = "yes"\n')
    requests = fake_release_http(monkeypatch, {PYPI: (200, PYPI_ANSWER)})
    installed = _installed()

    answer = canvas_client.get("/api/about")
    release.wait_for_update_check(10)
    assert answer.status_code == 200
    body = answer.json()
    assert body["version"] == installed
    assert body["changelog"] == release.installed_changelog(installed)
    assert body["offer"] is None
    assert body["footer"]["state"] == "invalid"
    assert '[updates] check = "yes"' in body["footer"]["text"]
    assert requests == []


@workflow(purpose="With no wf-canvas.toml the about route still answers the "
                  "installed version, its changelog and the links, with no "
                  "offer, no request, and a footer naming the missing file")
def test_a_missing_config_still_answers_the_version_and_changelog(
        canvas_client, tmp_project, home, monkeypatch):
    # The server resolved its project while the file was there (as `wfc
    # canvas` does at start); the file goes while the canvas is open.
    assert project_root() == tmp_project
    marker = layout.marker_path(tmp_project)
    marker.unlink()
    requests = fake_release_http(monkeypatch, {PYPI: (200, PYPI_ANSWER)})
    installed = _installed()

    answer = canvas_client.get("/api/about")
    release.wait_for_update_check(10)
    assert answer.status_code == 200
    body = answer.json()
    assert body["version"] == installed
    assert body["changelog"] == release.installed_changelog(installed)
    assert body["links"]["changelog"] == release.changelog_url()
    assert body["offer"] is None
    assert body["footer"]["state"] == "invalid"
    assert marker.name in body["footer"]["text"]
    assert requests == []
    assert not (home / ".wfc" / "update-check.json").exists()


def _report_body(url: str) -> str:
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    assert set(query) == {"body"}
    return query["body"][0]


@workflow(purpose="The report route answers the new-issue URL naming the "
                  "installed wfc and Docker's server version, not running "
                  "when Docker does not answer, and no project path")
def test_the_report_route_answers_the_prefilled_new_issue_url(canvas_client, tmp_project,
                                                              monkeypatch):
    def answering(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="27.3.1\n", stderr="")

    fake_subprocess_run(monkeypatch, answering, only="docker")
    answer = canvas_client.get("/api/about/report")
    assert answer.status_code == 200
    url = answer.json()["url"]
    assert url.startswith(release.issues_url() + "/new?body=")
    body = _report_body(url)
    assert f"wfc {_installed() or 'unknown'} · " in body
    assert body.rstrip().endswith("Docker 27.3.1")
    assert str(tmp_project) not in body and tmp_project.name not in body

    fake_subprocess_run(monkeypatch, canned_process(
        1, stdout="", stderr="Cannot connect to the Docker daemon"), only="docker")
    body = _report_body(canvas_client.get("/api/about/report").json()["url"])
    assert body.rstrip().endswith("Docker not running")
