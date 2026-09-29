"""The sample-hash read: each named registered sample's content hash, for the composer."""

from __future__ import annotations

from sqlmodel import select

from ..persistence import Sample


class MalformedSampleError(ValueError):
    """A named sample is registered but its row carries no content hash.

    A ``ValueError`` subclass, so the refusal keeps the shape every caller
    already handles, and a named type, so a verb can print it as a message
    instead of letting it escape as a traceback.  ``wfc run-pipeline``
    catches it beside
    :class:`~wfc.registration.sample_health.UnreachableSampleError`: both
    are "the pipeline was not started, here is what to repair", and the
    whole point of a pipeline-start refusal is that the user reads one
    message rather than digging it out of a stack.

    Attributes:
        sample: The offending sample's name.
    """

    def __init__(self, sample: str, message: str):
        """Record the offending sample name alongside the message.

        Args:
            sample: The offending sample's name.
            message: The refusal text.
        """
        self.sample = sample
        super().__init__(message)


def load_sample_hashes(samples: list[str], session) -> dict[str, str]:
    """The content hash of each named sample that is registered with one.

    Execution's composer reads this table in its session and hands it to
    the emitter, whose restore rule covers only the samples it can
    materialize.

    This is the earliest moment that sees the whole sample set, so it is
    where a **malformed record** is refused: a registered sample whose row
    carries no content hash. A sample is keyed on its content and restored
    from the cache by its content hash, so a hashless row can neither key a
    run nor be materialized. There is no conversion and no weaker fallback
    -- the refusal names the sample and the one command that fixes it, and
    the sample is re-registered.

    Args:
        samples: Sample names, in the pipeline's order.
        session: An open database session.

    Returns:
        Sample name to content hash, in the given order. A sample with no
        row is absent -- an unregistered name is not this function's
        refusal (the claim refuses it when a step actually reads it).

    Raises:
        MalformedSampleError: A named sample is registered but its row
            carries no content hash.  A ``ValueError``, so callers that
            catch that keep working; typed so ``wfc run-pipeline`` can
            print it as a message rather than a traceback.
    """
    hashes: dict[str, str] = {}
    for name in samples:
        row = session.exec(select(Sample).where(Sample.name == name)).first()
        if row is None:
            continue
        if not row.content_hash:
            raise MalformedSampleError(
                name,
                f"Sample '{name}' is a malformed record: its row carries no "
                f"content hash, so wfc cannot key a run on its content and "
                f"cannot restore it from the cache. Re-register it:\n"
                f"  wfc register-sample --name {name} --source <path>"
            )
        hashes[name] = row.content_hash
    return hashes
