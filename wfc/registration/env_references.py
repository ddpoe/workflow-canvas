"""The methods that name an env: the reference query behind ``wfc delete-env``'s warning."""

from __future__ import annotations

import logging

from sqlmodel import col, select

from ..contracts import parse_env_spec
from ..persistence import Method, Module, get_session

logger = logging.getLogger(__name__)


def methods_referencing_env(env_name: str) -> list[str]:
    """Return ``module.name/method.name`` for every Method whose env names *env_name*.

    Stored rows are read through the env-spec grammar's read side, so a row
    stored under the bare name and one stored under the legacy prefixed
    spelling both count; a row whose value is not a name is skipped (logged
    at debug level).

    Args:
        env_name: The bare env name.

    Returns:
        The ``module/method`` references, in row order.

    Raises:
        Whatever the session raises when the registry cannot be read; the
        ``delete-env`` caller treats that as no references.
    """
    references: list[str] = []
    with get_session() as session:
        rows = session.exec(
            select(Method, Module).join(Module, col(Method.module_id) == col(Module.id))
        ).all()
        for method, module in rows:
            try:
                stored = parse_env_spec(method.env)
            except ValueError as exc:
                logger.debug(
                    "delete-env reference query skips %s/%s: stored env %r is not an env spec (%s)",
                    module.name, method.name, method.env, exc,
                )
                continue
            if stored == env_name:
                references.append(f"{module.name}/{method.name}")
    return references
