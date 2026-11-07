"""Contracts unit: the method-facing ``WFC_*`` vocabulary.

The names a method script may read from its environment are one constant
in Contracts (``WFC_ENV_VARS``); dispatch assembles their values and
forwards those names into the container as ``-e`` flags, with
``PYTHONUNBUFFERED`` and, where they apply, ``PATH`` and
``PYTHONDONTWRITEBYTECODE``. ``WFC_INPUT_PATHS`` is present only when the
node has inputs, and nothing else from the host environment --
``PYTHONPATH`` in particular -- crosses the barrier.

Catalog: ``wfc-vocabulary``.
"""
from __future__ import annotations

from axiom_annotations import workflow

from tests.harness import Phase, Scenario, node, run_target, selector, wire
from wfc.contracts import WFC_ENV_VARS
from wfc.execution.container_vars import DEFAULT_CONTAINER_PATH


def _forwarded_names(cmd: list[str]) -> set[str]:
    """The env-var names a ``docker run`` argv forwards through ``-e``."""
    return {cmd[i + 1].split("=", 1)[0]
            for i in range(len(cmd) - 1) if cmd[i] == "-e"}


@workflow(purpose="The names run_dispatch forwards into the container as -e "
                  "flags are exactly what the container-variables builder "
                  "sends: the Contracts WFC_* vocabulary (WFC_INPUT_PATHS "
                  "included because the node has inputs), PYTHONUNBUFFERED, "
                  "and PATH / PYTHONDONTWRITEBYTECODE where they apply; "
                  "never PYTHONPATH or any other host variable",
          inputs="A selector-rooted method node with an input wire, run "
                 "through the dispatch phase",
          outputs="The forwarded name set holds the vocabulary and "
                  "PYTHONUNBUFFERED and nothing beyond the two conditional "
                  "names",
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
    cmd = obs.dispatch_cmd(target)
    names = _forwarded_names(cmd)

    # Always sent: the whole WFC_* vocabulary (this node has inputs and a
    # contract, so WFC_INPUT_PATHS and WFC_DECLARED_NAMES too) and
    # PYTHONUNBUFFERED. Sent only where they apply: PATH (an env image with
    # a known bin) and PYTHONDONTWRITEBYTECODE (a code copy bound
    # read-write). Any other name is a host variable leaking through.
    always = set(WFC_ENV_VARS) | {"PYTHONUNBUFFERED"}
    where_they_apply = {"PATH", "PYTHONDONTWRITEBYTECODE"}
    assert always <= names, f"missing: {sorted(always - names)}"
    assert names <= always | where_they_apply, (
        f"host variables crossed the barrier: "
        f"{sorted(names - always - where_they_apply)}"
    )
    assert "PYTHONPATH" not in names
    values = {cmd[i + 1].split("=", 1)[0]: cmd[i + 1].split("=", 1)[1]
              for i in range(len(cmd) - 1) if cmd[i] == "-e"}
    if "PATH" in values:
        assert values["PATH"].endswith(DEFAULT_CONTAINER_PATH), (
            "PATH is the env's bin before the container default, not the host's"
        )
