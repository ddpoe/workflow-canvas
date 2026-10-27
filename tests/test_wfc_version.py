"""The installed-wfc-version helper: the distribution's metadata, or None."""

from __future__ import annotations

import importlib.metadata
import logging

from tests.fixtures.fakes import stub_distribution_metadata_missing
from wfc.version import _DISTRIBUTION, installed_wfc_version


def test_returns_the_installed_distribution_version():
    """The helper answers what the installed metadata says."""
    assert installed_wfc_version() == importlib.metadata.version(_DISTRIBUTION)


def test_missing_metadata_returns_none_and_logs_a_warning(monkeypatch, caplog):
    """No metadata (a bare source tree): None and a warning, never an error."""
    stub_distribution_metadata_missing(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="wfc.version"):
        result = installed_wfc_version()

    assert result is None
    assert any(
        record.levelno == logging.WARNING and _DISTRIBUTION in record.getMessage()
        for record in caplog.records
    )


def test_unreadable_metadata_returns_none_and_logs_a_warning(monkeypatch, caplog):
    """Metadata that fails to read (not just a missing package): None and a
    warning naming the error, never an exception."""
    stub_distribution_metadata_missing(
        monkeypatch, error=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte"))

    with caplog.at_level(logging.WARNING, logger="wfc.version"):
        result = installed_wfc_version()

    assert result is None
    assert any(
        record.levelno == logging.WARNING and _DISTRIBUTION in record.getMessage()
        and "UnicodeDecodeError" in record.getMessage()
        for record in caplog.records
    )
