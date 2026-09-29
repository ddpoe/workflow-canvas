"""Spies: patch sites that wrap the real callable and call through.

Nothing here replaces behaviour; each entry records what a production
function was called with, or observes state mid-call, and delegates. A spy
proves nothing less than the unpatched call would, which is why its
``backed_by`` is the harness witness that runs with the spy installed.
"""

from __future__ import annotations

import pathlib

from ._entry import spy


@spy(
    boundary="wfc.execution.run_step's five phase entry points, rebound to "
             "record-then-delegate wrappers (tests.harness.drivers._interpose)",
    preserves="everything: production sequences the phases, the wrapper "
              "records each phase reached and its arguments, and raises the "
              "harness's private stop signal only past the stopping point",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_harness_smoke::"
              "test_no_argument_scenario_is_a_valid_one_node_run",
)
def interpose_phases(orchestrator, record, through, monkeypatch):
    """Wrap every phase entry point on the orchestrator module.

    Delegates to the harness's own context manager; the harness keeps the
    body because the wrappers close over its ``TargetRun`` record.

    Args:
        orchestrator: The ``wfc.execution.run_step`` module.
        record: The target's execution record.
        through: The stopping point, or ``None``.
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The context manager.
    """
    from tests.harness.drivers import _interpose

    return _interpose(orchestrator, record, through, monkeypatch)


@spy(
    boundary="wfc.execution.record.complete_run, wrapped to snapshot the "
             "RunOutput rows either side of the call "
             "(tests.harness.drivers._snapshot_output_writers)",
    preserves="everything: the real writer runs; the rows before and after "
              "are read for the invariant pack",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_harness_smoke::"
              "test_no_argument_scenario_is_a_valid_one_node_run",
)
def snapshot_output_writers(record, monkeypatch):
    """Snapshot the output rows either side of the second writer's call.

    Args:
        record: The target's execution record.
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The context manager.
    """
    from tests.harness.drivers import _snapshot_output_writers

    return _snapshot_output_writers(record, monkeypatch)


@spy(
    boundary="wfc.canvas.wfc_provider.Session -- the session class the "
             "provider's reload opens",
    preserves="everything: the subclass observes the provider's state on "
              "entry and delegates to the real session",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_wfc_provider_atomic_reload::"
              "test_readers_mid_reload_see_previous_complete_state",
)
def probing_provider_session(monkeypatch, session_cls) -> None:
    """Bind a ``Session`` subclass the provider's reload opens.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        session_cls: A ``sqlmodel.Session`` subclass whose ``__enter__``
            records what a concurrent reader would see, then delegates.
    """
    monkeypatch.setattr("wfc.canvas.wfc_provider.Session", session_cls)


@spy(
    boundary="pathlib.Path.iterdir -- every directory listing during a call",
    preserves="everything: each listing proceeds; the paths listed are "
              "recorded",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_fan_in_single_selector::"
              "test_generate_snakefile_collapsed_fanin_no_filesystem_inspection",
)
def spy_directory_listings(monkeypatch) -> list[str]:
    """Record every directory the code under test lists.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The list the recorded paths accumulate into.
    """
    listed: list[str] = []
    original_iterdir = pathlib.Path.iterdir

    def recording_iterdir(self):
        listed.append(str(self))
        return original_iterdir(self)

    monkeypatch.setattr(pathlib.Path, "iterdir", recording_iterdir)
    return listed
