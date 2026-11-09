"""Project git and Environments: the Report a bug page and the Docker version it names.

Tier 2: ``wfc.release.bug_report_url`` builds the GitHub new-issue URL the
canvas's Report a bug opens, from values its caller hands it (the installed
version and Docker's server version); ``wfc.environments.docker_server_version``
is the short probe that answers the latter, ``None`` when Docker does not
answer. Nothing is sent: the URL only opens a page the user edits.
"""
from __future__ import annotations

import getpass
import platform
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from axiom_annotations import workflow

from tests.fixtures.fakes import canned_process, fake_subprocess_run
from wfc.environments import docker_server_version
from wfc.release import bug_report_url, issues_url


def _body(url: str) -> str:
    """Return the decoded ``body`` of a new-issue URL, checking it is the only field."""
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    assert set(query) == {"body"}, "the title stays empty: the user writes it"
    return query["body"][0]


@workflow(purpose="The Report a bug URL is the Issues page's /new with only a body: the "
                  "headings What happened, What I expected and Steps to reproduce, then "
                  "one line naming the wfc version, the platform, Python and Docker's "
                  "server version, and no project path or user name",
          inputs="An installed version and a Docker server version, as values",
          outputs="The new-issue URL")
def test_the_report_names_the_versions_under_the_three_headings(tmp_project):
    """Case 12's server half: the body's content."""
    url = bug_report_url("0.8.0b4", "27.3.1")

    assert url.startswith(issues_url() + "/new?body=")
    body = _body(url)
    headings = ["What happened", "What I expected", "Steps to reproduce"]
    positions = [body.index(f"### {heading}") for heading in headings]
    assert positions == sorted(positions)
    last_line = body.rstrip().splitlines()[-1]
    assert last_line == (f"wfc 0.8.0b4 · {platform.platform()} · "
                         f"Python {platform.python_version()} · Docker 27.3.1")
    assert str(Path.cwd()) not in body and str(tmp_project) not in body
    assert getpass.getuser() not in body


def test_a_docker_that_does_not_answer_reads_not_running():
    body = _body(bug_report_url("0.8.0", None))

    assert body.rstrip().splitlines()[-1].endswith(" · Docker not running")


def test_an_unknown_wfc_version_still_builds_the_report():
    body = _body(bug_report_url(None, "27.3.1"))

    assert body.rstrip().splitlines()[-1].startswith("wfc unknown · ")


@workflow(purpose="The Docker probe asks the server's version with a short timeout and "
                  "answers it; a daemon that is down, a missing docker, a timeout or an "
                  "empty answer each give None, never an error",
          inputs="docker version answers faked at subprocess.run",
          outputs="docker_server_version's answer")
def test_the_docker_probe_answers_the_server_version_or_none(monkeypatch):
    """Case 12's probe half: the version, or None for every failure."""
    seen = []

    def answering(cmd, *args, **kwargs):
        seen.append((cmd, kwargs.get("timeout")))
        return subprocess.CompletedProcess(cmd, 0, stdout="27.3.1\n", stderr="")

    fake_subprocess_run(monkeypatch, answering, only="docker")
    assert docker_server_version() == "27.3.1"
    cmd, timeout = seen[0]
    assert cmd[:2] == ["docker", "version"] and "{{.Server.Version}}" in cmd
    assert timeout is not None and timeout <= 5

    fake_subprocess_run(monkeypatch, canned_process(
        1, stdout="", stderr="Cannot connect to the Docker daemon"), only="docker")
    assert docker_server_version() is None

    fake_subprocess_run(monkeypatch, canned_process(0, stdout="\n"), only="docker")
    assert docker_server_version() is None

    for error in (FileNotFoundError("docker"),
                  subprocess.TimeoutExpired(["docker", "version"], 5)):
        def raising(cmd, *args, _error=error, **kwargs):
            raise _error
        fake_subprocess_run(monkeypatch, raising, only="docker")
        assert docker_server_version() is None
