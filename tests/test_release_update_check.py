"""The canvas's once-a-day update check (request cases 4, 5, 6 and 11's server half).

Covers:
  - Tier 2 (case 4): the offer is the newest non-yanked release newer than
    the installed one; pre-releases follow the install unless
    ``prereleases`` overrides; a pre-release offer gets a pinned command.
  - Tier 2 (case 5): with the check off nothing is requested, read or
    written, and no offer is made.
  - Tier 2 (case 6): a second check within a day makes no request; offline,
    a timeout, a non-200 and an unreadable answer each leave no offer, no
    error and nothing cached that hides a later success.
  - Tier 2 (case 11): the offer carries the new version's Upgrading and
    Known issues when the public changelog was fetched, a link otherwise;
    a cached changelog without the offered version is fetched again; a
    daily refresh that makes an offer reads the changelog again (none
    without an offer), a failed read keeping the old notes.
  - Tier 2: the check runs on a background thread, one at a time, and the
    poll answers the cached offer without waiting for it.
  - Tier 1: the public changelog's URL comes from the Repository
    Project-URL.

Every request goes to the faked HTTP seam (``fakes.fake_release_http``);
the home is redirected (``fakes.redirect_home``) and each call is handed
Layout's ``update_check_path`` under it, the file the about route hands the
check, so the answer cached in ``~/.wfc/update-check.json`` is the test's own.
"""

from __future__ import annotations

import json
import threading

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import fake_release_http, redirect_home
from wfc import layout, release

PYPI = release.PYPI_JSON_URL
PUBLIC_CHANGELOG = "https://raw.githubusercontent.com/ddpoe/workflow-canvas/main/CHANGELOG.md"

T0 = 1_800_000_000.0
HOUR = 3600.0
DAY = 24 * HOUR


def _files(*, yanked: bool = False, count: int = 1) -> list[dict]:
    return [{"filename": f"workflow_canvas-{n}.whl", "yanked": yanked} for n in range(count)]


#: PyPI's JSON for workflow-canvas: stable and pre-releases, a yanked
#: release (every file yanked), a release with no files, and a label that is
#: not a version.
PYPI_ANSWER = {
    "info": {"version": "0.8.2"},
    "releases": {
        "0.7.0": _files(),
        "0.8.0b4": _files(),
        "0.8.0b5": _files(),
        "0.8.0": _files(),
        "0.8.1": _files(yanked=True, count=2),
        "0.8.2": _files(),
        "0.9.0b1": _files(),
        "0.9.0b2": _files(yanked=True),
        "0.9.0": [],
        "nightly": _files(),
    },
}

CHANGELOG_TEXT = """\
# Changelog

## [Unreleased]

### Added
- Not out yet.

## [0.9.0b1] — 2026-12-01

### Added
- A new view.

### Known issues
- The new view is slow on large projects.

### Upgrading
- Run `wfc doctor` once after upgrading.

## [0.8.2] — 2026-11-20

### Fixed
- A fix.

### Upgrading
- Nothing to do; your projects keep working.

### Known issues
- A node left on `"version": "draft"` runs the draft without a warning.
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The test's own home directory, where the check keeps its answer."""
    return redirect_home(monkeypatch, tmp_path / "home")


def _cache(home):
    """The file the about route hands the check: Layout's, under the test's home."""
    path = layout.update_check_path(home)
    assert path == home / ".wfc" / "update-check.json"
    return path


def _answers(**overrides) -> dict:
    answers = {PYPI: (200, PYPI_ANSWER), PUBLIC_CHANGELOG: (200, CHANGELOG_TEXT)}
    answers.update({PYPI if key == "pypi" else PUBLIC_CHANGELOG: value
                    for key, value in overrides.items()})
    return answers


@workflow(purpose="The offer is the newest non-yanked release newer than the "
                  "installed one, pre-releases included for a pre-release "
                  "install and excluded for a stable one unless prereleases "
                  "overrides, with the pip command that installs it")
@pytest.mark.parametrize("installed, prereleases, version, command", [
    ("0.8.0", None, "0.8.2", "pip install -U workflow-canvas"),
    ("0.8.0", True, "0.9.0b1", "pip install workflow-canvas==0.9.0b1"),
    ("0.8.0b4", None, "0.9.0b1", "pip install workflow-canvas==0.9.0b1"),
    ("0.8.0b4", False, "0.8.2", "pip install -U workflow-canvas"),
    ("0.8.2", None, None, None),
    ("0.9.0b1", True, None, None),
])
def test_the_offer_is_the_newest_release_the_install_takes(home, monkeypatch, installed,
                                                            prereleases, version, command):
    requests = fake_release_http(monkeypatch, _answers())

    offer = release.check_for_update(installed, check=True, prereleases=prereleases,
                                     path=_cache(home), now=T0)

    if version is None:
        assert offer is None
    else:
        assert (offer["version"], offer["command"]) == (version, command)
        assert offer["prerelease"] is ("b" in version)
    assert requests, "the check asked PyPI nothing"
    assert requests[0]["url"] == PYPI
    for sent in requests:
        assert sent["timeout"] == 3
        assert sent["headers"] == {"User-Agent": f"wfc/{installed}"}
    # The answer is cached; read back without the network it is the same offer.
    assert release.update_offer(installed, check=True, prereleases=prereleases,
                                path=_cache(home)) == offer


@workflow(purpose="A second check within a day makes no request and answers "
                  "the cached offer; a day later the check asks again")
def test_a_second_check_within_a_day_makes_no_request(home, monkeypatch):
    requests = fake_release_http(monkeypatch, _answers())

    first = release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0)
    asked = len(requests)
    again = release.check_for_update("0.8.0", check=True, prereleases=None,
                                     path=_cache(home), now=T0 + 23 * HOUR)

    assert first is not None and again == first
    assert len(requests) == asked
    assert not release.update_check_due("0.8.0", check=True, prereleases=None,
                                        path=_cache(home), now=T0 + 23 * HOUR)

    release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0 + DAY + 1)
    assert [sent["url"] for sent in requests[asked:]][:1] == [PYPI]


@workflow(purpose="Offline, a timeout, a non-200 or an unreadable answer leaves "
                  "no offer and no error, and caches nothing that would hide "
                  "the next check's success")
@pytest.mark.parametrize("pypi_answer", [
    OSError("network unreachable"),
    TimeoutError("timed out"),
    (503, "Service Unavailable"),
    (200, "<html>not json</html>"),
    (200, [1, 2, 3]),
    (200, {"releases": "none"}),
], ids=["offline", "timeout", "non-200", "not-json", "not-an-object", "no-release-table"])
def test_a_failed_check_leaves_no_offer_and_hides_no_later_success(home, monkeypatch,
                                                                     pypi_answer):
    answers = _answers(pypi=pypi_answer)
    requests = fake_release_http(monkeypatch, answers)

    offer = release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0)

    assert offer is None
    assert release.update_offer("0.8.0", check=True, prereleases=None, path=_cache(home)) is None
    assert not _cache(home).exists()

    answers[PYPI] = (200, PYPI_ANSWER)
    asked = len(requests)
    later = release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0 + 60)

    assert requests[asked]["url"] == PYPI
    assert later is not None and later["version"] == "0.8.2"


@workflow(purpose="With [updates] check off, the check sends no request, "
                  "neither reads nor writes its cached answer, and offers "
                  "nothing")
def test_with_the_check_off_nothing_is_requested_read_or_written(home, monkeypatch):
    requests = fake_release_http(monkeypatch, _answers())

    # Nothing cached: the check off creates nothing.
    assert release.check_for_update("0.8.0", check=False, prereleases=None, path=_cache(home), now=T0) is None
    assert release.poll_update("0.8.0", check=False, prereleases=None, path=_cache(home)) is None
    assert release.start_update_check("0.8.0", check=False, prereleases=None, path=_cache(home)) is False
    assert requests == []
    assert not (home / ".wfc").exists()

    # An offer cached while the check was on is neither served nor touched.
    assert release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0) is not None
    asked = len(requests)
    cached = _cache(home).read_bytes()
    stamp = _cache(home).stat().st_mtime_ns

    assert release.check_for_update("0.8.0", check=False, prereleases=None,
                                    path=_cache(home), now=T0 + 2 * DAY) is None
    assert release.update_offer("0.8.0", check=False, prereleases=None, path=_cache(home)) is None
    assert release.poll_update("0.8.0", check=False, prereleases=None, path=_cache(home)) is None
    assert len(requests) == asked
    assert _cache(home).read_bytes() == cached
    assert _cache(home).stat().st_mtime_ns == stamp


@workflow(purpose="When the public changelog was fetched, the offer carries "
                  "the new version's Upgrading and Known issues, items as "
                  "markdown, beside the link to all its notes")
def test_the_offer_carries_the_new_versions_upgrading_and_known_issues(home, monkeypatch):
    fake_release_http(monkeypatch, _answers())

    offer = release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0)

    notes = offer["notes"]
    assert [s["heading"] for s in notes["sections"]] == ["Upgrading", "Known issues"]
    assert notes["sections"][0]["items"] == ["Nothing to do; your projects keep working."]
    assert notes["sections"][1]["items"] == [
        'A node left on `"version": "draft"` runs the draft without a warning.']
    assert notes["link"] == release.changelog_url()


@workflow(purpose="When the public changelog could not be fetched the offer "
                  "carries a link instead, and the next check within the day "
                  "fetches only the changelog again until it holds the "
                  "offered version")
def test_without_the_changelog_the_offer_links_and_the_changelog_is_fetched_again(
        home, monkeypatch):
    answers = _answers(changelog=(404, "Not Found"))
    requests = fake_release_http(monkeypatch, answers)

    offer = release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0)

    assert offer["version"] == "0.8.2"
    assert offer["notes"] == {"sections": [], "link": release.changelog_url()}
    assert release.update_check_due("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0 + HOUR)

    # The changelog is on main but has no entry for the offer yet.
    answers[PUBLIC_CHANGELOG] = (200, CHANGELOG_TEXT.split("## [0.8.2]")[0])
    asked = len(requests)
    still = release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0 + HOUR)
    assert [sent["url"] for sent in requests[asked:]] == [PUBLIC_CHANGELOG]
    assert still["notes"]["sections"] == []

    answers[PUBLIC_CHANGELOG] = (200, CHANGELOG_TEXT)
    asked = len(requests)
    fetched = release.check_for_update("0.8.0", check=True, prereleases=None,
                                       path=_cache(home), now=T0 + 2 * HOUR)
    assert [sent["url"] for sent in requests[asked:]] == [PUBLIC_CHANGELOG]
    assert [s["heading"] for s in fetched["notes"]["sections"]] == ["Upgrading", "Known issues"]

    asked = len(requests)
    release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0 + 3 * HOUR)
    assert len(requests) == asked


@workflow(purpose="A daily refresh that makes an offer fetches the public "
                  "changelog again, so Known issues added to the offered "
                  "version after its release reach the offer; a failed "
                  "fetch keeps the old notes and still caches the new "
                  "release list; with no offer, no changelog is requested")
def test_a_daily_refresh_with_an_offer_fetches_the_changelog_again(home, monkeypatch):
    answers = _answers()
    requests = fake_release_http(monkeypatch, answers)
    first = release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0)
    assert first["notes"]["sections"][1]["items"] == [
        'A node left on `"version": "draft"` runs the draft without a warning.']

    # A day later 0.8.2's Known issues have grown on main.
    added = "- Exports of a nested group lose its colour.\n"
    answers[PUBLIC_CHANGELOG] = (200, CHANGELOG_TEXT + added)
    asked = len(requests)
    refreshed = release.check_for_update("0.8.0", check=True, prereleases=None,
                                         path=_cache(home), now=T0 + DAY + 1)
    assert [sent["url"] for sent in requests[asked:]] == [PYPI, PUBLIC_CHANGELOG]
    assert refreshed["notes"]["sections"][1]["items"] == [
        'A node left on `"version": "draft"` runs the draft without a warning.',
        "Exports of a nested group lose its colour."]
    assert release.update_offer("0.8.0", check=True, prereleases=None,
                                path=_cache(home)) == refreshed
    # Still at most once a day: within the day nothing is asked again.
    asked = len(requests)
    release.check_for_update("0.8.0", check=True, prereleases=None, path=_cache(home), now=T0 + DAY + HOUR)
    assert len(requests) == asked

    # The next day the changelog fails: the old notes stay, the new release list is cached.
    newer = json.loads(json.dumps(PYPI_ANSWER))
    newer["releases"]["0.8.3"] = _files()
    answers[PYPI] = (200, newer)
    answers[PUBLIC_CHANGELOG] = (503, "Service Unavailable")
    kept = release.check_for_update("0.8.0", check=True, prereleases=None,
                                    path=_cache(home), now=T0 + 2 * DAY + 2)
    assert kept["version"] == "0.8.3"
    assert json.loads(_cache(home).read_text())["checked_at"] == T0 + 2 * DAY + 2
    assert any(entry["version"] == "0.8.2"
               for entry in json.loads(_cache(home).read_text())["changelog"])


def test_a_daily_refresh_with_no_offer_requests_no_changelog(home, monkeypatch):
    requests = fake_release_http(monkeypatch, _answers())
    assert release.check_for_update("0.8.2", check=True, prereleases=None, path=_cache(home), now=T0) is None
    asked = len(requests)

    assert release.check_for_update("0.8.2", check=True, prereleases=None,
                                    path=_cache(home), now=T0 + DAY + 1) is None
    assert [sent["url"] for sent in requests[asked:]] == [PYPI]


@workflow(purpose="The poll answers the cached offer at once and starts the "
                  "check on a background thread when it is due, never a "
                  "second one while one is running")
def test_the_poll_answers_at_once_and_runs_one_check_in_the_background(home, monkeypatch):
    held = threading.Event()
    reached = threading.Event()

    def held_pypi(url):
        reached.set()
        assert held.wait(10), "the test never released the held request"
        return 200, json.dumps(PYPI_ANSWER)

    requests = fake_release_http(monkeypatch, _answers(pypi=held_pypi))

    assert release.poll_update("0.8.0", check=True, prereleases=None, path=_cache(home)) is None
    assert reached.wait(10), "the poll started no check"
    assert release.start_update_check("0.8.0", check=True, prereleases=None, path=_cache(home)) is False

    held.set()
    release.wait_for_update_check(10)

    offer = release.poll_update("0.8.0", check=True, prereleases=None, path=_cache(home))
    assert offer is not None and offer["version"] == "0.8.2"
    release.wait_for_update_check(10)
    assert [sent["url"] for sent in requests] == [PYPI, PUBLIC_CHANGELOG]


def test_the_public_changelog_is_read_from_the_repository_on_main():
    assert release.public_changelog_url() == PUBLIC_CHANGELOG
