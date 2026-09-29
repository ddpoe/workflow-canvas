"""The ``wfc.run()`` entrypoint.

Resolves exactly one ``@wfc.method``-decorated function from the module
registry, builds a :class:`RunContext`, calls the function with ``ctx``,
and finalizes (writes the ``_wfc_results.json`` manifest). There is no
return-value parsing — the function's return value is ignored.

The standard library plus one small dependency: ``axiom-annotations``,
whose markers narrate the four steps below.
"""

from __future__ import annotations

from axiom_annotations import Step, task

from .context import RunContext
from .decorator import _registry


@task(purpose="Run the module's one @wfc.method function and write its results manifest")
def run() -> None:
    """Run the single ``@wfc.method``-decorated function in this module.

    Resolves exactly one decorated function. Builds a ``RunContext`` from
    the ``WFC_*`` env vars, calls ``func(ctx)``, then writes the results
    manifest. The function's return value is ignored; all outputs flow
    through ``ctx.save_artifact`` and all metrics through ``ctx.log_metric``.

    Raises:
        RuntimeError: If zero or more than one ``@wfc.method`` function is
            registered in this module.
    """
    口 = Step(step_num=1, name="Find the method function",
             purpose="Require exactly one @wfc.method in the module registry")

    if len(_registry) == 0:
        raise RuntimeError(
            "wfc.run() found no @wfc.method function. Decorate exactly one "
            "function with @wfc.method before the "
            "if __name__ == '__main__': block."
        )
    if len(_registry) > 1:
        names = ", ".join(getattr(f, "__name__", repr(f)) for f in _registry)
        raise RuntimeError(
            f"wfc.run() found {len(_registry)} @wfc.method functions ({names}); "
            f"exactly one is required per method module. Keep one entry point "
            f"and move helpers into undecorated functions."
        )

    func = _registry[0]

    口 = Step(step_num=2, name="Build the run context",
             purpose="Read the WFC_* env vars into a RunContext")

    ctx = RunContext()

    口 = Step(step_num=3, name="Call the method",
             purpose="Hand the context to the user's function; its return value is ignored")

    func(ctx)

    口 = Step(step_num=4, name="Write the results manifest",
             purpose="Write _wfc_results.json with the recorded outputs and metrics")

    ctx._finalize()
