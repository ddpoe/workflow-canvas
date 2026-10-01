"""The ``@wfc.method`` marker decorator.

Pure stdlib; no wfc / pandas imports.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

# Module-level registry of @method-decorated functions for this method module.
# ``run()`` resolves exactly one entry; zero or more than one is an error.
_registry: list[Callable[..., Any]] = []


def method(func: F) -> F:
    """Mark a function as the method script's entry point.

    Decorate exactly one function per script. :func:`wfc_client.run`
    calls it with a :class:`~wfc_client.RunContext` as its only argument.
    The function records each output file with
    :meth:`~wfc_client.RunContext.save_artifact` and each metric with
    :meth:`~wfc_client.RunContext.log_metric`. Its return value is ignored.

    Example::

        import wfc_client as wfc

        @wfc.method
        def qc(ctx):
            clean_path = ctx.workdir / "clean.csv"
            ...  # write the file
            ctx.save_artifact("clean", clean_path)
            ctx.log_metric("kept_rows", 100)

        if __name__ == "__main__":
            wfc.run()

    Args:
        func: The function to mark. It takes one argument, the run context.

    Returns:
        The same function, unchanged.
    """
    func._wfc_method = True  # type: ignore[attr-defined]
    _registry.append(func)
    return func
