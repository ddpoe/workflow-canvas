"""The update check's HTTPS GETs: PyPI's JSON and the public changelog.

``wfc.release._http_get`` is the one place wfc reaches the network on its
own. No test may reach it for real: ``refuse_release_http`` is armed for
every test by ``tests/conftest.py``, and a test that wants answers arms
``fake_release_http`` over it (the later patch wins).
"""

from __future__ import annotations

import json

from ._entry import fake


def _body(value) -> bytes:
    """Return a canned answer's body as bytes (a dict or list as JSON)."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value).encode("utf-8")


@fake(
    boundary="wfc.release._http_get -- the update check's HTTPS GET of "
             "PyPI's JSON API and the public CHANGELOG.md",
    preserves="the URL, timeout and headers each request sends (recorded "
              "in order); the status and body each URL answers; an "
              "exception raised as the network raises it (OSError offline, "
              "TimeoutError on a timeout); a URL with no answer is offline",
    not_proven="anything about PyPI's or GitHub's real answers, TLS, a "
               "proxy, or urllib's own timeout handling",
    backed_by="undrivable: no test may reach PyPI or GitHub; the suite's "
              "network guard (refuse_release_http) refuses every unfaked call",
)
def fake_release_http(monkeypatch, answers: dict) -> list[dict]:
    """Answer the update check's requests from *answers*.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        answers: ``{url: (status, body)}``, ``{url: exception}`` or ``{url:
            callable}`` (called with the URL, returning ``(status, body)``;
            a test holds a request open with it). A body is bytes, text, or
            a dict or list sent as JSON. The dict is read at each request,
            so a test may change it between checks.

    Returns:
        The requests made, in order: ``{"url", "timeout", "headers"}`` each.
    """
    requests: list[dict] = []

    def _get(url: str, *, timeout: float, headers: dict) -> tuple[int, bytes]:
        requests.append({"url": url, "timeout": timeout, "headers": dict(headers)})
        answer = answers.get(url)
        if answer is None:
            raise OSError(f"no answer faked for {url} (offline)")
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            answer = answer(url)
        status, body = answer
        return status, _body(body)

    monkeypatch.setattr("wfc.release._http_get", _get)
    return requests


@fake(
    boundary="wfc.release._http_get -- every request the update check would "
             "send to the network",
    preserves="the check's own failure handling: a refused request raises "
              "OSError, as an offline machine does, and is recorded",
    not_proven="nothing about the network; it exists so that no test "
               "reaches it",
    backed_by="undrivable: no test may reach PyPI or GitHub; this guard is "
              "what forbids it",
)
def refuse_release_http(monkeypatch) -> list[str]:
    """Refuse every request the update check makes, and record its URL.

    Armed for every test by ``tests/conftest.py``, which fails the test that
    made a refused request, naming the URL.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The URLs refused, in order.
    """
    refused: list[str] = []

    def _refuse(url: str, *, timeout: float, headers: dict) -> tuple[int, bytes]:
        refused.append(url)
        raise OSError(f"the test suite refuses the network: {url}")

    monkeypatch.setattr("wfc.release._http_get", _refuse)
    return refused
