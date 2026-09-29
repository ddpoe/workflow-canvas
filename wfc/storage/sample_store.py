"""The sample store: a registered sample's bytes into the cache, with their first push."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from axiom_annotations import task

from ..identity import hash_path
from ..persistence import PushStatus
from .cache import cache_file
from .push_worker import first_push_status


class StoredSample(NamedTuple):
    """What the sample store hands back for the sample row.

    Attributes:
        content_hash: The sample's content hash.
        push_status: Its first push status, or the outcome of a standalone push.
        pushed_at: When a standalone push landed, else None.
        push_error: The standalone push's error, else None.
    """

    content_hash: str
    push_status: PushStatus
    pushed_at: datetime | None
    push_error: str | None


@task(
    purpose="Store a sample's bytes in the cache (copy mode) and settle its first push",
    inputs="the user's source path, left in place; the project root",
    outputs="the content hash and the push fields for the sample row",
)
def store_sample_bytes(source_path: Path, project_root: Path) -> StoredSample:
    """Hash a sample, cache it in copy mode, and settle its first push.

    Inside a pipeline run (``WFC_PIPELINE_ID`` set) a pending sample is left
    to the push worker.  A standalone registration pushes it at once and
    lands pushed or failed, so the user has immediate feedback.

    Args:
        source_path: The user's source file (never moved).
        project_root: Root directory of the wfc project.

    Returns:
        The content hash and the push fields.
    """
    # Content-hash the source and store in DVC cache
    content_hash = hash_path(source_path)
    # The user owns the source: copy mode leaves it in place.
    cache_file(source_path, content_hash, project_root, move=False)

    # In-pipeline registrations enqueue onto the push worker
    # (WFC_PIPELINE_ID env set by run_pipeline).  Standalone CLI mode does a
    # one-shot synchronous push so the user has immediate feedback.
    in_pipeline = bool(os.environ.get("WFC_PIPELINE_ID"))
    push_status = first_push_status(project_root)
    pushed_at = None
    push_error = None

    if push_status == PushStatus.pending and not in_pipeline:
        # Standalone: synchronous push, mark terminal state.  The entry's
        # own outcome decides it -- a reported failure is a failure, the
        # same rule the push worker applies.
        from . import transport
        try:
            result = transport.push([content_hash], project_root)
            push_error = _entry_error(result, content_hash)
        except Exception as exc:  # noqa: BLE001 -- the transport itself failed
            push_error = str(exc) or type(exc).__name__
        if push_error is None:
            push_status = PushStatus.pushed
            pushed_at = datetime.now(timezone.utc)
        else:
            push_status = PushStatus.failed
            print(
                f"WARNING: DVC push failed ({push_error}); sample is in local cache only.",
                file=sys.stderr,
            )

    return StoredSample(content_hash, push_status, pushed_at, push_error)


def _entry_error(result, content_hash: str) -> str | None:
    """The pushed entry's own error from a transport outcome, or None."""
    errors = getattr(result, "errors", None) or {}
    if content_hash in errors:
        return errors[content_hash]
    for obj in getattr(result, "failed", None) or ():
        value = obj if isinstance(obj, str) else getattr(obj, "value", None)
        if value == content_hash:
            return "DVC reported the entry failed."
    return None
