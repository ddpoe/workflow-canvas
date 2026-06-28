"""
Datetime/ISO-string -> epoch-ms parity for the ORM history reader.

The ORM read conversion introduced new datetime-handling logic: the raw
sqlite3 reader received timestamps as ISO **strings** (via ``_iso_to_epoch_ms``),
whereas SQLModel hands the same columns back as Python ``datetime`` objects.
``WfcProvider._to_epoch_ms`` must produce the **same** epoch-ms for both shapes,
or ``timestamp`` / ``duration`` / ``archivedAt`` would silently diverge between
the old and new read paths.

Tier 1: plain pytest — a tight edge-case unit on the conversion primitive.
"""

from __future__ import annotations

from datetime import datetime, timezone

from wfc.canvas.wfc_provider import WfcProvider


def _provider() -> WfcProvider:
    # _to_epoch_ms is a pure method that doesn't touch the DB; bypass __init__
    # (which requires an on-disk DB) to test the conversion in isolation.
    return WfcProvider.__new__(WfcProvider)


def test_iso_string_and_equivalent_datetime_yield_same_epoch_ms():
    """A known ISO string and the equivalent naive datetime both convert to the
    same specific epoch-ms (naive timestamps are treated as UTC)."""
    prov = _provider()
    iso = "2026-01-01 12:00:00.000000"
    dt_naive = datetime(2026, 1, 1, 12, 0, 0)
    dt_aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    expected = 1767268800000.0
    assert prov._to_epoch_ms(iso) == expected
    assert prov._to_epoch_ms(dt_naive) == expected
    assert prov._to_epoch_ms(dt_aware) == expected


def test_none_timestamp_is_zero():
    """A missing timestamp (None, e.g. an unstarted cancelled row) converts to 0
    in both the string and datetime paths."""
    prov = _provider()
    assert prov._to_epoch_ms(None) == 0
