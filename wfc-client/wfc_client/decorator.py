"""The ``@wfc.method`` marker decorator.

Pure stdlib; no wfc / pandas imports.
"""

from __future__ import annotations

from typing import Callable

# Module-level registry of @method-decorated functions for this method module.
# ``run()`` resolves exactly one entry; zero or more than one is an error.
_registry: "list[Callable]" = []


def method(func: Callable) -> Callable:
    """Mark a function as the wfc method entry point.

    The decorated function takes a single ``ctx`` argument (a
    :class:`wfc_client.context.RunContext`). It produces outputs by
    calling ``ctx.save_artifact(name, path)`` and metrics via
    ``ctx.log_metric(name, value)``. Its return value is ignored — there
    is no return-value parsing.

    Decorating is a no-op at import time beyond registration; dispatch
    happens when :func:`wfc_client.main.run` is called.

    Args:
        func: The method function to decorate. Should accept ``(ctx)``.

    Returns:
        The original function, unchanged, with ``_wfc_method = True`` set.
    """
    func._wfc_method = True  # type: ignore[attr-defined]
    _registry.append(func)
    return func
