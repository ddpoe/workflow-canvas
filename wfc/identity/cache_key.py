"""Cache key -- what is this run's identity.

SHA256 over the compact JSON list ``[KEY_RECIPE, code fingerprint, params,
input fingerprint, env fingerprint, "<module>.<method>"]``, the params
serialized with sorted keys. The recipe version is inside the hash, so a
change to what the key covers moves every key; the JSON list delimits the
components, so moving characters from one component to its neighbour moves
the key too. Each component moves the key; parameter key order does not.
"""

from __future__ import annotations

import hashlib
import json

KEY_RECIPE = 3
"""Version of the rule that composes the cache key.

Bump it whenever what the key covers or how it is composed changes; every
key then moves, so no result computed under an older rule is served under
the new one. Keyed runs record it as ``Run.key_recipe``. Recipe 3: the code
fingerprint covers every file of the method's copy, not its scripts alone.
"""


def build_cache_key(
    code_fingerprint: str,
    params: dict,
    input_fingerprint: str,
    env_fingerprint: str,
    method_identity: str,
) -> str:
    """Build a SHA256 cache key for a run under recipe ``KEY_RECIPE``.

    Pure function -- no DB access.  Uses the content-addressed code fingerprint
    (not git commit) so that unrelated commits do not invalidate cache keys.
    The key is the SHA256 of the compact JSON list ``[KEY_RECIPE,
    code_fingerprint, params, input_fingerprint, env_fingerprint,
    method_identity]``, with the params' keys sorted.

    Args:
        code_fingerprint: 64-char SHA256 hex digest from build_code_fingerprint().
        params: Parameter dict for this run (serialised deterministically).
        input_fingerprint: Output of build_input_fingerprint().
        env_fingerprint: 32-char MD5 hex digest from
            ``wfc.storage.store_env_content()``, identifying what the env's
            image contains (its layers, environment variables, user and
            working directory), not its name, tag, labels or registry.
            Folding env into the cache key means that a real change to the
            image invalidates the cache -- no more silent stale-env hits --
            while re-tagging or re-pushing an unchanged image does not.
        method_identity: The method's fully-qualified identity,
            ``"<module>.<method>"``.  WHICH method a run is a run of is part
            of what the key is over: without it, two modules registering a
            same-named method over the same sample, params, env and (for a
            one-line script, easily) the same code fingerprint compute one
            key, and the second module's step is served the first's outputs.

    Returns:
        64-char hex SHA256 string.
    """
    raw = json.dumps(
        [
            KEY_RECIPE,
            code_fingerprint,
            params,
            input_fingerprint,
            env_fingerprint,
            method_identity,
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()
