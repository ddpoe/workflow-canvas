"""Canonical JSON -- one byte string, and one hash, per JSON value.

A JSON value (a parsed pipeline document, for one) is turned into canonical
bytes: keys sorted, separators ``(",", ":")`` with no whitespace, non-ASCII
characters kept as themselves, UTF-8 encoded. Two spellings of one value --
another key order, other whitespace, other indentation -- give the same
bytes. NaN and the infinities, which JSON cannot carry, are refused. The md5
of the canonical bytes is the value's hash, the key its bytes are kept under
in the content-addressed cache.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Return the canonical UTF-8 bytes of a JSON value.

    Args:
        value: A JSON value: dicts with string keys, lists, strings, numbers,
            booleans and None, nested to any depth.

    Returns:
        The value serialised with sorted keys, separators ``(",", ":")`` and
        ``ensure_ascii=False``, encoded as UTF-8.

    Raises:
        ValueError: The value holds NaN or an infinity.
        TypeError: The value holds something JSON cannot serialise.
    """
    text = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def canonical_json_hash(value: Any) -> str:
    """Return the md5 hex digest of a JSON value's canonical bytes.

    Args:
        value: A JSON value, as :func:`canonical_json_bytes` takes it.

    Returns:
        32-character md5 hex digest of ``canonical_json_bytes(value)``.

    Raises:
        ValueError: The value holds NaN or an infinity.
        TypeError: The value holds something JSON cannot serialise.
    """
    return hashlib.md5(canonical_json_bytes(value)).hexdigest()
