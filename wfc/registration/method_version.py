"""The method-version lookup: one MethodVersion row per (method, code fingerprint)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from axiom_annotations import task

from ..persistence import get_session, MethodVersion


@task(purpose="Return the MethodVersion id for a method's code fingerprint, "
              "creating the row if needed; git_commit is audit metadata on it, "
              "not part of the cache key",
      inputs="method id, code fingerprint, optional git commit",
      outputs="MethodVersion.id")
def get_or_create_version(method_id: int, code_fingerprint: str, git_commit: str | None = None) -> int:
    """Return MethodVersion.id for (method_id, code_fingerprint), creating if needed.

    The DB UniqueConstraint on (method_id, code_fingerprint) is the safety net;
    this function provides the upsert logic on top.  Concurrent callers (e.g. 4+
    parallel Snakemake workers sharing the same method/fingerprint) will race on
    the INSERT -- the loser catches IntegrityError and re-SELECTs the winning row.

    Args:
        method_id: Database ID of the Method row.
        code_fingerprint: 64-char SHA256 hex digest from
            ``wfc.identity.build_code_fingerprint()``.
        git_commit: Optional 40-char git commit SHA for audit metadata.  Stored
            on the MethodVersion row but not used for identity or cache keys.

    Returns:
        MethodVersion.id (integer).
    """
    # Fast path: row already exists (common case after first worker wins).
    with get_session() as session:
        existing = session.exec(
            select(MethodVersion).where(
                MethodVersion.method_id == method_id,
                MethodVersion.code_fingerprint == code_fingerprint,
            )
        ).first()
        if existing is not None:
            return existing.id  # type: ignore[return-value]

    # Slow path: try to INSERT, fall back to SELECT if another worker beat us.
    try:
        with get_session() as session:
            version = MethodVersion(
                method_id=method_id,
                code_fingerprint=code_fingerprint,
                git_commit=git_commit,
                recorded_at=datetime.now(timezone.utc),
            )
            session.add(version)
            session.commit()
            session.refresh(version)
            return version.id  # type: ignore[return-value]
    except IntegrityError:
        # Another concurrent worker inserted the same (method_id, code_fingerprint)
        # first — retrieve their row.
        with get_session() as session:
            existing = session.exec(
                select(MethodVersion).where(
                    MethodVersion.method_id == method_id,
                    MethodVersion.code_fingerprint == code_fingerprint,
                )
            ).first()
            if existing is None:  # pragma: no cover — should be impossible
                raise RuntimeError(
                    f"get_or_create_version: INSERT failed with IntegrityError "
                    f"but follow-up SELECT found nothing for "
                    f"method_id={method_id}, code_fingerprint={code_fingerprint!r}"
                )
            return existing.id  # type: ignore[return-value]
