"""Input fingerprint -- what did this run consume, and into which slot.

The part alphabet, the digest, and their composition. Each input's identity
-- an upstream run's cache key, a sample's content hash, as read from its
row by the caller -- is rendered as one tagged part; the parts are sorted,
joined by commas and hashed with SHA256. The sort means the order inputs
are handed in never moves the key. An empty part list is legal and yields
the digest of the empty string; refusing it is the caller's decision.

An input's identity is WHAT it is **and where it goes**. A value alone does
not say what was computed from it: the same upstream output wired into a
different input slot, or a different output of the same upstream, or two
samples swapped between two slots, is a different computation with the same
set of values. Every form therefore carries the step's input slot as its
second field, and an upstream's part carries the source slot it was taken
from as well.

The alphabet has three forms and this module is the only place that spells
them:

- ``key:<input_slot>:<source_slot>:<cache_key>`` -- an upstream run's output;
- ``key:<input_slot>:<source_slot>:legacy-run-<id>`` -- the same, for an
  upstream run whose row has no cache key;
- ``hash:<input_slot>:<content_hash>`` -- a sample.

A sample has exactly one form. There is no path/size/mtime spelling for a
sample whose row carries no content hash: such a row is a malformed record,
refused where the whole sample set is first visible
(``wfc.registration.load_sample_hashes``) and again by ``restore_sample``,
not keyed on a weaker identity here. ``render_sample_part`` raises rather
than inventing a part, but that is a backstop on a pure function -- by the
time a fingerprint is being built the record has already been refused.

Nothing here reads a row, and nothing here resolves a slot. The reads --
``Run.cache_key`` by id and ``Sample.content_hash`` by name -- the refusal
on a row that is not found, and the resolution of an entry that names no
source slot to the upstream's one recorded output, are all the caller's
(``wfc.execution.claim.input_fingerprint_from_rows`` beside ``pre_run``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from axiom_annotations import task


@dataclass(frozen=True)
class UpstreamRunIdentity:
    """An upstream run's identity and the wiring it arrives through.

    Attributes:
        input_slot: The consuming step's input slot this output feeds.
        source_slot: The upstream run's output slot this is taken from.
            Always resolved -- the caller turns an entry that names no output
            into the upstream's one recorded output before building this.
        run_id: The run's id -- the legacy-run sentinel is a function of it.
        cache_key: The run's cache key, or ``None`` when the row has none
            (a run that predates cache keys).
    """

    input_slot: str
    source_slot: str
    run_id: int
    cache_key: str | None = None


@dataclass(frozen=True)
class SampleIdentity:
    """A sample's identity and the input slot it is read into.

    Attributes:
        input_slot: The consuming step's input slot the sample feeds.
        content_hash: The DVC content hash (MD5). Required -- a sample's
            identity IS its content, and a row carrying no hash is a
            malformed record refused before a fingerprint is built.
    """

    input_slot: str
    content_hash: str


def render_run_part(run: UpstreamRunIdentity) -> str:
    """Render an upstream run's part: the wiring, then its cache key.

    Args:
        run: The run's identity and the wiring it arrives through.

    Returns:
        ``key:<input_slot>:<source_slot>:<cache_key>``, or
        ``key:<input_slot>:<source_slot>:legacy-run-<id>`` when the row has
        no cache key.
    """
    wiring = f"{run.input_slot}:{run.source_slot}"
    if run.cache_key:
        return f"key:{wiring}:{run.cache_key}"
    # Upstream run has no cache key -- use a sentinel so the
    # fingerprint is still deterministic (always produces the same value
    # for this run).
    return f"key:{wiring}:legacy-run-{run.run_id}"


def render_sample_part(sample: SampleIdentity) -> str:
    """Render a sample's part: the input slot, then its content hash.

    The DVC content hash (MD5) is the sample's whole identity -- it is
    content-addressed, so identical content produces an identical key even
    when path, size or mtime differ (re-registration, relocation), and an
    edit that preserves all three still moves the key.

    Args:
        sample: The sample's identity and the slot it is read into.

    Returns:
        ``hash:<input_slot>:<content_hash>``.

    Raises:
        ValueError: The identity carries no content hash. There is no
            weaker spelling to fall back to; a row with no hash is a
            malformed record and is refused by
            ``wfc.registration.load_sample_hashes`` before a pipeline
            starts, so reaching here means a caller built an identity
            from an unrefused row.
    """
    if not sample.content_hash:
        raise ValueError(
            f"input slot '{sample.input_slot}' reads a sample whose identity "
            f"carries no content hash. A sample is keyed on its content and "
            f"has no weaker identity; re-register it with "
            f"`wfc register-sample --name <name> --source <path>`."
        )
    return f"hash:{sample.input_slot}:{sample.content_hash}"


def render_input_parts(
    upstream_runs: Iterable[UpstreamRunIdentity],
    samples: Iterable[SampleIdentity],
) -> list[str]:
    """Render every input identity as its part, in the order handed in.

    Args:
        upstream_runs: The upstream runs' identities.
        samples: The samples' identities.

    Returns:
        One part per identity; the digest sorts them, so this order is
        immaterial to the fingerprint.
    """
    return [render_run_part(run) for run in upstream_runs] + [
        render_sample_part(sample) for sample in samples
    ]


def digest_input_parts(parts: Iterable[str]) -> str:
    """Digest a list of parts: sort, comma-join, SHA256.

    ``sorted()`` is **load-bearing**, not an optimisation -- DB row order
    is not guaranteed and will silently break cache key stability.

    Args:
        parts: The rendered parts, in any order.

    Returns:
        64-char hex SHA256 string; the digest of the empty string for an
        empty list.
    """
    return hashlib.sha256(",".join(sorted(parts)).encode()).hexdigest()


@task(purpose="Build a SHA256 input fingerprint from the resolved, slot-qualified "
              "identities of a run's upstream runs and root samples: render each as "
              "its part, sort, comma-join, hash")
def build_input_fingerprint(
    upstream_runs: Sequence[UpstreamRunIdentity],
    samples: Sequence[SampleIdentity] = (),
) -> str:
    """Build a SHA256 fingerprint of all inputs to a run, from their identities.

    Uses upstream ``Run.cache_key`` (deterministic cache key chain) instead of
    ``RunOutput.content_hash``.  This decouples cache validity from archival
    state -- a NULL content_hash (deferred archiving) has no effect on
    fingerprint computation.

    The cache key chain is deterministic: each node's cache_key is
    SHA256(code_fingerprint + params + upstream_cache_keys + env).  Using
    upstream cache_key here closes the chain.

    Handles both upstream runs (downstream nodes) and samples (root nodes)
    in one call -- the caller never branches. The caller resolves ids to
    identities, refuses a row that is not found, and resolves an entry that
    names no source slot; a pure function of the values handed in.

    Each identity carries the input slot it feeds, so rewiring the same
    values into different slots moves the fingerprint.

    Args:
        upstream_runs: Identities of the upstream runs. Empty for root nodes.
        samples: Identities of the root samples. Root nodes only.

    Returns:
        64-char hex SHA256 string.
    """
    return digest_input_parts(render_input_parts(upstream_runs, samples))
