"""Canonical JSON: one byte string, and one hash, per JSON value.

A pipeline document is kept in the cache under the hash of its canonical
form, so two documents that differ only in key order or whitespace must give
the same bytes, and a value JSON cannot carry (NaN, an infinity) is refused.
"""

from __future__ import annotations

import hashlib
import json
import math

import pytest

from wfc.identity import canonical_json_bytes, canonical_json_hash


def test_key_order_and_whitespace_give_the_same_bytes_and_hash():
    """Two spellings of one document are one canonical document."""
    compact = '{"nodes":[{"id":"a","params":{"x":1,"y":[1,2]}}],"edges":[]}'
    spaced = """
    {
        "edges": [ ],
        "nodes": [ { "params": { "y": [1, 2], "x": 1 }, "id": "a" } ]
    }
    """
    first, second = json.loads(compact), json.loads(spaced)

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_json_hash(first) == canonical_json_hash(second)


def test_bytes_are_sorted_compact_utf8_and_the_hash_is_their_md5():
    """Sorted keys, no spaces, non-ASCII kept as UTF-8; the hash is the md5."""
    value = {"b": "café", "a": [1, 2.5, None, True]}

    data = canonical_json_bytes(value)

    assert data == '{"a":[1,2.5,null,true],"b":"café"}'.encode()
    assert canonical_json_hash(value) == hashlib.md5(data).hexdigest()


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nan_and_infinities_are_refused(bad):
    """A value JSON cannot represent never gets a canonical form."""
    with pytest.raises(ValueError):
        canonical_json_bytes({"nodes": [{"params": {"x": bad}}]})
    with pytest.raises(ValueError):
        canonical_json_hash([bad])
