"""Contracts unit: the method-facing ``WFC_*`` vocabulary.

The names a method script may read from its environment are one constant
in Contracts (``WFC_ENV_VARS``); dispatch assembles their values and
forwards exactly those names into the container as ``-e`` flags.
``WFC_INPUT_PATHS`` is present only when the node has inputs, and nothing
else from the host environment -- ``PYTHONPATH`` in particular -- crosses
the barrier.

Catalog: ``wfc-vocabulary``.
"""
from __future__ import annotations

from axiom_annotations import workflow

from tests.harness import Phase, Scenario, node, run_target, selector, wire
from wfc.contracts import WFC_ENV_VARS


def _forwarded_names(cmd: list[str]) -> set[str]:
    """The env-var names a ``docker run`` argv forwards through ``-e``."""
    return {cmd[i + 1].split("=", 1)[0]
            for i in range(len(cmd) - 1) if cmd[i] == "-e"}


@workflow(purpose="The names run_dispatch forwards into the container as -e "
                  "flags are exactly the Contracts WFC_* vocabulary, "
                  "WFC_INPUT_PATHS included because the node has inputs, "
                  "and never PYTHONPATH",
          inputs="A selector-rooted method node with an input wire, run "
                 "through the dispatch phase",
          outputs="The forwarded name set equals the constant as a set",
          critical="Every node the engine dispatches has inputs: the "
                   "materialize phase refuses a root method node with no "
                   "input data before dispatch runs, so the "
                   "WFC_INPUT_PATHS-absent branch is not reachable through "
                   "the engine and is not asserted here")
def test_dispatch_forwards_exactly_the_wfc_vocabulary(git_project, monkeypatch):
    """One dispatch argv, its ``-e`` names against the constant."""
    scn = Scenario(nodes=[selector(), node("m", module="test", inputs=[wire("sel")])],
                   samples=["s1"])
    obs = run_target(scn, "m", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.DISPATCH)

    target = ("m", "s1", "default")
    assert obs.runs[target].dispatch_cmd is not None, (
        f"dispatch did not reach the subprocess (rc={obs.exit_code(target)})"
    )
    names = _forwarded_names(obs.dispatch_cmd(target))

    assert names == set(WFC_ENV_VARS)
    assert "WFC_INPUT_PATHS" in names
    assert "PYTHONPATH" not in names
