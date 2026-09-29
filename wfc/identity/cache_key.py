"""Cache key -- what is this run's identity.

SHA256 over the code fingerprint, the parameters serialized as JSON with
sorted keys, the input fingerprint, the env fingerprint and the method's
module-qualified identity, concatenated in that order with no delimiter.
Each component moves the key; parameter key order does not.
"""

from __future__ import annotations

import hashlib
import json


def build_cache_key(
    code_fingerprint: str,
    params: dict,
    input_fingerprint: str,
    env_fingerprint: str,
    method_identity: str,
) -> str:
    """Build a SHA256 cache key for a run.

    Pure function -- no DB access.  Uses the content-addressed code fingerprint
    (not git commit) so that unrelated commits do not invalidate cache keys.

    Args:
        code_fingerprint: 64-char SHA256 hex digest from build_code_fingerprint().
        params: Parameter dict for this run (serialised deterministically).
        input_fingerprint: Output of build_input_fingerprint().
        env_fingerprint: 32-char MD5 hex digest from
            ``wfc.storage.store_env_content()``, identifying the resolved
            environment content (lock + pip freeze, or a container env's
            image fingerprint).  Folding env into the cache key means that
            installing a different numpy version between runs invalidates
            the cache -- no more silent stale-env hits.
        method_identity: The method's fully-qualified identity,
            ``"<module>.<method>"``.  WHICH method a run is a run of is part
            of what the key is over: without it, two modules registering a
            same-named method over the same sample, params, env and (for a
            one-line script, easily) the same code fingerprint compute one
            key, and the second module's step is served the first's outputs.

    Returns:
        64-char hex SHA256 string.
    """
    raw = (
        code_fingerprint
        + json.dumps(params, sort_keys=True)
        + input_fingerprint
        + env_fingerprint
        + method_identity
    )
    return hashlib.sha256(raw.encode()).hexdigest()
