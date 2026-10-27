"""The cache key records the recipe that built it, and its components are delimited.

Two promises of the key recipe, both pure (no DB, no Docker):

- The recipe version is inside the hash: a deliberate change to what the key
  covers bumps ``KEY_RECIPE``, and every key moves with it, so a result
  computed under the old rule is never served under the new one.
- The components are delimited: moving characters from one component to its
  neighbour gives a different key. The undelimited concatenation of recipe 1
  hashed both splits to the same bytes.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from axiom_annotations import workflow

from wfc.identity import KEY_RECIPE, build_cache_key

_PARAMS = {"threshold": 0.5}


def _recipe_key(recipe, code, params, input_fp, env_fp, method):
    """The documented recipe composition, hashed under the given version."""
    raw = json.dumps([recipe, code, params, input_fp, env_fp, method],
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


@workflow(purpose="The cache key equals the documented recipe composition led by "
                  "KEY_RECIPE: the key recipe version, then the code, params, input, "
                  "env and method, hashed together")
def test_key_recipe_version_is_inside_the_key():
    """The key is the recipe composition led by ``KEY_RECIPE``.

    The key equals the documented composition hashed with the current
    version as its first element. The version is hashed, so a bump moves
    every key without the components changing; a key built without the
    version would not equal this composition.
    """
    args = ("c" * 64, _PARAMS, "i" * 64, "e" * 32, "mod.method")

    assert build_cache_key(*args) == _recipe_key(KEY_RECIPE, *args)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(
            ("c" * 64, _PARAMS, "i" * 63 + "x", "y" + "e" * 31, "mod.method"),
            ("c" * 64, _PARAMS, "i" * 63, "xy" + "e" * 31, "mod.method"),
            id="input-env-boundary",
        ),
        pytest.param(
            ("c" * 64, _PARAMS, "i" * 64, "e" * 32, "mod.method"),
            ("c" * 64, _PARAMS, "i" * 64, "e" * 31, "emod.method"),
            id="env-method-boundary",
        ),
    ],
)
@workflow(purpose="Two splits of the same characters across adjacent key components "
                  "give different cache keys")
def test_adjacent_component_splits_give_different_keys(left, right):
    """Shifting a character across a component boundary moves the key.

    The fixture's two splits concatenate to the same characters (recipe 1's
    undelimited composition, written out here), which is exactly the
    collision that composition hashed alike.
    """
    def concatenated(code, params, input_fp, env_fp, method):
        return (code + json.dumps(params, sort_keys=True) + input_fp + env_fp
                + method)

    assert concatenated(*left) == concatenated(*right)
    assert build_cache_key(*left) != build_cache_key(*right)
